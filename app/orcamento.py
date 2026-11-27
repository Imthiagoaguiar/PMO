"""
Contas do orçamento do projeto: itens (orçado) x lançamentos (realizado), sempre no banco (SUM/GROUP BY).

- Saldo e execução por item, categoria (CAPEX/OPEX) e pilar.
- Lançamentos REALIZADOS (o que já foi gasto) x PROJETADOS (gastos futuros previstos): os projetados nunca
  entram no realizado; a previsão final é realizado + projetado.
- Curva mês a mês: realizado, planejado (os valores por mês dos itens; itens sem meses têm o orçado distribuído
  por igual entre o início e o fim do cronograma) e o previsto (realizado + projetado).
- Referência: custo ao final no ritmo atual (EAC) = realizado / avanço físico do cronograma.
- Alertas: itens acima do aprovado, estouros (com a justificativa), previsão acima do orçado, gasto adiantado.
"""

import calendar
from collections import defaultdict
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services, tempo
from app.models import CategoriaOrcamento, ItemOrcamento, LancamentoOrcamento, OrcamentoMensal, Pilar, Projeto
from app.schemas import (
    AlertaOrcamento,
    ItemOrcamentoRead,
    MesOrcamento,
    PontoCurva,
    ResumoOrcamento,
    TotalOrcamento,
)

CENTAVO = Decimal("0.01")
ZERO = Decimal("0")
MAX_MESES_CURVA = 120
# Abaixo disso, "realizado ÷ avanço" não é previsão: com 1% feito, qualquer gasto vira 100 vezes.
AVANCO_MINIMO_PREVISAO = 10.0
# Lançamento mais de 1 ano antes do início ou depois do fim: provável erro de digitação (2062 em vez de 2026).
FOLGA_PERIODO_DIAS = 365
NOME_CATEGORIA = {CategoriaOrcamento.CAPEX: "CAPEX", CategoriaOrcamento.OPEX: "OPEX"}


def reais(valor: Decimal) -> str:
    """Decimal('1234.5') -> 'R$ 1.234,50' (para as mensagens dos alertas)."""
    sinal = "-" if valor < 0 else ""
    inteiro, centavos = f"{abs(valor):,.2f}".split(".")
    return f"{sinal}R$ {inteiro.replace(',', '.')},{centavos}"


def gastos_por_item(db: Session, projeto_id: int) -> Dict[int, Tuple[Decimal, Decimal, int]]:
    """{item_id: (realizado, projetado, quantidade de lançamentos)} numa consulta só."""
    gastos: Dict[int, Tuple[Decimal, Decimal, int]] = {}
    for item_id, projetado, total, quantidade in db.execute(
        select(LancamentoOrcamento.item_id, LancamentoOrcamento.projetado, func.sum(LancamentoOrcamento.valor), func.count())
        .where(LancamentoOrcamento.projeto_id == projeto_id)
        .group_by(LancamentoOrcamento.item_id, LancamentoOrcamento.projetado)
    ):
        realizado, futuro, n = gastos.get(item_id, (ZERO, ZERO, 0))
        if projetado:
            futuro += Decimal(total or 0)
        else:
            realizado += Decimal(total or 0)
        gastos[item_id] = (realizado, futuro, n + int(quantidade))
    return gastos


def realizado_por_item(db: Session, projeto_id: int) -> Dict[int, Tuple[Decimal, int]]:
    """{item_id: (soma dos lançamentos REALIZADOS, quantidade de lançamentos)}: projetados não contam como gasto."""
    return {item_id: (realizado, n) for item_id, (realizado, _, n) in gastos_por_item(db, projeto_id).items()}


def realizado_do_item(db: Session, item_id: int) -> Decimal:
    return Decimal(db.scalar(
        select(func.coalesce(func.sum(LancamentoOrcamento.valor), 0))
        .where(LancamentoOrcamento.item_id == item_id, LancamentoOrcamento.projetado.is_(False))
    ) or 0)


def mes_iso(mes: date) -> str:
    return f"{mes.year}-{mes.month:02d}"


def financeiro_por_pilar(db: Session, projeto_id: int) -> Dict[int, Tuple[Decimal, Decimal]]:
    """{pilar_id: (orçado, realizado)} dos pilares que têm itens no orçamento."""
    realizado = realizado_por_item(db, projeto_id)
    por_pilar: Dict[int, List[Decimal]] = {}
    for item_id, pilar_id, orcado in db.execute(
        select(ItemOrcamento.id, ItemOrcamento.pilar_id, ItemOrcamento.valor_orcado)
        .where(ItemOrcamento.projeto_id == projeto_id, ItemOrcamento.pilar_id.is_not(None))
    ):
        soma = por_pilar.setdefault(pilar_id, [ZERO, ZERO])
        soma[0] += Decimal(orcado)
        soma[1] += realizado.get(item_id, (ZERO, 0))[0]
    return {p: (o, r) for p, (o, r) in por_pilar.items()}


def _percentual(parte: Decimal, todo: Decimal) -> Optional[float]:
    return round(float(parte) / float(todo) * 100, 1) if todo else None


def ler_itens(db: Session, projeto: Projeto, ids: Optional[List[int]] = None) -> List[ItemOrcamentoRead]:
    gastos = gastos_por_item(db, projeto.id)
    meses: Dict[int, List[MesOrcamento]] = defaultdict(list)
    for item_id, mes, valor in db.execute(
        select(OrcamentoMensal.item_id, OrcamentoMensal.mes, OrcamentoMensal.valor)
        .join(ItemOrcamento, ItemOrcamento.id == OrcamentoMensal.item_id)
        .where(ItemOrcamento.projeto_id == projeto.id).order_by(OrcamentoMensal.mes)
    ):
        meses[item_id].append(MesOrcamento(mes=mes_iso(mes), valor=Decimal(valor)))
    justificativas: Dict[int, str] = {}
    for item_id, texto in db.execute(
        select(LancamentoOrcamento.item_id, LancamentoOrcamento.justificativa)
        .where(LancamentoOrcamento.projeto_id == projeto.id, LancamentoOrcamento.justificativa.is_not(None),
               LancamentoOrcamento.projetado.is_(False))
        .order_by(LancamentoOrcamento.id.desc())
    ):
        justificativas.setdefault(item_id, texto)
    consulta = (
        select(ItemOrcamento, Pilar.nome).outerjoin(Pilar, Pilar.id == ItemOrcamento.pilar_id)
        .where(ItemOrcamento.projeto_id == projeto.id).order_by(ItemOrcamento.ordem, ItemOrcamento.id)
    )
    if ids is not None:
        consulta = consulta.where(ItemOrcamento.id.in_(ids))
    itens = []
    for item, pilar_nome in db.execute(consulta):
        gasto, futuro, quantidade = gastos.get(item.id, (ZERO, ZERO, 0))
        orcado = Decimal(item.valor_orcado)
        itens.append(ItemOrcamentoRead(
            id=item.id, descricao=item.descricao, categoria=item.categoria, pilar_id=item.pilar_id,
            pilar_nome=pilar_nome, valor_orcado=orcado, valor_realizado=gasto, valor_projetado=futuro,
            previsao=gasto + futuro, saldo=orcado - gasto, execucao=_percentual(gasto, orcado), estourado=gasto > orcado,
            justificativa=justificativas.get(item.id) if gasto > orcado else None, meses=meses.get(item.id, []),
            lancamentos=quantidade, versao=item.versao,
        ))
    return itens


def _meses(inicio: date, fim: date) -> List[Tuple[int, int]]:
    meses, ano, mes = [], inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month) and len(meses) < MAX_MESES_CURVA:
        meses.append((ano, mes))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


def janela_valida(periodo: Tuple[Optional[date], Optional[date]], hoje: date) -> Tuple[date, date]:
    """Datas de lançamento plausíveis: o período do projeto (ou hoje) com 1 ano de folga para cada lado."""
    inicio, fim = periodo
    folga = timedelta(days=FOLGA_PERIODO_DIAS)
    return (inicio or hoje) - folga, max(fim or hoje, hoje) + folga


def _curva(db: Session, projeto: Projeto, itens: List[ItemOrcamentoRead],
           periodo: Tuple[Optional[date], Optional[date]], hoje: date) -> Tuple[List[PontoCurva], List[date]]:
    """(curva, datas de lançamento fora do período plausível — que ficam fora da curva e viram alerta)."""
    de, ate = janela_valida(periodo, hoje)
    realizado_mes: Dict[Tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    projetado_mes: Dict[Tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    datas: List[date] = []
    fora: List[date] = []
    for dia, projetado, soma in db.execute(
        select(LancamentoOrcamento.data, LancamentoOrcamento.projetado, func.sum(LancamentoOrcamento.valor))
        .where(LancamentoOrcamento.projeto_id == projeto.id)
        .group_by(LancamentoOrcamento.data, LancamentoOrcamento.projetado)
    ):
        if not de <= dia <= ate:
            fora.append(dia)
            continue
        (projetado_mes if projetado else realizado_mes)[(dia.year, dia.month)] += Decimal(soma)
        datas.append(dia)

    # Planejado: os meses dos itens + os itens sem meses distribuídos por igual no período do cronograma.
    plano_mes: Dict[Tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    for item in itens:
        for m in item.meses:
            plano_mes[(int(m.mes[:4]), int(m.mes[5:]))] += m.valor
    linear = sum((i.valor_orcado for i in itens if not i.meses), ZERO)
    inicio, fim = periodo
    periodo_ok = inicio is not None and fim is not None and fim >= inicio
    com_linear = linear > 0 and periodo_ok
    # Itens sem meses e sem período: o planejado ficaria incompleto (e enganaria), então não aparece.
    com_plano = (bool(plano_mes) or com_linear) and not (linear > 0 and not periodo_ok)

    limites = datas + ([inicio, fim] if com_linear else []) + ([date(a, m, 1) for a, m in plano_mes] if com_plano else [])
    if not limites:
        return [], fora
    duracao = (fim - inicio).days + 1 if com_linear else 0
    com_projetado = bool(projetado_mes)

    pontos: List[PontoCurva] = []
    acumulado = futuro = plano_meses = ZERO
    planejado_anterior = ZERO
    for ano, mes in _meses(min(limites), max(limites)):
        no_mes = realizado_mes.get((ano, mes), ZERO)
        projetado_no_mes = projetado_mes.get((ano, mes), ZERO)
        acumulado += no_mes
        futuro += projetado_no_mes
        planejado = planejado_no_mes = None
        if com_plano:
            plano_meses += plano_mes.get((ano, mes), ZERO)
            distribuido = ZERO
            if com_linear:
                ultimo_dia = date(ano, mes, calendar.monthrange(ano, mes)[1])
                decorridos = min(max((ultimo_dia - inicio).days + 1, 0), duracao)
                distribuido = linear * decorridos / duracao
            planejado = (plano_meses + distribuido).quantize(CENTAVO, ROUND_HALF_UP)
            planejado_no_mes, planejado_anterior = planejado - planejado_anterior, planejado
        pontos.append(PontoCurva(
            mes=f"{ano}-{mes:02d}", realizado_no_mes=no_mes, realizado_acumulado=acumulado,
            planejado_no_mes=planejado_no_mes, planejado_acumulado=planejado, projetado_no_mes=projetado_no_mes,
            previsto_acumulado=acumulado + futuro if com_projetado else None,
        ))
    return pontos, fora


def resumo(db: Session, projeto: Projeto, hoje: Optional[date] = None) -> ResumoOrcamento:
    hoje = hoje or tempo.hoje()
    itens = ler_itens(db, projeto)
    total_orcado = sum((i.valor_orcado for i in itens), ZERO)
    total_realizado = sum((i.valor_realizado for i in itens), ZERO)
    total_projetado = sum((i.valor_projetado for i in itens), ZERO)
    previsao = total_realizado + total_projetado
    indicadores = services.calcular_indicadores(db, projeto, hoje)
    avanco = indicadores.percentual_conclusao
    ritmo = (
        (total_realizado / Decimal(str(avanco)) * 100).quantize(CENTAVO, ROUND_HALF_UP)
        if avanco >= AVANCO_MINIMO_PREVISAO and total_realizado > 0 else None
    )

    por_categoria = [
        TotalOrcamento(chave=c.value, nome=NOME_CATEGORIA[c],
                       orcado=sum((i.valor_orcado for i in itens if i.categoria == c), ZERO),
                       realizado=sum((i.valor_realizado for i in itens if i.categoria == c), ZERO),
                       projetado=sum((i.valor_projetado for i in itens if i.categoria == c), ZERO))
        for c in CategoriaOrcamento
    ]
    pilares: Dict[Optional[int], List] = {}
    for i in itens:
        grupo = pilares.setdefault(i.pilar_id, [i.pilar_nome or "Sem pilar", ZERO, ZERO, ZERO])
        grupo[1] += i.valor_orcado
        grupo[2] += i.valor_realizado
        grupo[3] += i.valor_projetado
    por_pilar = [
        TotalOrcamento(chave=str(pid) if pid is not None else "sem_pilar", nome=nome, orcado=o, realizado=r, projetado=pj)
        for pid, (nome, o, r, pj) in sorted(pilares.items(), key=lambda kv: (kv[0] is None, kv[1][0]))
    ]

    periodo = (projeto.data_inicio or indicadores.data_inicio_prevista, indicadores.data_fim_prevista)
    curva, fora_do_periodo = _curva(db, projeto, itens, periodo, hoje)

    alertas: List[AlertaOrcamento] = []
    aprovado = projeto.orcamento_aprovado
    if aprovado is not None and total_orcado > aprovado:
        alertas.append(AlertaOrcamento(nivel="alto", mensagem=(
            f"Os itens somam {reais(total_orcado)}, acima do orçamento aprovado ({reais(aprovado)}).")))
    if total_orcado > 0 and total_realizado > total_orcado:
        alertas.append(AlertaOrcamento(nivel="alto", mensagem=(
            f"O realizado ({reais(total_realizado)}) já passou do orçado ({reais(total_orcado)}).")))
    estourados = [i for i in itens if i.estourado]
    for i in estourados[:5]:
        motivo = f" Justificativa: “{i.justificativa}”" if i.justificativa else " Sem justificativa registrada."
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=(
            f"“{i.descricao}” estourou: {reais(i.valor_realizado)} de {reais(i.valor_orcado)} orçados.{motivo}")))
    if len(estourados) > 5:
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=f"Mais {len(estourados) - 5} item(ns) estourado(s)."))
    if total_projetado and total_orcado > 0 and previsao > total_orcado:
        acima = _percentual(previsao - total_orcado, total_orcado)
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=(
            f"Com os gastos projetados ({reais(total_projetado)}), o projeto deve fechar em {reais(previsao)}, "
            f"{acima:.0f}% acima do orçado.")))
    elif not total_projetado and ritmo is not None and total_orcado > 0 and ritmo > total_orcado * Decimal("1.05"):
        acima = _percentual(ritmo - total_orcado, total_orcado)
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=(
            f"No ritmo atual ({avanco:.0f}% do cronograma feito), o projeto deve fechar em {reais(ritmo)}, "
            f"{acima:.0f}% acima do orçado. Lance os gastos projetados para uma previsão mais fiel.")))
    if fora_do_periodo:
        exemplo = max(fora_do_periodo, key=lambda d: abs((d - hoje).days))
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=(
            f"{len(fora_do_periodo)} data(s) de lançamento fora do período do projeto (ex.: {exemplo:%d/%m/%Y}). "
            "Confira se não é erro de digitação; esses lançamentos contam no total, mas ficam fora do gráfico mensal.")))
    mes_atual = f"{hoje.year}-{hoje.month:02d}"
    agora = next((p for p in curva if p.mes == mes_atual), None)
    if agora and agora.planejado_acumulado and agora.realizado_acumulado > agora.planejado_acumulado * Decimal("1.1"):
        alertas.append(AlertaOrcamento(nivel="info", mensagem=(
            f"Gasto adiantado: {reais(agora.realizado_acumulado)} até este mês, contra {reais(agora.planejado_acumulado)} "
            "previstos no planejado mês a mês.")))

    return ResumoOrcamento(
        orcamento_aprovado=aprovado, total_orcado=total_orcado, total_realizado=total_realizado,
        total_projetado=total_projetado, saldo=total_orcado - total_realizado,
        execucao=_percentual(total_realizado, total_orcado), avanco_fisico=avanco, previsao_final=previsao,
        previsao_ritmo=ritmo, itens=itens, por_categoria=por_categoria, por_pilar=por_pilar, curva=curva, alertas=alertas,
    )


# ---------- Planilha (aba Orçamento e Excel do projeto) ----------

FORMATO_REAIS = '"R$" #,##0.00;-"R$" #,##0.00'


def _reais(aba: Any, valor: Any) -> Any:
    from openpyxl.cell import WriteOnlyCell

    celula = WriteOnlyCell(aba, value=valor)
    celula.number_format = FORMATO_REAIS
    return celula


def _larguras(aba: Any, larguras: List[int]) -> None:
    from openpyxl.utils import get_column_letter

    for i, largura in enumerate(larguras, 1):
        aba.column_dimensions[get_column_letter(i)].width = largura


def escrever_planilha(livro: Any, db: Session, projeto: Projeto) -> None:
    """
    Abas financeiras num livro openpyxl de escrita contínua: resumo (com CAPEX x OPEX), itens, mês a mês,
    orçado por item e mês (se houver) e todos os lançamentos (realizados e projetados, com a justificativa).
    """
    texto, data = services.celula_texto_seguro, services.celula_data
    r = resumo(db, projeto)
    nomes = {i.id: (i.descricao, NOME_CATEGORIA[i.categoria]) for i in r.itens}

    aba = livro.create_sheet("Resumo do orçamento")
    _larguras(aba, [24, 18, 18, 18, 18, 18, 12])
    aba.append(["Indicador", "Valor"])
    for rotulo, valor in (("Orçamento aprovado", r.orcamento_aprovado), ("Orçado (itens)", r.total_orcado),
                          ("Realizado", r.total_realizado), ("Projetado (futuro)", r.total_projetado),
                          ("Previsão final (realizado + projetado)", r.previsao_final), ("Saldo", r.saldo)):
        aba.append([rotulo, _reais(aba, valor)])
    aba.append(["Execução %", r.execucao])
    aba.append(["Avanço do cronograma %", r.avanco_fisico])
    aba.append([])
    aba.append(["Categoria", "Orçado", "Realizado", "Projetado", "Previsão final", "Saldo"])
    for t in [*r.por_categoria, TotalOrcamento(chave="total", nome="Total", orcado=r.total_orcado,
                                               realizado=r.total_realizado, projetado=r.total_projetado)]:
        aba.append([t.nome, _reais(aba, t.orcado), _reais(aba, t.realizado), _reais(aba, t.projetado),
                    _reais(aba, t.realizado + t.projetado), _reais(aba, t.orcado - t.realizado)])
    if r.alertas:
        aba.append([])
        aba.append(["Alertas"])
        for a in r.alertas:
            aba.append([texto(aba, a.mensagem)])

    aba = livro.create_sheet("Itens do orçamento")
    _larguras(aba, [40, 10, 18, 16, 16, 16, 16, 16, 12, 60])
    aba.append(["Item", "Categoria", "Pilar", "Orçado", "Realizado", "Projetado", "Previsão final", "Saldo",
                "Execução %", "Justificativa do estouro"])
    for i in r.itens:
        aba.append([
            texto(aba, i.descricao), NOME_CATEGORIA[i.categoria], texto(aba, i.pilar_nome), _reais(aba, i.valor_orcado),
            _reais(aba, i.valor_realizado), _reais(aba, i.valor_projetado), _reais(aba, i.previsao), _reais(aba, i.saldo),
            i.execucao, texto(aba, i.justificativa),
        ])

    if r.curva:
        aba = livro.create_sheet("Mês a mês")
        _larguras(aba, [10, 18, 18, 18, 18, 18, 18])
        aba.append(["Mês", "Planejado no mês", "Planejado acumulado", "Realizado no mês", "Realizado acumulado",
                    "Projetado no mês", "Previsto acumulado"])
        for p in r.curva:
            aba.append([f"{p.mes[5:]}/{p.mes[:4]}", _reais(aba, p.planejado_no_mes), _reais(aba, p.planejado_acumulado),
                        _reais(aba, p.realizado_no_mes), _reais(aba, p.realizado_acumulado),
                        _reais(aba, p.projetado_no_mes), _reais(aba, p.previsto_acumulado)])

    com_meses = [i for i in r.itens if i.meses]
    if com_meses:
        meses = sorted({m.mes for i in com_meses for m in i.meses})
        aba = livro.create_sheet("Orçado por mês")
        _larguras(aba, [40, 10] + [14] * len(meses) + [16])
        aba.append(["Item", "Categoria", *[f"{m[5:]}/{m[:4]}" for m in meses], "Total"])
        for i in com_meses:
            valores = {m.mes: m.valor for m in i.meses}
            aba.append([texto(aba, i.descricao), NOME_CATEGORIA[i.categoria],
                        *[_reais(aba, valores.get(m)) for m in meses], _reais(aba, i.valor_orcado)])

    aba = livro.create_sheet("Lançamentos")
    _larguras(aba, [12, 12, 40, 10, 40, 18, 16, 60])
    aba.append(["Data", "Tipo", "Item", "Categoria", "Descrição", "Documento", "Valor", "Justificativa"])
    for lanc in db.scalars(
        select(LancamentoOrcamento).where(LancamentoOrcamento.projeto_id == projeto.id)
        .order_by(LancamentoOrcamento.projetado, LancamentoOrcamento.data, LancamentoOrcamento.id)
        .execution_options(yield_per=services.LOTE_EXPORTACAO)
    ):
        nome, categoria = nomes.get(lanc.item_id, (None, None))
        aba.append([
            data(aba, lanc.data), "Projetado" if lanc.projetado else "Realizado", texto(aba, nome), categoria,
            texto(aba, lanc.descricao), texto(aba, lanc.documento), _reais(aba, lanc.valor), texto(aba, lanc.justificativa),
        ])
