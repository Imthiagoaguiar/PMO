"""
Contas do orçamento do projeto: itens (orçado) x lançamentos (realizado), sempre no banco (SUM/GROUP BY).

- Saldo e execução por item, categoria (CAPEX/OPEX) e pilar.
- Curva mês a mês: realizado no mês, realizado acumulado e o planejado acumulado (o orçado distribuído
  por igual entre o início e o fim do cronograma).
- Previsão de fechamento no ritmo atual (EAC): realizado / avanço físico do cronograma.
- Alertas: itens acima do aprovado, estouros, previsão acima do orçado, gasto adiantado.
"""

import calendar
from collections import defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Dict, List, Optional, Tuple

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import services, tempo
from app.models import CategoriaOrcamento, ItemOrcamento, LancamentoOrcamento, Pilar, Projeto
from app.schemas import (
    AlertaOrcamento,
    ItemOrcamentoRead,
    PontoCurva,
    ResumoOrcamento,
    TotalOrcamento,
)

CENTAVO = Decimal("0.01")
ZERO = Decimal("0")
MAX_MESES_CURVA = 120
NOME_CATEGORIA = {CategoriaOrcamento.CAPEX: "CAPEX", CategoriaOrcamento.OPEX: "OPEX"}


def reais(valor: Decimal) -> str:
    """Decimal('1234.5') -> 'R$ 1.234,50' (para as mensagens dos alertas)."""
    sinal = "-" if valor < 0 else ""
    inteiro, centavos = f"{abs(valor):,.2f}".split(".")
    return f"{sinal}R$ {inteiro.replace(',', '.')},{centavos}"


def realizado_por_item(db: Session, projeto_id: int) -> Dict[int, Tuple[Decimal, int]]:
    """{item_id: (soma dos lançamentos, quantidade)} numa consulta só."""
    return {
        item_id: (Decimal(total or 0), int(quantidade))
        for item_id, total, quantidade in db.execute(
            select(LancamentoOrcamento.item_id, func.sum(LancamentoOrcamento.valor), func.count())
            .where(LancamentoOrcamento.projeto_id == projeto_id).group_by(LancamentoOrcamento.item_id)
        )
    }


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
    realizado = realizado_por_item(db, projeto.id)
    consulta = (
        select(ItemOrcamento, Pilar.nome).outerjoin(Pilar, Pilar.id == ItemOrcamento.pilar_id)
        .where(ItemOrcamento.projeto_id == projeto.id).order_by(ItemOrcamento.ordem, ItemOrcamento.id)
    )
    if ids is not None:
        consulta = consulta.where(ItemOrcamento.id.in_(ids))
    itens = []
    for item, pilar_nome in db.execute(consulta):
        gasto, quantidade = realizado.get(item.id, (ZERO, 0))
        orcado = Decimal(item.valor_orcado)
        itens.append(ItemOrcamentoRead(
            id=item.id, descricao=item.descricao, categoria=item.categoria, pilar_id=item.pilar_id,
            pilar_nome=pilar_nome, valor_orcado=orcado, valor_realizado=gasto, saldo=orcado - gasto,
            execucao=_percentual(gasto, orcado), estourado=gasto > orcado, lancamentos=quantidade, versao=item.versao,
        ))
    return itens


def _meses(inicio: date, fim: date) -> List[Tuple[int, int]]:
    meses, ano, mes = [], inicio.year, inicio.month
    while (ano, mes) <= (fim.year, fim.month) and len(meses) < MAX_MESES_CURVA:
        meses.append((ano, mes))
        ano, mes = (ano + 1, 1) if mes == 12 else (ano, mes + 1)
    return meses


def _curva(db: Session, projeto: Projeto, total_orcado: Decimal,
           periodo: Tuple[Optional[date], Optional[date]]) -> List[PontoCurva]:
    por_mes: Dict[Tuple[int, int], Decimal] = defaultdict(lambda: ZERO)
    datas: List[date] = []
    for dia, soma in db.execute(
        select(LancamentoOrcamento.data, func.sum(LancamentoOrcamento.valor))
        .where(LancamentoOrcamento.projeto_id == projeto.id).group_by(LancamentoOrcamento.data)
    ):
        por_mes[(dia.year, dia.month)] += Decimal(soma)
        datas.append(dia)

    inicio, fim = periodo
    com_plano = inicio is not None and fim is not None and fim >= inicio and total_orcado > 0
    limites = datas + ([inicio, fim] if com_plano else [])
    if not limites:
        return []
    duracao = (fim - inicio).days + 1 if com_plano else 0

    pontos, acumulado = [], ZERO
    for ano, mes in _meses(min(limites), max(limites)):
        no_mes = por_mes.get((ano, mes), ZERO)
        acumulado += no_mes
        planejado = None
        if com_plano:
            ultimo_dia = date(ano, mes, calendar.monthrange(ano, mes)[1])
            decorridos = min(max((ultimo_dia - inicio).days + 1, 0), duracao)
            planejado = (total_orcado * decorridos / duracao).quantize(CENTAVO, ROUND_HALF_UP)
        pontos.append(PontoCurva(mes=f"{ano}-{mes:02d}", realizado_no_mes=no_mes,
                                 realizado_acumulado=acumulado, planejado_acumulado=planejado))
    return pontos


def resumo(db: Session, projeto: Projeto, hoje: Optional[date] = None) -> ResumoOrcamento:
    hoje = hoje or tempo.hoje()
    itens = ler_itens(db, projeto)
    total_orcado = sum((i.valor_orcado for i in itens), ZERO)
    total_realizado = sum((i.valor_realizado for i in itens), ZERO)
    indicadores = services.calcular_indicadores(db, projeto, hoje)
    avanco = indicadores.percentual_conclusao
    previsao = (
        (total_realizado / Decimal(str(avanco)) * 100).quantize(CENTAVO, ROUND_HALF_UP)
        if avanco > 0 and total_realizado > 0 else None
    )

    por_categoria = [
        TotalOrcamento(chave=c.value, nome=NOME_CATEGORIA[c],
                       orcado=sum((i.valor_orcado for i in itens if i.categoria == c), ZERO),
                       realizado=sum((i.valor_realizado for i in itens if i.categoria == c), ZERO))
        for c in CategoriaOrcamento
    ]
    pilares: Dict[Optional[int], List] = {}
    for i in itens:
        grupo = pilares.setdefault(i.pilar_id, [i.pilar_nome or "Sem pilar", ZERO, ZERO])
        grupo[1] += i.valor_orcado
        grupo[2] += i.valor_realizado
    por_pilar = [
        TotalOrcamento(chave=str(pid) if pid is not None else "sem_pilar", nome=nome, orcado=o, realizado=r)
        for pid, (nome, o, r) in sorted(pilares.items(), key=lambda kv: (kv[0] is None, kv[1][0]))
    ]

    periodo = (projeto.data_inicio or indicadores.data_inicio_prevista, indicadores.data_fim_prevista)
    curva = _curva(db, projeto, total_orcado, periodo)

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
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=(
            f"“{i.descricao}” estourou: {reais(i.valor_realizado)} de {reais(i.valor_orcado)} orçados.")))
    if len(estourados) > 5:
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=f"Mais {len(estourados) - 5} item(ns) estourado(s)."))
    if previsao is not None and total_orcado > 0 and previsao > total_orcado * Decimal("1.05"):
        acima = _percentual(previsao - total_orcado, total_orcado)
        alertas.append(AlertaOrcamento(nivel="medio", mensagem=(
            f"No ritmo atual ({avanco:.0f}% do cronograma feito), o projeto deve fechar em {reais(previsao)}, "
            f"{acima:.0f}% acima do orçado.")))
    mes_atual = f"{hoje.year}-{hoje.month:02d}"
    agora = next((p for p in curva if p.mes == mes_atual), None)
    if agora and agora.planejado_acumulado and agora.realizado_acumulado > agora.planejado_acumulado * Decimal("1.1"):
        alertas.append(AlertaOrcamento(nivel="info", mensagem=(
            f"Gasto adiantado: {reais(agora.realizado_acumulado)} até este mês, contra {reais(agora.planejado_acumulado)} "
            "previstos pela distribuição do orçado no período.")))

    return ResumoOrcamento(
        orcamento_aprovado=aprovado, total_orcado=total_orcado, total_realizado=total_realizado,
        saldo=total_orcado - total_realizado, execucao=_percentual(total_realizado, total_orcado),
        avanco_fisico=avanco, previsao_final=previsao, itens=itens, por_categoria=por_categoria,
        por_pilar=por_pilar, curva=curva, alertas=alertas,
    )
