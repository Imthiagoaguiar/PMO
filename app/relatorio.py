"""
Relatório do projeto em PDF (ReportLab): resumo e indicadores, pilares, linha do tempo,
histórico e comentários. Gerado no servidor: sai igual em qualquer computador (e no app desktop).

Segurança: todo texto digitado pelas pessoas passa por `_p`/`_limpo` antes de entrar no PDF.
O Paragraph do ReportLab entende uma mini-linguagem de marcação (<b>, <font>, <img src=...>):
sem o escape, um comentário com <img src="/etc/..."> faria o servidor ler arquivos locais.
"""

import io
import math
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, List, Optional, Sequence
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from reportlab.graphics.charts.barcharts import HorizontalBarChart, VerticalBarChart
from reportlab.graphics.charts.linecharts import HorizontalLineChart
from reportlab.graphics.shapes import Circle, Drawing, Line, Polygon, Rect, String
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import HRFlowable, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.config import settings
from app.models import Auditoria, Comentario, CorMarco, FarolPilar, FormaMarco, MarcoLinhaTempo, Projeto
from app.schemas import PilarRead, ProjetoIndicadores, ResumoOrcamento

MARGEM = 1.8 * cm
LARGURA_UTIL = A4[0] - 2 * MARGEM
MAX_TAREFAS_LINHA_DO_TEMPO = 60
LINHAS_POR_BLOCO = 30

COR_PRIMARIA = colors.HexColor("#1d4ed8")  # séries "realizado" dos gráficos
COR_MARCA = colors.HexColor("#0a0a0a")  # OMPY: preto e branco com detalhes em amarelo
COR_AMARELO = colors.HexColor("#facc15")
COR_TEXTO_FRACO = colors.HexColor("#6b6b6b")
COR_BORDA = colors.HexColor("#e5e5e5")
COR_FUNDO = colors.HexColor("#fafafa")
CORES_FASE = [colors.HexColor(c) for c in ("#2563eb", "#7c3aed", "#059669", "#d97706", "#475569")]
COR_FAROL = {
    FarolPilar.VERDE: colors.HexColor("#16a34a"),
    FarolPilar.AMARELO: colors.HexColor("#ca8a04"),
    FarolPilar.VERMELHO: colors.HexColor("#dc2626"),
    None: colors.HexColor("#94a3b8"),
}
NOME_FAROL = {FarolPilar.VERDE: "Verde", FarolPilar.AMARELO: "Amarelo", FarolPilar.VERMELHO: "Vermelho", None: "Sem farol"}
# As mesmas cores da tela (frontend/src/components/projeto/LinhaDoTempo.tsx).
COR_MARCO = {
    CorMarco.VERMELHO: colors.HexColor("#dc2626"), CorMarco.LARANJA: colors.HexColor("#ea580c"),
    CorMarco.AMARELO: colors.HexColor("#ca8a04"), CorMarco.VERDE: colors.HexColor("#16a34a"),
    CorMarco.AZUL: colors.HexColor("#2563eb"), CorMarco.ROXO: colors.HexColor("#7c3aed"),
    CorMarco.CINZA: colors.HexColor("#64748b"),
}
NOME_FORMA = {
    FormaMarco.PIN: "pin", FormaMarco.BANDEIRA: "bandeira", FormaMarco.CIRCULO: "círculo",
    FormaMarco.QUADRADO: "quadrado", FormaMarco.TRIANGULO: "triângulo", FormaMarco.LOSANGO: "losango",
    FormaMarco.ESTRELA: "estrela", FormaMarco.FAIXA: "período",
}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]

ACOES = {
    "projeto.criado": "criou o projeto",
    "projeto.importado": "importou o projeto de uma planilha",
    "projeto.atualizado": "atualizou o projeto",
    "projeto.exportado": "exportou o projeto para Excel",
    "projeto.relatorio_pdf": "gerou o relatório em PDF",
    "tarefa.criada": "criou uma tarefa",
    "tarefa.atualizada": "atualizou uma tarefa",
    "tarefa.excluida": "excluiu uma tarefa",
    "pilar.criado": "criou um pilar",
    "pilar.atualizado": "atualizou um pilar",
    "pilar.excluido": "excluiu um pilar",
    "membro.adicionado": "adicionou um membro",
    "membro.removido": "removeu um membro",
    "membro.papel_alterado": "alterou o papel de um membro",
    "comentario.criado": "comentou",
    "comentario.editado": "editou um comentário",
    "comentario.excluido": "excluiu um comentário",
}
CAMPOS = {
    "nome_projeto": "nome", "orcamento_aprovado": "orçamento", "data_inicio": "início", "data_fim": "término",
    "status": "status", "descricao": "descrição", "percentual_conclusao": "% concluído",
    "responsavel": "responsável", "responsavel_id": "responsável (usuário)", "fase_id": "fase", "papel": "papel",
    "nome": "nome", "email": "e-mail", "fase": "fase", "tarefas": "tarefas", "farol": "farol",
    "status_texto": "situação", "valor_orcado": "orçado", "valor_realizado": "realizado", "pilar_id": "pilar",
    "pilar": "pilar", "ordem": "ordem", "arquivo": "arquivo",
}
DETALHES_OCULTOS = {"modo", "tarefa_id", "autor_id", "modelo", "caracteres"}


@dataclass
class DadosRelatorio:
    projeto: Projeto
    dono: str
    indicadores: ProjetoIndicadores
    pilares: List[PilarRead]
    historico: Sequence[Auditoria]
    comentarios: Sequence[Comentario]
    gerado_por: str
    agora_utc: datetime
    hoje: date
    linha_do_tempo: List[Any]  # [(tarefa, índice da fase)], as primeiras por data de início
    total_com_datas: int
    marcos: List[MarcoLinhaTempo] = field(default_factory=list)
    orcamento: Optional[ResumoOrcamento] = None


def tarefas_da_linha_do_tempo(db: Any, projeto: Projeto) -> dict:
    """Só as tarefas que cabem no Gantt (e quantas têm datas), sem carregar o cronograma inteiro."""
    from sqlalchemy import func, select

    from app.models import Fase, Tarefa

    com_datas = (Fase.projeto_id == projeto.id, Tarefa.data_inicio.is_not(None), Tarefa.data_fim.is_not(None))
    total = db.scalar(select(func.count()).select_from(Tarefa).join(Fase).where(*com_datas)) or 0
    linhas = db.execute(
        select(Tarefa, Fase.ordem_sequencia).join(Fase).where(*com_datas)
        .order_by(Tarefa.data_inicio, Fase.ordem_sequencia, Tarefa.id).limit(MAX_TAREFAS_LINHA_DO_TEMPO)
    ).all()
    return {"linha_do_tempo": [(t, ordem - 1) for t, ordem in linhas], "total_com_datas": total}


# ---------- Texto seguro e formatação ----------

_CONTROLE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def _limpo(texto: Any) -> str:
    """Só o que a fonte Helvetica (WinAnsi/cp1252) desenha; emoji e afins viram "?"."""
    t = "" if texto is None else str(texto)
    return _CONTROLE.sub("", t).encode("cp1252", "replace").decode("cp1252")


def _p(texto: Any, estilo: ParagraphStyle) -> Paragraph:
    """Paragraph com o texto ESCAPADO: marcação digitada pelo usuário vira texto, não comando."""
    return Paragraph(escape(_limpo(texto)).replace("\n", "<br/>"), estilo)


def _data(d: Optional[date]) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _local(instante: datetime) -> datetime:
    instante = instante if instante.tzinfo else instante.replace(tzinfo=timezone.utc)
    return instante.astimezone(ZoneInfo(settings.fuso_horario))


def _data_hora(instante: Optional[datetime]) -> str:
    return _local(instante).strftime("%d/%m/%Y %H:%M") if instante else "—"


def _moeda(valor: Optional[Decimal]) -> str:
    if valor is None:
        return "—"
    texto = f"{valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"R$ {texto}"


CAMPOS_EM_REAIS = {"orcamento_aprovado", "valor_orcado", "valor_realizado"}


def _valor_detalhe(v: Any, campo: str = "") -> str:
    if v is None or v == "":
        return "vazio"
    if campo in CAMPOS_EM_REAIS:
        try:
            return _moeda(Decimal(str(v)))
        except ArithmeticError:
            return str(v)
    if isinstance(v, bool):
        return "sim" if v else "não"
    if isinstance(v, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return "/".join(reversed(v.split("-")))
    return str(v)


def _descrever(evento: Auditoria) -> str:
    texto = ACOES.get(evento.acao, evento.acao)
    partes = []
    for campo, valor in (evento.detalhes or {}).items():
        if campo in DETALHES_OCULTOS or valor is None:
            continue
        rotulo = CAMPOS.get(campo, campo)
        if isinstance(valor, list) and len(valor) == 2:
            partes.append(f"{rotulo}: de {_valor_detalhe(valor[0], campo)} para {_valor_detalhe(valor[1], campo)}")
        else:
            partes.append(f"{rotulo}: {_valor_detalhe(valor, campo)}")
    return texto + (f" ({'; '.join(partes)})" if partes else "")


# ---------- Estilos ----------

ESTILOS = {
    "titulo": ParagraphStyle("titulo", fontName="Helvetica-Bold", fontSize=20, leading=24, spaceAfter=4),
    "subtitulo": ParagraphStyle("subtitulo", fontName="Helvetica", fontSize=9, leading=12, textColor=COR_TEXTO_FRACO),
    "secao": ParagraphStyle("secao", fontName="Helvetica-Bold", fontSize=13, leading=16, spaceBefore=14,
                            spaceAfter=6, textColor=COR_MARCA),
    "normal": ParagraphStyle("normal", fontName="Helvetica", fontSize=9, leading=12),
    "pequeno": ParagraphStyle("pequeno", fontName="Helvetica", fontSize=8, leading=10),
    "fraco": ParagraphStyle("fraco", fontName="Helvetica", fontSize=8, leading=10, textColor=COR_TEXTO_FRACO),
    "kpi": ParagraphStyle("kpi", fontName="Helvetica-Bold", fontSize=16, leading=19),
    "kpi_rotulo": ParagraphStyle("kpi_rotulo", fontName="Helvetica", fontSize=8, leading=10, textColor=COR_TEXTO_FRACO),
    "celula_branca": ParagraphStyle("celula_branca", fontName="Helvetica-Bold", fontSize=8, leading=10,
                                    textColor=colors.white, alignment=1),
}

TABELA_PADRAO = [
    ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ("FONTSIZE", (0, 0), (-1, -1), 8),
    ("TEXTCOLOR", (0, 0), (-1, 0), COR_TEXTO_FRACO),
    ("LINEBELOW", (0, 0), (-1, 0), 0.8, COR_BORDA),
    ("LINEBELOW", (0, 1), (-1, -1), 0.4, COR_BORDA),
    ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ("TOPPADDING", (0, 0), (-1, -1), 4),
    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
]


def _secao(titulo: str) -> Paragraph:
    return Paragraph(escape(titulo), ESTILOS["secao"])


# ---------- Seções ----------


def _cabecalho(d: DadosRelatorio) -> List[Any]:
    gerado = _local(d.agora_utc).strftime("%d/%m/%Y às %H:%M")
    return [
        HRFlowable(width=2 * cm, thickness=4, color=COR_AMARELO, hAlign="LEFT", spaceAfter=8),
        _p(d.projeto.nome_projeto, ESTILOS["titulo"]),
        _p(f"Relatório do projeto · gerado em {gerado} por {d.gerado_por}", ESTILOS["subtitulo"]),
        Spacer(1, 10),
    ]


def _resumo(d: DadosRelatorio) -> List[Any]:
    ind, projeto = d.indicadores, d.projeto
    campos = [
        ("Status", projeto.status.value),
        ("Dono", d.dono),
        ("Fase atual", ind.fase_atual or "—"),
        ("Início previsto", _data(ind.data_inicio_prevista or projeto.data_inicio)),
        ("Término previsto", _data(ind.data_fim_prevista)),
        ("Orçamento aprovado", _moeda(projeto.orcamento_aprovado)),
    ]
    linhas = [
        [_p(campos[i][0], ESTILOS["fraco"]), _p(campos[i][1], ESTILOS["normal"]),
         _p(campos[i + 1][0], ESTILOS["fraco"]), _p(campos[i + 1][1], ESTILOS["normal"])]
        for i in range(0, len(campos), 2)
    ]
    resumo = Table(linhas, colWidths=[3 * cm, LARGURA_UTIL / 2 - 3 * cm] * 2)
    resumo.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 3)]))

    vermelhos = sum(p.farol == FarolPilar.VERMELHO for p in d.pilares)
    kpis = [
        (f"{ind.percentual_conclusao:.0f}%".replace(".", ","), "Andamento geral"),
        (f"{ind.tarefas_concluidas} de {ind.total_tarefas}", "Tarefas concluídas"),
        (str(ind.tarefas_atrasadas), "Tarefas atrasadas"),
        (str(vermelhos), "Pilares em vermelho"),
    ]
    cartoes = Table(
        [[_p(v, ESTILOS["kpi"]) for v, _ in kpis], [_p(r, ESTILOS["kpi_rotulo"]) for _, r in kpis]],
        colWidths=[LARGURA_UTIL / 4] * 4,
    )
    estilo_cartoes = [
        ("BACKGROUND", (0, 0), (-1, -1), COR_FUNDO),
        ("LINEAFTER", (0, 0), (-2, -1), 3, colors.white),
        ("TOPPADDING", (0, 0), (-1, 0), 8),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
    ]
    if ind.tarefas_atrasadas:
        estilo_cartoes.append(("TEXTCOLOR", (2, 0), (2, 0), COR_FAROL[FarolPilar.VERMELHO]))
    cartoes.setStyle(TableStyle(estilo_cartoes))
    return [_secao("Resumo"), resumo, Spacer(1, 10), cartoes, Spacer(1, 8),
            KeepTogether([Paragraph("Andamento por fase", ESTILOS["normal"]),
                          _grafico_barras([f.titulo for f in ind.fases],
                                          [f.percentual_conclusao for f in ind.fases], CORES_FASE)])]


def _grafico_barras(rotulos: List[str], valores: List[float], cores: List[Any], altura_barra: float = 16) -> Drawing:
    """Barras horizontais de 0 a 100%, uma por item, com o valor escrito na ponta."""
    altura = max(len(rotulos), 1) * altura_barra + 24
    desenho = Drawing(LARGURA_UTIL, altura)
    grafico = HorizontalBarChart()
    grafico.x, grafico.y = 3.6 * cm, 14
    grafico.width, grafico.height = LARGURA_UTIL - 3.6 * cm - 1.4 * cm, altura - 22
    # De baixo para cima no ReportLab: inverte para a 1ª fase ficar em cima.
    grafico.data = [list(reversed(valores)) or [0]]
    grafico.categoryAxis.categoryNames = [_limpo(r)[:28] for r in reversed(rotulos)] or [""]
    grafico.categoryAxis.labels.fontName = "Helvetica"
    grafico.categoryAxis.labels.fontSize = 8
    grafico.categoryAxis.labels.dx = -4
    grafico.categoryAxis.labels.boxAnchor = "e"
    grafico.categoryAxis.strokeColor = COR_BORDA
    grafico.valueAxis.valueMin, grafico.valueAxis.valueMax, grafico.valueAxis.valueStep = 0, 100, 25
    grafico.valueAxis.labels.fontName = "Helvetica"
    grafico.valueAxis.labels.fontSize = 7
    grafico.valueAxis.labelTextFormat = "%d%%"
    grafico.valueAxis.strokeColor = COR_BORDA
    grafico.valueAxis.visibleGrid = True
    grafico.valueAxis.gridStrokeColor = COR_BORDA
    grafico.bars.strokeColor = None
    grafico.barWidth = altura_barra * 0.6
    grafico.groupSpacing = altura_barra * 0.4
    for i, cor in enumerate(reversed(cores[: len(rotulos)])):
        grafico.bars[(0, i)].fillColor = cor
    grafico.barLabelFormat = lambda v: f"{v:.0f}%"
    grafico.barLabels.fontName = "Helvetica"
    grafico.barLabels.fontSize = 7
    grafico.barLabels.boxAnchor = "w"
    grafico.barLabels.dx = 3
    desenho.add(grafico)
    return desenho


def _pilares(d: DadosRelatorio) -> List[Any]:
    itens: List[Any] = [_secao("Pilares")]
    if not d.pilares:
        return itens + [_p("Este projeto não tem pilares.", ESTILOS["fraco"])]

    cabecalho = ["Farol", "Pilar", "Andamento", "Tarefas", "Atrasadas", "Orçado", "Realizado", "Desvio"]
    linhas: List[List[Any]] = [cabecalho]
    estilo = list(TABELA_PADRAO)
    for i, p in enumerate(d.pilares, start=1):
        linhas.append([
            _p(NOME_FAROL[p.farol], ESTILOS["celula_branca"]),
            _p(p.nome, ESTILOS["pequeno"]),
            f"{p.percentual_conclusao:.0f}%",
            f"{p.tarefas_concluidas}/{p.total_tarefas}",
            str(p.tarefas_atrasadas),
            _moeda(p.valor_orcado),
            _moeda(p.valor_realizado),
            _moeda(p.desvio) if p.desvio is None or p.desvio <= 0 else "+" + _moeda(p.desvio),
        ])
        estilo.append(("BACKGROUND", (0, i), (0, i), COR_FAROL[p.farol]))
        if p.tarefas_atrasadas:
            estilo.append(("TEXTCOLOR", (4, i), (4, i), COR_FAROL[FarolPilar.VERMELHO]))
        if p.desvio is not None and p.desvio > 0:
            estilo.append(("TEXTCOLOR", (7, i), (7, i), COR_FAROL[FarolPilar.VERMELHO]))
    larguras = [2.1, 3.4, 1.7, 1.4, 1.6, 2.4, 2.4, 2.4]  # soma = largura útil (17,4 cm)
    tabela = Table(linhas, colWidths=[w * cm for w in larguras], repeatRows=1)
    estilo += [("ALIGN", (2, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    tabela.setStyle(TableStyle(estilo))
    itens += [tabela, Spacer(1, 8)]

    com_status = [p for p in d.pilares if p.status_texto]
    if com_status:
        itens.append(Paragraph("Situação de cada pilar", ESTILOS["normal"]))
        itens.append(Spacer(1, 3))
        for p in com_status:
            quem = p.status_atualizado_por.nome if p.status_atualizado_por else "—"
            itens.append(Paragraph(
                f"<b>{escape(_limpo(p.nome))}</b> · {NOME_FAROL[p.farol].lower()} · "
                f"atualizado em {_data_hora(p.status_atualizado_em)} por {escape(_limpo(quem))}<br/>"
                f"{escape(_limpo(p.status_texto)).replace(chr(10), '<br/>')}",
                ESTILOS["pequeno"],
            ))
            itens.append(Spacer(1, 4))

    itens.append(KeepTogether([
        Paragraph("Andamento por pilar", ESTILOS["normal"]),
        _grafico_barras([p.nome for p in d.pilares], [p.percentual_conclusao for p in d.pilares],
                        [COR_FAROL[p.farol] for p in d.pilares], altura_barra=14),
    ]))
    financeiros = [p for p in d.pilares if p.valor_orcado is not None or p.valor_realizado is not None]
    if financeiros:
        itens.append(KeepTogether([Spacer(1, 6), Paragraph("Orçado x realizado por pilar", ESTILOS["normal"]),
                                   _grafico_financeiro(financeiros)]))
    return itens


def _orcamento(d: DadosRelatorio) -> List[Any]:
    o = d.orcamento
    if o is None or not o.itens:
        return []
    itens: List[Any] = [_secao("Orçamento")]
    cartoes = [
        ("Aprovado", _moeda(o.orcamento_aprovado)), ("Orçado (itens)", _moeda(o.total_orcado)),
        ("Realizado", _moeda(o.total_realizado)), ("Projetado", _moeda(o.total_projetado)), ("Saldo", _moeda(o.saldo)),
        ("Execução", f"{o.execucao:.0f}%" if o.execucao is not None else "—"),
        ("Previsão ao final", _moeda(o.previsao_final)),
    ]
    tabela = Table([[_p(r, ESTILOS["kpi_rotulo"]) for r, _ in cartoes], [_p(v, ESTILOS["pequeno"]) for _, v in cartoes]],
                   colWidths=[LARGURA_UTIL / len(cartoes)] * len(cartoes))
    estilo = [("BACKGROUND", (0, 0), (-1, -1), COR_FUNDO), ("BOX", (0, 0), (-1, -1), 0.5, COR_BORDA)]
    if o.saldo < 0:
        estilo.append(("TEXTCOLOR", (3, 1), (3, 1), COR_FAROL[FarolPilar.VERMELHO]))
    tabela.setStyle(TableStyle(estilo))
    itens += [tabela, Spacer(1, 6)]

    for alerta in o.alertas:
        prefixo = "<b>Atenção:</b> " if alerta.nivel != "info" else "<b>Nota:</b> "
        itens.append(Paragraph(prefixo + escape(_limpo(alerta.mensagem)), ESTILOS["pequeno"]))
    if o.alertas:
        itens.append(Spacer(1, 6))

    if len(o.curva) >= 2:
        itens.append(KeepTogether([Paragraph("Realizado x planejado (acumulado, por mês)", ESTILOS["normal"]),
                                   _grafico_curva(o)]))

    linhas: List[List[Any]] = [["Item", "Categoria", "Pilar", "Orçado", "Realizado", "Saldo", "Exec."]]
    estilo = list(TABELA_PADRAO)
    for i, item in enumerate(o.itens, start=1):
        linhas.append([_p(item.descricao, ESTILOS["pequeno"]), item.categoria.value.upper(),
                       _p(item.pilar_nome or "—", ESTILOS["pequeno"]), _moeda(item.valor_orcado),
                       _moeda(item.valor_realizado), _moeda(item.saldo),
                       f"{item.execucao:.0f}%" if item.execucao is not None else "—"])
        if item.estourado:
            estilo.append(("TEXTCOLOR", (5, i), (6, i), COR_FAROL[FarolPilar.VERMELHO]))
    larguras = [4.6, 1.7, 2.4, 2.5, 2.5, 2.5, 1.2]
    tabela = Table(linhas, colWidths=[w * cm for w in larguras], repeatRows=1)
    tabela.setStyle(TableStyle(estilo + [("ALIGN", (3, 0), (-1, -1), "RIGHT"), ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
    itens += [Spacer(1, 6), tabela]
    return itens


def _grafico_curva(o: ResumoOrcamento) -> Drawing:
    desenho = Drawing(LARGURA_UTIL, 160)
    grafico = HorizontalLineChart()
    grafico.x, grafico.y, grafico.width, grafico.height = 1.6 * cm, 28, LARGURA_UTIL - 2 * cm, 110
    series = [[float(p.realizado_acumulado) for p in o.curva]]
    com_plano = any(p.planejado_acumulado is not None for p in o.curva)
    if com_plano:
        series.append([float(p.planejado_acumulado or 0) for p in o.curva])
    grafico.data = series
    passo = max(1, len(o.curva) // 12)
    grafico.categoryAxis.categoryNames = [
        f"{MESES[int(p.mes[5:]) - 1]}/{p.mes[2:4]}" if i % passo == 0 else "" for i, p in enumerate(o.curva)]
    grafico.categoryAxis.labels.fontName = "Helvetica"
    grafico.categoryAxis.labels.fontSize = 6.5
    grafico.categoryAxis.strokeColor = COR_BORDA
    grafico.valueAxis.valueMin = 0
    grafico.valueAxis.labels.fontName = "Helvetica"
    grafico.valueAxis.labels.fontSize = 7
    grafico.valueAxis.labelTextFormat = _abreviar
    grafico.valueAxis.visibleGrid = True
    grafico.valueAxis.gridStrokeColor = COR_BORDA
    grafico.valueAxis.strokeColor = COR_BORDA
    grafico.lines[0].strokeColor = COR_PRIMARIA
    grafico.lines[0].strokeWidth = 1.6
    if com_plano:
        grafico.lines[1].strokeColor = colors.HexColor("#94a3b8")
        grafico.lines[1].strokeWidth = 1.2
        grafico.lines[1].strokeDashArray = [3, 2]
    desenho.add(grafico)
    legendas = [("Realizado", COR_PRIMARIA)] + ([("Planejado", colors.HexColor("#94a3b8"))] if com_plano else [])
    for i, (rotulo, cor) in enumerate(legendas):
        x = 1.6 * cm + i * 2.6 * cm
        desenho.add(Line(x, 152, x + 10, 152, strokeColor=cor, strokeWidth=1.6))
        desenho.add(String(x + 13, 149.5, rotulo, fontName="Helvetica", fontSize=7))
    return desenho


def _abreviar(valor: float) -> str:
    for limite, sufixo in ((1e9, " bi"), (1e6, " mi"), (1e3, " mil")):
        if abs(valor) >= limite:
            return f"{valor / limite:.1f}".replace(".", ",").replace(",0", "") + sufixo
    return f"{valor:.0f}"


def _grafico_financeiro(pilares: List[PilarRead]) -> Drawing:
    desenho = Drawing(LARGURA_UTIL, 170)
    grafico = VerticalBarChart()
    grafico.x, grafico.y, grafico.width, grafico.height = 1.6 * cm, 30, LARGURA_UTIL - 2 * cm, 115
    grafico.data = [
        [float(p.valor_orcado or 0) for p in pilares],
        [float(p.valor_realizado or 0) for p in pilares],
    ]
    grafico.categoryAxis.categoryNames = [_limpo(p.nome)[:14] for p in pilares]
    grafico.categoryAxis.labels.fontName = "Helvetica"
    grafico.categoryAxis.labels.fontSize = 7
    grafico.categoryAxis.strokeColor = COR_BORDA
    grafico.valueAxis.valueMin = 0
    grafico.valueAxis.labels.fontName = "Helvetica"
    grafico.valueAxis.labels.fontSize = 7
    grafico.valueAxis.labelTextFormat = _abreviar
    grafico.valueAxis.visibleGrid = True
    grafico.valueAxis.gridStrokeColor = COR_BORDA
    grafico.valueAxis.strokeColor = COR_BORDA
    grafico.bars.strokeColor = None
    grafico.bars[0].fillColor = colors.HexColor("#93c5fd")
    grafico.bars[1].fillColor = COR_PRIMARIA
    desenho.add(grafico)
    for i, (rotulo, cor) in enumerate((("Orçado", colors.HexColor("#93c5fd")), ("Realizado", COR_PRIMARIA))):
        x = 1.6 * cm + i * 2.6 * cm
        desenho.add(Rect(x, 158, 8, 8, fillColor=cor, strokeColor=None))
        desenho.add(String(x + 11, 159, rotulo, fontName="Helvetica", fontSize=7))
    return desenho


def _linha_do_tempo(d: DadosRelatorio) -> List[Any]:
    itens: List[Any] = [_secao("Linha do tempo")]
    tarefas, marcos = d.linha_do_tempo, d.marcos
    if not tarefas and not marcos:
        return itens + [_p("Nenhuma tarefa com início e término definidos.", ESTILOS["fraco"])]
    omitidas = max(d.total_com_datas - len(tarefas), 0)

    datas = [t.data_inicio for t, _ in tarefas] + [m.data for m in marcos]
    fins = [t.data_fim for t, _ in tarefas] + [m.data_fim or m.data for m in marcos]
    inicio, fim = min(datas), max(fins) + timedelta(days=1)
    if (fim - inicio).days < 14:
        fim = inicio + timedelta(days=14)

    if tarefas:
        for bloco in range(0, len(tarefas), LINHAS_POR_BLOCO):
            itens.append(_gantt(tarefas[bloco: bloco + LINHAS_POR_BLOCO], inicio, fim, d.hoje, marcos))
            itens.append(Spacer(1, 6))
    else:
        itens += [_gantt([], inicio, fim, d.hoje, marcos), Spacer(1, 6)]

    legenda = Drawing(LARGURA_UTIL, 12)
    x = 0.0
    # As fases do PROJETO (renomeadas, criadas ou excluídas pelo editor), não as 5 fixas do Waterfall.
    for i, fase in enumerate(d.projeto.fases[:8]):
        titulo = _limpo(fase.titulo)
        legenda.add(Rect(x, 2, 8, 8, fillColor=CORES_FASE[i % len(CORES_FASE)], strokeColor=None))
        legenda.add(String(x + 11, 3, titulo if len(titulo) <= 16 else titulo[:15] + "…", fontName="Helvetica", fontSize=7))
        x += 2.2 * cm
    legenda.add(Line(x, 0, x, 11, strokeColor=COR_FAROL[FarolPilar.VERMELHO], strokeDashArray=[2, 2]))
    legenda.add(String(x + 4, 3, "hoje", fontName="Helvetica", fontSize=7))
    itens.append(legenda)
    if omitidas:
        itens.append(_p(f"Mostrando as {MAX_TAREFAS_LINHA_DO_TEMPO} primeiras tarefas por data de início; "
                        f"+{omitidas} tarefa(s) não aparecem aqui.", ESTILOS["fraco"]))

    if marcos:
        linhas: List[List[Any]] = [["", "Data", "Marco", "Descrição"]]
        estilo = list(TABELA_PADRAO)
        for i, m in enumerate(marcos, start=1):
            quando = _data(m.data) + (f" a {_data(m.data_fim)}" if m.data_fim else "")
            linhas.append(["", quando, _p(m.titulo, ESTILOS["pequeno"]), _p(m.descricao or "—", ESTILOS["pequeno"])])
            estilo.append(("BACKGROUND", (0, i), (0, i), COR_MARCO[m.cor]))
        tabela = Table(linhas, colWidths=[0.4 * cm, 3.8 * cm, 4.6 * cm, LARGURA_UTIL - 8.8 * cm], repeatRows=1)
        tabela.setStyle(TableStyle(estilo + [("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        itens += [Spacer(1, 6), Paragraph("Marcos", ESTILOS["normal"]), Spacer(1, 3), tabela]
    return itens


def _forma_marco(m: MarcoLinhaTempo, x: float, y: float, cor: Any) -> List[Any]:
    """Desenho pequeno (~8 pt) da forma do marco, centrado em (x, y)."""
    r = 3.6
    if m.forma == FormaMarco.CIRCULO:
        return [Circle(x, y, r, fillColor=cor, strokeColor=None)]
    if m.forma == FormaMarco.QUADRADO:
        return [Rect(x - r, y - r, 2 * r, 2 * r, fillColor=cor, strokeColor=None)]
    if m.forma == FormaMarco.TRIANGULO:
        return [Polygon([x - r, y - r, x + r, y - r, x, y + r], fillColor=cor, strokeColor=None)]
    if m.forma == FormaMarco.LOSANGO:
        return [Polygon([x, y + r, x + r, y, x, y - r, x - r, y], fillColor=cor, strokeColor=None)]
    if m.forma == FormaMarco.ESTRELA:
        pontos = []
        for k in range(10):
            raio = r * 1.2 if k % 2 == 0 else r * 0.5
            ang = math.pi / 2 + k * math.pi / 5
            pontos += [x + raio * math.cos(ang), y + raio * math.sin(ang)]
        return [Polygon(pontos, fillColor=cor, strokeColor=None)]
    if m.forma == FormaMarco.PIN:
        return [Line(x, y - r, x, y + 1, strokeColor=cor, strokeWidth=1), Circle(x, y + 2, 2.4, fillColor=cor, strokeColor=None)]
    # bandeira (e a ponta de uma faixa)
    return [Line(x, y - r, x, y + r, strokeColor=cor, strokeWidth=1),
            Polygon([x, y + r, x + 6, y + r - 2, x, y + r - 4], fillColor=cor, strokeColor=None)]


def _gantt(tarefas: List[Any], inicio: date, fim: date, hoje: date,
           marcos: Sequence[MarcoLinhaTempo] = ()) -> Drawing:
    linha, rotulo = 13.0, 5.4 * cm
    faixa_marcos = 14.0 if marcos else 0.0  # a linha dos marcos, logo abaixo das datas
    topo = 16.0 + faixa_marcos
    area = LARGURA_UTIL - rotulo
    altura = topo + len(tarefas) * linha + 2
    desenho = Drawing(LARGURA_UTIL, altura)
    dias = (fim - inicio).days

    def x(dia: date) -> float:
        return rotulo + (dia - inicio).days / dias * area

    # Marcas de mês (1º dia de cada mês dentro do intervalo).
    cursor = date(inicio.year, inicio.month, 1)
    while cursor < fim:
        if cursor >= inicio:
            desenho.add(Line(x(cursor), 0, x(cursor), altura - topo + 2, strokeColor=COR_BORDA, strokeWidth=0.5))
            desenho.add(String(x(cursor) + 2, altura - 10, f"{MESES[cursor.month - 1]}/{str(cursor.year)[2:]}",
                               fontName="Helvetica", fontSize=6.5, fillColor=COR_TEXTO_FRACO))
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)
    desenho.add(String(rotulo, altura - 10, inicio.strftime("%d/%m/%y"), fontName="Helvetica", fontSize=6.5,
                       fillColor=COR_TEXTO_FRACO, textAnchor="end"))

    for i, (tarefa, fase) in enumerate(tarefas):
        y = altura - topo - (i + 1) * linha
        if i % 2 == 0:
            desenho.add(Rect(0, y, LARGURA_UTIL, linha, fillColor=COR_FUNDO, strokeColor=None))
        texto = _limpo(tarefa.descricao)
        texto = texto if len(texto) <= 42 else texto[:41] + "…"
        desenho.add(String(2, y + 3.5, texto, fontName="Helvetica", fontSize=7))
        x0, x1 = x(tarefa.data_inicio), x(tarefa.data_fim + timedelta(days=1))
        desenho.add(Rect(x0, y + 2.5, max(x1 - x0, 1.5), linha - 5, fillColor=CORES_FASE[fase % len(CORES_FASE)], strokeColor=None))
        if tarefa.percentual_conclusao:
            # Parte já feita, mais escura sobre a barra.
            feito = (x1 - x0) * tarefa.percentual_conclusao / 100
            desenho.add(Rect(x0, y + 2.5, max(feito, 0.5), 2, fillColor=colors.black, fillOpacity=0.35, strokeColor=None))

    if marcos:
        y_marcos = altura - 16.0 - faixa_marcos / 2
        desenho.add(String(2, y_marcos - 2.5, "Marcos", fontName="Helvetica-Bold", fontSize=7, fillColor=COR_TEXTO_FRACO))
        for m in marcos:
            cor = COR_MARCO[m.cor]
            if m.forma == FormaMarco.FAIXA and m.data_fim:
                x0, x1 = x(m.data), x(m.data_fim + timedelta(days=1))
                desenho.add(Rect(x0, 0, max(x1 - x0, 1.5), altura - 16.0, fillColor=cor, fillOpacity=0.12, strokeColor=None))
                desenho.add(Rect(x0, y_marcos - 3, max(x1 - x0, 1.5), 6, fillColor=cor, strokeColor=None))
                continue
            xm = x(m.data) + (x(m.data + timedelta(days=1)) - x(m.data)) / 2
            desenho.add(Line(xm, 0, xm, y_marcos - 4, strokeColor=cor, strokeWidth=0.6, strokeDashArray=[1, 1.5]))
            for parte in _forma_marco(m, xm, y_marcos, cor):
                desenho.add(parte)

    if inicio <= hoje < fim:
        desenho.add(Line(x(hoje), 0, x(hoje), altura - topo + 2, strokeColor=COR_FAROL[FarolPilar.VERMELHO],
                         strokeWidth=0.8, strokeDashArray=[2, 2]))
    return desenho


def _historico(d: DadosRelatorio) -> List[Any]:
    itens: List[Any] = [_secao("Histórico recente")]
    if not d.historico:
        return itens + [_p("Nenhuma alteração registrada.", ESTILOS["fraco"])]
    linhas = [["Quando", "Quem", "O quê"]] + [
        [_data_hora(e.criado_em), _p(e.usuario.nome if e.usuario else "Sistema", ESTILOS["pequeno"]),
         _p(_descrever(e), ESTILOS["pequeno"])]
        for e in d.historico
    ]
    tabela = Table(linhas, colWidths=[2.8 * cm, 3.6 * cm, LARGURA_UTIL - 6.4 * cm], repeatRows=1)
    tabela.setStyle(TableStyle(TABELA_PADRAO))
    return itens + [tabela]


def _comentarios(d: DadosRelatorio) -> List[Any]:
    itens: List[Any] = [_secao("Comentários recentes")]
    if not d.comentarios:
        return itens + [_p("Nenhum comentário.", ESTILOS["fraco"])]
    linhas: List[List[Any]] = [["Quando", "Autor", "Comentário"]]
    for c in d.comentarios:
        onde = f"Na tarefa “{c.tarefa.descricao}”: " if c.tarefa else ""
        linhas.append([_data_hora(c.criado_em), _p(c.autor.nome if c.autor else "Usuário removido", ESTILOS["pequeno"]),
                       _p(onde + c.texto, ESTILOS["pequeno"])])
    tabela = Table(linhas, colWidths=[2.8 * cm, 3.6 * cm, LARGURA_UTIL - 6.4 * cm], repeatRows=1)
    tabela.setStyle(TableStyle(TABELA_PADRAO))
    return itens + [tabela]


# ---------- Documento ----------


def gerar_pdf(d: DadosRelatorio) -> bytes:
    buffer = io.BytesIO()
    nome = _limpo(d.projeto.nome_projeto)
    documento = SimpleDocTemplate(
        buffer, pagesize=A4, leftMargin=MARGEM, rightMargin=MARGEM, topMargin=MARGEM, bottomMargin=MARGEM,
        title=f"Relatório - {nome}", author=_limpo(d.gerado_por), subject="Relatório do projeto", creator="OMPY",
    )

    def _rodape(canvas: Any, doc: Any) -> None:
        canvas.saveState()
        canvas.setFont("Helvetica", 7)
        canvas.setFillColor(COR_TEXTO_FRACO)
        canvas.drawString(MARGEM, MARGEM / 2, "OMPY · " + (nome if len(nome) <= 80 else nome[:79] + "…"))
        canvas.drawRightString(A4[0] - MARGEM, MARGEM / 2, f"Página {doc.page}")
        canvas.restoreState()

    conteudo = (_cabecalho(d) + _resumo(d) + _pilares(d) + _orcamento(d) + _linha_do_tempo(d)
                + _historico(d) + _comentarios(d))
    documento.build(conteudo, onFirstPage=_rodape, onLaterPages=_rodape)
    return buffer.getvalue()
