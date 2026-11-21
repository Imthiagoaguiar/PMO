"""
Apresentação do projeto em PowerPoint (.pptx, 16:9), gerada no servidor com python-pptx.

Slides: capa; resumo (indicadores + andamento por fase); linha do tempo (uma barra por fase, marcos com
as formas e cores escolhidas, períodos e "hoje"); marcos; pilares (farol); orçamento; gargalos; pontos
de atenção (tarefas atrasadas e próximos marcos). Os gráficos são NATIVOS do PowerPoint: a pessoa
clica em "Editar dados" e muda o que quiser. Slides sem conteúdo (ex.: projeto sem orçamento) não entram.

Texto digitado pelas pessoas passa por `_limpo`: caractere de controle quebraria o XML do arquivo.
"""

import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, List, Optional, Sequence, Tuple
from zoneinfo import ZoneInfo

from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.dml import MSO_LINE_DASH_STYLE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.util import Emu, Inches, Pt

from app.config import settings
from app.models import CorMarco, FarolPilar, FormaMarco, MarcoLinhaTempo, Projeto
from app.schemas import Gargalos, PilarRead, ProjetoIndicadores, ResumoOrcamento

AZUL = RGBColor(0x1D, 0x4E, 0xD8)
TEXTO = RGBColor(0x1E, 0x29, 0x3B)
FRACO = RGBColor(0x64, 0x74, 0x8B)
BORDA = RGBColor(0xE2, 0xE8, 0xF0)
FUNDO = RGBColor(0xF8, 0xFA, 0xFC)
BRANCO = RGBColor(0xFF, 0xFF, 0xFF)
VERMELHO = RGBColor(0xDC, 0x26, 0x26)
# As mesmas cores validadas dos gráficos da tela: realizado azul, orçado/planejado laranja.
SERIE_REALIZADO, SERIE_PLANEJADO = RGBColor(0x2A, 0x78, 0xD6), RGBColor(0xEB, 0x68, 0x34)
CORES_FASE = [RGBColor.from_string(c) for c in ("2563EB", "7C3AED", "059669", "D97706", "475569")]
COR_MARCO = {
    CorMarco.VERMELHO: "DC2626", CorMarco.LARANJA: "EA580C", CorMarco.AMARELO: "CA8A04", CorMarco.VERDE: "16A34A",
    CorMarco.AZUL: "2563EB", CorMarco.ROXO: "7C3AED", CorMarco.CINZA: "64748B",
}
CLARO_MARCO = {  # a "transparência" das faixas: o mesmo tom, bem claro (o python-pptx não faz alfa)
    CorMarco.VERMELHO: "FEE2E2", CorMarco.LARANJA: "FFEDD5", CorMarco.AMARELO: "FEF9C3", CorMarco.VERDE: "DCFCE7",
    CorMarco.AZUL: "DBEAFE", CorMarco.ROXO: "EDE9FE", CorMarco.CINZA: "F1F5F9",
}
COR_FAROL = {FarolPilar.VERDE: "16A34A", FarolPilar.AMARELO: "CA8A04", FarolPilar.VERMELHO: "DC2626", None: "94A3B8"}
NOME_FAROL = {FarolPilar.VERDE: "Verde", FarolPilar.AMARELO: "Amarelo", FarolPilar.VERMELHO: "Vermelho", None: "—"}
FORMA = {
    FormaMarco.CIRCULO: MSO_SHAPE.OVAL, FormaMarco.QUADRADO: MSO_SHAPE.RECTANGLE,
    FormaMarco.TRIANGULO: MSO_SHAPE.ISOSCELES_TRIANGLE, FormaMarco.LOSANGO: MSO_SHAPE.DIAMOND,
    FormaMarco.ESTRELA: MSO_SHAPE.STAR_5_POINT, FormaMarco.BANDEIRA: MSO_SHAPE.WAVE, FormaMarco.PIN: MSO_SHAPE.OVAL,
}
MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
LARGURA, ALTURA = Inches(13.333), Inches(7.5)
MARGEM = Inches(0.6)
_CONTROLE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass
class DadosApresentacao:
    projeto: Projeto
    dono: str
    indicadores: ProjetoIndicadores
    pilares: List[PilarRead]
    orcamento: ResumoOrcamento
    gargalos: Gargalos
    marcos: List[MarcoLinhaTempo]
    fases: List[Tuple[str, Optional[date], Optional[date], float]]  # (título, início, fim, % concluído)
    atrasadas: List[Tuple[str, str, date, int]]  # (tarefa, responsável, prazo, dias)
    gerado_por: str
    agora_utc: datetime
    hoje: date
    avisos: List[str] = field(default_factory=list)


def _limpo(texto: Any, maximo: int = 200) -> str:
    t = _CONTROLE.sub("", str(texto if texto is not None else ""))
    return t if len(t) <= maximo else t[: maximo - 1] + "…"


def _data(d: Optional[date]) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _moeda(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------- Peças ----------


def _texto(slide: Any, x: int, y: int, w: int, h: int, texto: str, tamanho: int = 14, negrito: bool = False,
           cor: RGBColor = TEXTO, alinhar: Any = PP_ALIGN.LEFT) -> Any:
    caixa = slide.shapes.add_textbox(x, y, w, h)
    quadro = caixa.text_frame
    quadro.word_wrap = True
    for i, linha in enumerate(texto.split("\n")):
        paragrafo = quadro.paragraphs[0] if i == 0 else quadro.add_paragraph()
        paragrafo.alignment = alinhar
        run = paragrafo.add_run()
        run.text = linha
        run.font.size, run.font.bold, run.font.color.rgb = Pt(tamanho), negrito, cor
    return caixa


def _retangulo(slide: Any, x: int, y: int, w: int, h: int, cor: RGBColor, forma: Any = MSO_SHAPE.RECTANGLE) -> Any:
    r = slide.shapes.add_shape(forma, x, y, max(w, 1), max(h, 1))
    r.fill.solid()
    r.fill.fore_color.rgb = cor
    r.line.fill.background()
    r.shadow.inherit = False
    return r


def _linha(slide: Any, x1: int, y1: int, x2: int, y2: int, cor: RGBColor, largura: float = 1.0, tracejada: bool = False) -> None:
    c = slide.shapes.add_connector(MSO_CONNECTOR.STRAIGHT, x1, y1, x2, y2)
    c.line.color.rgb = cor
    c.line.width = Pt(largura)
    if tracejada:
        c.line.dash_style = MSO_LINE_DASH_STYLE.DASH


def _slide(prs: Any, titulo: str, d: DadosApresentacao) -> Any:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _retangulo(s, 0, 0, LARGURA, Inches(0.08), AZUL)
    _texto(s, MARGEM, Inches(0.3), LARGURA - 2 * MARGEM, Inches(0.7), titulo, 26, True)
    _texto(s, MARGEM, ALTURA - Inches(0.45), LARGURA - 2 * MARGEM, Inches(0.3),
           f"{_limpo(d.projeto.nome_projeto, 80)} · PMO", 9, cor=FRACO)
    return s


def _tabela(slide: Any, x: int, y: int, w: int, linhas: List[List[str]], larguras: Sequence[float],
            cores_celula: Optional[dict] = None) -> None:
    altura_linha = Inches(0.36)
    forma = slide.shapes.add_table(len(linhas), len(larguras), x, y, w, altura_linha * len(linhas))
    tabela = forma.table
    total = sum(larguras)
    for j, fracao in enumerate(larguras):
        tabela.columns[j].width = Emu(int(w * fracao / total))
    for i, linha in enumerate(linhas):
        for j, valor in enumerate(linha):
            celula = tabela.cell(i, j)
            celula.text = valor
            p = celula.text_frame.paragraphs[0]
            for run in p.runs:
                run.font.size = Pt(11 if i else 11)
                run.font.bold = i == 0
                run.font.color.rgb = FRACO if i == 0 else TEXTO
            celula.fill.solid()
            celula.fill.fore_color.rgb = FUNDO if i == 0 else BRANCO
            celula.vertical_anchor = MSO_ANCHOR.MIDDLE
    for (i, j), cor in (cores_celula or {}).items():
        celula = tabela.cell(i, j)
        celula.fill.fore_color.rgb = RGBColor.from_string(cor)
        for run in celula.text_frame.paragraphs[0].runs:
            run.font.color.rgb = BRANCO
            run.font.bold = True


def _cartao(slide: Any, x: int, y: int, w: int, rotulo: str, valor: str, destaque: bool = False) -> None:
    caixa = _retangulo(slide, x, y, w, Inches(1.2), FUNDO, MSO_SHAPE.ROUNDED_RECTANGLE)
    caixa.adjustments[0] = 0.08
    _texto(slide, x + Inches(0.2), y + Inches(0.15), w - Inches(0.4), Inches(0.35), rotulo, 12, cor=FRACO)
    _texto(slide, x + Inches(0.2), y + Inches(0.5), w - Inches(0.4), Inches(0.6), valor, 24, True,
           VERMELHO if destaque else TEXTO)


def _grafico_barras(slide: Any, x: int, y: int, w: int, h: int, categorias: List[str],
                    series: List[Tuple[str, List[float], RGBColor]], horizontal: bool = True,
                    formato: str = "0") -> None:
    dados = CategoryChartData()
    dados.categories = categorias
    for nome, valores, _ in series:
        dados.add_series(nome, valores)
    tipo = XL_CHART_TYPE.BAR_CLUSTERED if horizontal else XL_CHART_TYPE.COLUMN_CLUSTERED
    grafico = slide.shapes.add_chart(tipo, x, y, w, h, dados).chart
    grafico.has_legend = len(series) > 1
    if grafico.has_legend:
        grafico.legend.position = XL_LEGEND_POSITION.TOP
        grafico.legend.include_in_layout = False
        grafico.legend.font.size = Pt(11)
    for i, (_, _, cor) in enumerate(series):
        serie = grafico.plots[0].series[i]
        serie.format.fill.solid()
        serie.format.fill.fore_color.rgb = cor
    grafico.plots[0].gap_width = 60
    eixo = grafico.value_axis
    eixo.has_major_gridlines = True
    eixo.major_gridlines.format.line.color.rgb = BORDA
    eixo.tick_labels.font.size = Pt(10)
    eixo.tick_labels.number_format = formato
    eixo.tick_labels.number_format_is_linked = False
    grafico.category_axis.tick_labels.font.size = Pt(11)
    if horizontal:
        grafico.category_axis.reverse_order = True  # o primeiro da lista no topo


# ---------- Slides ----------


def _capa(prs: Any, d: DadosApresentacao) -> None:
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _retangulo(s, 0, 0, Inches(0.35), ALTURA, AZUL)
    _texto(s, Inches(1.2), Inches(2.2), Inches(11), Inches(1.4), _limpo(d.projeto.nome_projeto, 120), 40, True)
    ind = d.indicadores
    periodo = f"{_data(d.projeto.data_inicio or ind.data_inicio_prevista)} a {_data(ind.data_fim_prevista)}"
    _texto(s, Inches(1.2), Inches(3.6), Inches(11), Inches(1.2),
           f"Status: {d.projeto.status.value}  ·  {periodo}\nResponsável: {_limpo(d.dono, 80)}", 18, cor=FRACO)
    local = d.agora_utc.astimezone(ZoneInfo(settings.fuso_horario))
    _texto(s, Inches(1.2), ALTURA - Inches(1.1), Inches(11), Inches(0.5),
           f"Gerado em {local:%d/%m/%Y %H:%M} por {_limpo(d.gerado_por, 80)}", 12, cor=FRACO)


def _resumo(prs: Any, d: DadosApresentacao) -> None:
    s = _slide(prs, "Resumo", d)
    ind = d.indicadores
    largura = (LARGURA - 2 * MARGEM - Inches(0.6)) // 4
    cartoes = [
        ("Andamento geral", f"{ind.percentual_conclusao:.0f}%", False),
        ("Tarefas concluídas", f"{ind.tarefas_concluidas} de {ind.total_tarefas}", False),
        ("Tarefas atrasadas", str(ind.tarefas_atrasadas), ind.tarefas_atrasadas > 0),
        ("Prazo previsto", _data(ind.data_fim_prevista), False),
    ]
    for i, (rotulo, valor, destaque) in enumerate(cartoes):
        _cartao(s, MARGEM + i * (largura + Inches(0.2)), Inches(1.2), largura, rotulo, valor, destaque)
    if ind.fases:
        _texto(s, MARGEM, Inches(2.65), Inches(8), Inches(0.4), "Andamento por fase (%)", 14, True)
        _grafico_barras(s, MARGEM, Inches(3.05), LARGURA - 2 * MARGEM, Inches(3.8),
                        [_limpo(f.titulo, 40) for f in ind.fases],
                        [("% concluído", [round(f.percentual_conclusao, 1) for f in ind.fases], SERIE_REALIZADO)])


def _linha_do_tempo(prs: Any, d: DadosApresentacao) -> None:
    fases = [f for f in d.fases if f[1] and f[2]]
    marcos = d.marcos
    if not fases and not marcos:
        return
    s = _slide(prs, "Linha do tempo", d)
    inicios = [f[1] for f in fases] + [m.data for m in marcos]
    fins = [f[2] for f in fases] + [m.data_fim or m.data for m in marcos]
    inicio, fim = min(inicios), max(fins) + timedelta(days=1)
    if (fim - inicio).days < 14:
        fim = inicio + timedelta(days=14)
    rotulo = Inches(2.6)
    x0, x1 = MARGEM + rotulo, LARGURA - MARGEM
    topo = Inches(1.3)
    linha_marcos = Inches(0.9) if marcos else 0
    altura_fase = Inches(0.55)
    base = topo + Inches(0.4) + linha_marcos + altura_fase * max(len(fases), 1)

    def x(dia: date) -> int:
        return int(x0 + (x1 - x0) * (dia - inicio).days / (fim - inicio).days)

    # Meses: linha fina e rótulo no topo.
    cursor = date(inicio.year, inicio.month, 1)
    passo = max(1, ((fim - inicio).days // 30) // 12 + 1)
    contador = 0
    while cursor < fim:
        if cursor >= inicio and contador % passo == 0:
            _linha(s, x(cursor), topo + Inches(0.35), x(cursor), base, BORDA, 0.75)
            _texto(s, x(cursor) - Inches(0.4), topo, Inches(0.8), Inches(0.3), f"{MESES[cursor.month - 1]}/{str(cursor.year)[2:]}",
                   10, cor=FRACO, alinhar=PP_ALIGN.CENTER)
        contador += 1
        cursor = date(cursor.year + (cursor.month == 12), cursor.month % 12 + 1, 1)

    y_marcos = topo + Inches(0.45)
    # Faixas (períodos) atrás de tudo.
    for m in marcos:
        if m.forma == FormaMarco.FAIXA and m.data_fim:
            _retangulo(s, x(m.data), y_marcos, x(m.data_fim + timedelta(days=1)) - x(m.data), base - y_marcos,
                       RGBColor.from_string(CLARO_MARCO[m.cor]))
    if marcos:
        _texto(s, MARGEM, y_marcos + Inches(0.1), rotulo, Inches(0.4), "Marcos", 12, True, FRACO)

    for i, (titulo, ini, fim_fase, pct) in enumerate(fases):
        y = topo + Inches(0.4) + linha_marcos + altura_fase * i
        _texto(s, MARGEM, y + Inches(0.08), rotulo - Inches(0.1), Inches(0.4), _limpo(titulo, 30), 12)
        cor = CORES_FASE[i % len(CORES_FASE)]
        barra_x, barra_w = x(ini), max(x(fim_fase + timedelta(days=1)) - x(ini), Inches(0.05))
        fundo = _retangulo(s, barra_x, y + Inches(0.12), barra_w, Inches(0.3), cor, MSO_SHAPE.ROUNDED_RECTANGLE)
        fundo.fill.fore_color.brightness = 0.6
        if pct > 0:
            _retangulo(s, barra_x, y + Inches(0.12), int(barra_w * min(pct, 100) / 100), Inches(0.3), cor, MSO_SHAPE.ROUNDED_RECTANGLE)
        _texto(s, barra_x + barra_w + Inches(0.05), y + Inches(0.08), Inches(0.8), Inches(0.3), f"{pct:.0f}%", 10, cor=FRACO)

    # Marcos pontuais: linha tracejada até o fim das fases + a forma + o título.
    for m in marcos:
        cor = RGBColor.from_string(COR_MARCO[m.cor])
        if m.forma == FormaMarco.FAIXA and m.data_fim:
            _retangulo(s, x(m.data), y_marcos + Inches(0.12), x(m.data_fim + timedelta(days=1)) - x(m.data), Inches(0.14), cor)
            _texto(s, x(m.data), y_marcos + Inches(0.3), Inches(2), Inches(0.3), _limpo(m.titulo, 30), 9, cor=TEXTO)
            continue
        centro = x(m.data) + (x(m.data + timedelta(days=1)) - x(m.data)) // 2
        _linha(s, centro, y_marcos + Inches(0.35), centro, base, cor, 1, tracejada=True)
        lado = Inches(0.26) if m.forma != FormaMarco.PIN else Inches(0.18)
        _retangulo(s, centro - lado // 2, y_marcos + Inches(0.08), lado, lado, cor, FORMA[m.forma])
        _texto(s, centro + Inches(0.12), y_marcos + Inches(0.02), Inches(2.2), Inches(0.3), _limpo(m.titulo, 30), 9, cor=TEXTO)

    if inicio <= d.hoje < fim:
        _linha(s, x(d.hoje), topo + Inches(0.35), x(d.hoje), base, VERMELHO, 1.5, tracejada=True)
        _texto(s, x(d.hoje) - Inches(0.4), base + Inches(0.02), Inches(0.8), Inches(0.3), "hoje", 10, True, VERMELHO, PP_ALIGN.CENTER)
    if not fases:
        _texto(s, MARGEM, base + Inches(0.4), Inches(10), Inches(0.4), "Nenhuma fase com datas ainda: só os marcos aparecem.", 12, cor=FRACO)


def _marcos(prs: Any, d: DadosApresentacao) -> None:
    if not d.marcos:
        return
    s = _slide(prs, "Marcos", d)
    linhas = [["Data", "Marco", "Descrição"]]
    cores = {}
    for i, m in enumerate(d.marcos[:14], start=1):
        quando = _data(m.data) + (f" a {_data(m.data_fim)}" if m.data_fim else "")
        linhas.append([quando, _limpo(m.titulo, 60), _limpo(m.descricao or "—", 120)])
        cores[(i, 0)] = COR_MARCO[m.cor]
    _tabela(s, MARGEM, Inches(1.25), LARGURA - 2 * MARGEM, linhas, [2.2, 3, 7], cores)
    if len(d.marcos) > 14:
        _texto(s, MARGEM, ALTURA - Inches(0.9), Inches(8), Inches(0.3), f"+{len(d.marcos) - 14} marco(s) no sistema.", 11, cor=FRACO)


def _pilares(prs: Any, d: DadosApresentacao) -> None:
    if not d.pilares:
        return
    s = _slide(prs, "Pilares", d)
    linhas = [["Farol", "Pilar", "Andamento", "Tarefas", "Atrasadas", "Orçado", "Realizado", "Situação"]]
    cores = {}
    for i, p in enumerate(d.pilares[:12], start=1):
        linhas.append([NOME_FAROL[p.farol], _limpo(p.nome, 40), f"{p.percentual_conclusao:.0f}%",
                       f"{p.tarefas_concluidas}/{p.total_tarefas}", str(p.tarefas_atrasadas), _moeda(p.valor_orcado),
                       _moeda(p.valor_realizado), _limpo(p.status_texto or "—", 90)])
        cores[(i, 0)] = COR_FAROL[p.farol]
    _tabela(s, MARGEM, Inches(1.25), LARGURA - 2 * MARGEM, linhas, [1.2, 2, 1.3, 1.1, 1.1, 1.8, 1.8, 4], cores)


def _orcamento(prs: Any, d: DadosApresentacao) -> None:
    o = d.orcamento
    if not o.itens:
        return
    s = _slide(prs, "Orçamento", d)
    largura = (LARGURA - 2 * MARGEM - Inches(0.6)) // 4
    cartoes = [("Orçado (itens)", _moeda(o.total_orcado), False), ("Realizado", _moeda(o.total_realizado), False),
               ("Saldo", _moeda(o.saldo), o.saldo < 0),
               ("Previsão ao final", _moeda(o.previsao_final),
                bool(o.previsao_final and o.previsao_final > o.total_orcado * Decimal("1.05")))]
    for i, (rotulo, valor, destaque) in enumerate(cartoes):
        _cartao(s, MARGEM + i * (largura + Inches(0.2)), Inches(1.2), largura, rotulo, valor, destaque)
    itens = sorted(o.itens, key=lambda i: -i.valor_orcado)[:10]
    _grafico_barras(s, MARGEM, Inches(2.6), Inches(7.6), Inches(4.3), [_limpo(i.descricao, 35) for i in itens], [
        ("Orçado", [float(i.valor_orcado) for i in itens], SERIE_PLANEJADO),
        ("Realizado", [float(i.valor_realizado) for i in itens], SERIE_REALIZADO),
    ], formato='"R$" #,##0')
    alertas = [a.mensagem for a in o.alertas][:5] or ["Nenhum alerta: execução dentro do orçado."]
    _texto(s, MARGEM + Inches(7.9), Inches(2.6), Inches(4.2), Inches(0.4), "Alertas", 14, True)
    _texto(s, MARGEM + Inches(7.9), Inches(3.05), Inches(4.2), Inches(3.8), "\n".join(f"• {_limpo(a, 160)}" for a in alertas), 11)


def _gargalos(prs: Any, d: DadosApresentacao) -> None:
    g = d.gargalos
    if not g.piores:
        return
    s = _slide(prs, "Gargalos: onde o prazo escapou", d)
    pessoas = [p for p in g.por_responsavel if p.dias_atraso_entregues + p.dias_atraso_abertas > 0][:8]
    if pessoas:
        _texto(s, MARGEM, Inches(1.15), Inches(6), Inches(0.4), "Dias de atraso por responsável", 14, True)
        _grafico_barras(s, MARGEM, Inches(1.55), Inches(6.2), Inches(5.3), [_limpo(p.nome, 30) for p in pessoas], [
            ("Entregues com atraso", [p.dias_atraso_entregues for p in pessoas], SERIE_REALIZADO),
            ("Ainda atrasadas", [p.dias_atraso_abertas for p in pessoas], SERIE_PLANEJADO),
        ])
    areas = [a for a in g.por_pilar if a.dias_atraso_entregues + a.dias_atraso_abertas > 0][:4]
    linhas = [["Tarefa", "Responsável", "Atraso"]] + [
        [_limpo(t.descricao, 45), _limpo(t.responsavel or "—", 25), f"{t.dias_atraso} d"] for t in g.piores[:8]]
    x = MARGEM + Inches(6.5)
    _texto(s, x, Inches(1.15), Inches(5.6), Inches(0.4), "Maiores atrasos", 14, True)
    _tabela(s, x, Inches(1.55), Inches(5.6), linhas, [3.4, 1.6, 0.8])
    if areas:
        resumo = "Áreas com mais atraso: " + ", ".join(
            f"{_limpo(a.nome, 25)} ({a.dias_atraso_entregues + a.dias_atraso_abertas} d)" for a in areas)
        _texto(s, x, ALTURA - Inches(1.2), Inches(5.6), Inches(0.6), resumo, 11, cor=FRACO)


def _atencao(prs: Any, d: DadosApresentacao) -> None:
    proximos = [m for m in d.marcos if m.data >= d.hoje][:6]
    if not d.atrasadas and not proximos:
        return
    s = _slide(prs, "Pontos de atenção", d)
    if d.atrasadas:
        _texto(s, MARGEM, Inches(1.15), Inches(6), Inches(0.4), "Tarefas atrasadas", 14, True, VERMELHO)
        linhas = [["Tarefa", "Responsável", "Prazo", "Atraso"]] + [
            [_limpo(t, 45), _limpo(r or "—", 22), _data(p), f"{dias} d"] for t, r, p, dias in d.atrasadas[:10]]
        _tabela(s, MARGEM, Inches(1.55), Inches(7), linhas, [3.6, 1.8, 1.2, 0.8])
    if proximos:
        x = MARGEM + Inches(7.4)
        _texto(s, x, Inches(1.15), Inches(4.7), Inches(0.4), "Próximos marcos", 14, True)
        _texto(s, x, Inches(1.6), Inches(4.7), Inches(4.5),
               "\n".join(f"{_data(m.data)} · {_limpo(m.titulo, 50)}" for m in proximos), 13)


def gerar_pptx(d: DadosApresentacao) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = LARGURA, ALTURA
    prs.core_properties.title = _limpo(f"Apresentação - {d.projeto.nome_projeto}", 250)
    prs.core_properties.author = _limpo(d.gerado_por, 250)
    for montar in (_capa, _resumo, _linha_do_tempo, _marcos, _pilares, _orcamento, _gargalos, _atencao):
        montar(prs, d)
    buffer = io.BytesIO()
    prs.save(buffer)
    return buffer.getvalue()
