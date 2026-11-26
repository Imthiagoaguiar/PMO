"""
Modelo de apresentação da empresa (.pptx ou .potx): validação, normalização e uso dos layouts.

Ao receber o arquivo (só admin):
- é um ZIP de PowerPoint de verdade, sem "bomba" (limite de arquivos e de tamanho descompactado);
- modelos com macro (.pptm/.potm, vbaProject) são recusados: macro é código;
- .potx vira .pptx (troca o tipo do documento principal; o python-pptx só abre apresentações);
- os slides de exemplo do modelo saem: fica só o "molde" (tema, layouts, logotipo).

Ao gerar: usa os layouts de capa, "só título" e "título e conteúdo" do modelo; o conteúdo do PMO é
reposicionado na área livre entre o título e o rodapé do layout (app/apresentacao.py, Montagem).
"""

import io
import zipfile
from pathlib import Path
from typing import Any, List, Optional, Tuple

from pptx import Presentation
from pptx.enum.shapes import PP_PLACEHOLDER
from pptx.util import Emu, Pt

from app.config import settings

MAX_ARQUIVOS_NO_ZIP = 3000
MAX_DESCOMPACTADO = 300 * 1024 * 1024
TIPO_MODELO = "application/vnd.openxmlformats-officedocument.presentationml.template.main+xml"
TIPO_APRESENTACAO = "application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml"
TITULOS = {PP_PLACEHOLDER.TITLE, PP_PLACEHOLDER.CENTER_TITLE}
CORPO = {PP_PLACEHOLDER.BODY, PP_PLACEHOLDER.OBJECT, PP_PLACEHOLDER.SUBTITLE}
RODAPE = {PP_PLACEHOLDER.DATE, PP_PLACEHOLDER.FOOTER, PP_PLACEHOLDER.SLIDE_NUMBER}


class ModeloInvalido(Exception):
    pass


def pasta() -> Path:
    return Path(settings.anexos_dir) / "modelos"


def caminho(chave: str) -> Path:
    if not chave.isalnum():
        raise ValueError("chave de modelo inválida")
    return pasta() / f"{chave}.pptx"


def remover_slides(prs: Any) -> None:
    lista = prs.slides._sldIdLst
    for sld in list(lista):
        prs.part.drop_rel(sld.rId)
        lista.remove(sld)


def normalizar(conteudo: bytes) -> Tuple[bytes, int, int]:
    """Valida o arquivo e devolve (pptx limpo, largura, altura). Levanta ModeloInvalido com a explicação."""
    try:
        zip_in = zipfile.ZipFile(io.BytesIO(conteudo))
    except zipfile.BadZipFile:
        raise ModeloInvalido("O arquivo não é um PowerPoint (.pptx ou .potx).")
    infos = zip_in.infolist()
    nomes = {i.filename for i in infos}
    if len(infos) > MAX_ARQUIVOS_NO_ZIP or sum(i.file_size for i in infos) > MAX_DESCOMPACTADO:
        raise ModeloInvalido("O modelo é grande demais depois de descompactado.")
    if "ppt/presentation.xml" not in nomes or "[Content_Types].xml" not in nomes:
        raise ModeloInvalido("O arquivo não é um PowerPoint (.pptx ou .potx).")
    if any("vbaProject" in n for n in nomes):
        raise ModeloInvalido("Modelos com macro (.pptm/.potm) não são aceitos. Salve como .pptx ou .potx.")

    saida = io.BytesIO()
    with zipfile.ZipFile(saida, "w", zipfile.ZIP_DEFLATED) as zip_out:
        for info in infos:
            dados = zip_in.read(info.filename)
            if info.filename == "[Content_Types].xml":
                dados = dados.replace(TIPO_MODELO.encode(), TIPO_APRESENTACAO.encode())
            zip_out.writestr(info, dados)
    try:
        prs = Presentation(io.BytesIO(saida.getvalue()))
    except Exception:
        raise ModeloInvalido("Não foi possível abrir o modelo. Salve-o de novo no PowerPoint como .pptx ou .potx.")
    if not len(prs.slide_layouts):
        raise ModeloInvalido("O modelo não tem layouts de slide.")
    remover_slides(prs)
    limpo = io.BytesIO()
    prs.save(limpo)
    return limpo.getvalue(), int(prs.slide_width), int(prs.slide_height)


def _tipos(layout: Any) -> List[Any]:
    tipos = []
    for ph in layout.placeholders:
        try:
            tipos.append(ph.placeholder_format.type)
        except ValueError:
            continue
    return tipos


def layout_com(prs: Any, uso: str) -> Any:
    """'capa' (título + subtítulo), 'titulo' (só o título) ou 'texto' (título + corpo). Com reservas."""
    layouts = list(prs.slide_layouts)
    com_titulo = [lay for lay in layouts if TITULOS & set(_tipos(lay))]
    if uso == "capa":
        for lay in layouts:
            if PP_PLACEHOLDER.CENTER_TITLE in _tipos(lay):
                return lay
        return next((lay for lay in com_titulo if PP_PLACEHOLDER.SUBTITLE in _tipos(lay)), layouts[0])
    if uso == "texto":
        return next((lay for lay in com_titulo if CORPO & set(_tipos(lay)) and PP_PLACEHOLDER.SUBTITLE not in _tipos(lay)),
                    layout_com(prs, "titulo"))
    so_titulo = [lay for lay in com_titulo if set(_tipos(lay)) - RODAPE <= TITULOS]
    if so_titulo:
        return so_titulo[0]
    return min(com_titulo, key=lambda lay: len(_tipos(lay))) if com_titulo else layouts[-1]


def preencher(slide: Any, titulo: str, linhas: List[str]) -> None:
    """Título no espaço de título do layout; linhas no primeiro espaço de corpo/subtítulo; o resto vazio sai."""
    usados = set()
    for ph in slide.placeholders:
        tipo = ph.placeholder_format.type
        if tipo in TITULOS and "titulo" not in usados:
            ph.text_frame.text = titulo
            usados.add("titulo")
        elif tipo in CORPO and linhas and "corpo" not in usados:
            quadro = ph.text_frame
            quadro.text = linhas[0]
            for linha in linhas[1:]:
                quadro.add_paragraph().text = linha
            usados.add("corpo")
        else:
            ph._element.getparent().remove(ph._element)
    if "titulo" not in usados:  # layout sem espaço de título: um texto no topo
        largura = int(slide.part.package.presentation_part.presentation.slide_width)
        caixa = slide.shapes.add_textbox(Emu(int(largura * 0.05)), Emu(int(largura * 0.02)), Emu(int(largura * 0.9)), Pt(48))
        caixa.text_frame.text = titulo
        caixa.text_frame.paragraphs[0].runs[0].font.size = Pt(28)


def area_de_conteudo(prs: Any, slide: Any) -> Tuple[int, int, int, int]:
    """(x0, y0, x1, y1): abaixo do título do slide, acima do rodapé do layout, nas margens do título."""
    largura, altura = int(prs.slide_width), int(prs.slide_height)
    titulo: Optional[Any] = slide.shapes.title
    x0, x1 = int(largura * 0.05), int(largura * 0.95)
    y0 = int(altura * 0.18)
    if titulo is not None and titulo.width:
        x0, x1 = int(titulo.left), int(titulo.left + titulo.width)
        y0 = int(titulo.top + titulo.height + altura * 0.02)
    rodapes = [int(ph.top) for ph in slide.slide_layout.placeholders
               if ph.placeholder_format.type in RODAPE and ph.top is not None and ph.top > y0]
    y1 = min(rodapes) - int(altura * 0.01) if rodapes else int(altura * 0.94)
    if y1 - y0 < altura * 0.4:  # layout esquisito: usa uma área segura
        y0, y1 = int(altura * 0.18), int(altura * 0.94)
    return x0, y0, x1, y1
