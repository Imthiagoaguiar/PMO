"""Regras de negócio de criação de projetos (independentes do FastAPI)."""

import io
import logging
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Tuple

import openpyxl
from openpyxl.cell import WriteOnlyCell
import pandas as pd
from pydantic import ValidationError
from sqlalchemy import and_, case, func, insert, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from app import auditoria, tempo
from app.config import settings
from app.integracoes.feriados import calcular_data_fim, proximo_dia_util
from app.models import (
    FASES_WATERFALL,
    MAX_FASES,
    MAX_PILARES,
    PILARES_PADRAO,
    Fase,
    ItemOrcamento,
    Pilar,
    PapelProjeto,
    PapelUsuario,
    Projeto,
    ProjetoMembro,
    ProvedorAuth,
    RefreshToken,
    Tarefa,
    Usuario,
    chave_da_fase,
    chave_do_pilar,
)
from app.schemas import FaseIndicadores, FaseNova, PilarRead, ProjetoIndicadores, TarefaPlanilha, UsuarioResumo
from app.security import agora_utc
from app.texto import normalizar

if TYPE_CHECKING:
    from app.integracoes.clerk import IdentidadeClerk

logger = logging.getLogger(__name__)

# A planilha é processada pelo worker da fila (app/importacao.py), não dentro da requisição.
MAX_LINHAS = settings.importacao_max_linhas
LOTE_DE_INSERCAO = 2_000
TAMANHO_TITULO_FASE = 50  # = Fase.titulo
# .xlsx é um ZIP: um arquivo pequeno pode "explodir" ao descompactar (zip bomb).
MAX_XLSX_DESCOMPACTADO = 400 * 1024 * 1024  # 100 mil linhas cabem com folga

# Cabeçalhos aceitos na planilha (já normalizados) -> campo interno.
ALIASES_COLUNAS: Dict[str, str] = {
    "fase": "fase",
    "descricao": "descricao",
    "tarefa": "descricao",
    "data_inicio": "data_inicio",
    "inicio": "data_inicio",
    "data_fim": "data_fim",
    "fim": "data_fim",
    "data_termino": "data_fim",
    "responsavel": "responsavel",
    "percentual_conclusao": "percentual_conclusao",
    "percentual": "percentual_conclusao",
    "%_conclusao": "percentual_conclusao",
    "conclusao": "percentual_conclusao",
    "pilar": "pilar",
    "frente": "pilar",
    "frente_de_trabalho": "pilar",
    "workstream": "pilar",
}
CAMPOS_TAREFA = tuple(TarefaPlanilha.model_fields)
COLUNAS_OBRIGATORIAS = {"fase", "descricao"}


class PlanilhaInvalida(Exception):
    def __init__(self, mensagem: str, erros: Optional[List[str]] = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.erros = erros or []


# ---------- Modo 1 e base do Modo 2 ----------


def criar_projeto_com_fases(
    db: Session,
    dono: Usuario,
    nome_projeto: str,
    orcamento_aprovado: Optional[Decimal] = None,
    data_inicio: Optional[date] = None,
    modelo_fases: str = "waterfall",
) -> Projeto:
    """
    Cria o projeto com as 5 fases do Waterfall (modelo "waterfall") ou sem fases ("em_branco");
    o criador vira dono e membro. Não faz commit (responsabilidade de quem chama).
    """
    projeto = Projeto(
        nome_projeto=nome_projeto,
        orcamento_aprovado=orcamento_aprovado,
        data_inicio=data_inicio,
        dono=dono,
    )
    titulos = FASES_WATERFALL if modelo_fases == "waterfall" else ()
    projeto.fases = [Fase(titulo=titulo, ordem_sequencia=ordem) for ordem, titulo in enumerate(titulos, start=1)]
    projeto.membros = [ProjetoMembro(usuario=dono, papel=PapelProjeto.EDITOR)]
    projeto.pilares = [Pilar(nome=nome, ordem=i) for i, nome in enumerate(PILARES_PADRAO)]
    db.add(projeto)
    return projeto


def titulo_de_fase(texto: str) -> str:
    """Espaços repetidos viram um só; o tamanho é validado por quem chama."""
    return " ".join(texto.split())


def garantir_fases(projeto: Projeto, titulos: List[str]) -> Dict[str, Fase]:
    """
    {chave: fase} com as fases que o projeto já tem + as que faltam (criadas no fim, na ordem dada).
    "execucao" encontra "Execução": a comparação é sem acento, caixa e espaços.
    """
    por_chave = {f.titulo_chave: f for f in projeto.fases}
    proxima = max((f.ordem_sequencia for f in projeto.fases), default=0) + 1
    for titulo in titulos:
        chave = chave_da_fase(titulo)
        if chave not in por_chave:
            fase = Fase(titulo=titulo_de_fase(titulo), ordem_sequencia=proxima)
            projeto.fases.append(fase)
            por_chave[chave] = fase
            proxima += 1
    return por_chave


def adicionar_cronograma(projeto: Projeto, fases: List[FaseNova], inicio: Optional[date]) -> int:
    """
    Distribui as tarefas nas fases (criando as que o projeto ainda não tem) e encadeia as datas na
    ordem das fases: cada tarefa com duração começa no dia útil seguinte ao fim da anterior (pulando
    fins de semana e feriados). Tarefas sem duração entram sem datas e não empurram o cronograma.
    """
    por_chave = garantir_fases(projeto, [f.fase for f in fases])
    posicao = {f.titulo_chave: f.ordem_sequencia for f in projeto.fases}
    cursor = inicio
    total = 0
    for fase in sorted(fases, key=lambda f: posicao[chave_da_fase(f.fase)]):
        for nova in fase.tarefas:
            tarefa = Tarefa(descricao=nova.descricao, responsavel=nova.responsavel)
            if nova.duracao_dias_uteis is not None and cursor is not None:
                tarefa.data_inicio = proximo_dia_util(cursor)
                tarefa.data_fim = calcular_data_fim(tarefa.data_inicio, nova.duracao_dias_uteis)
                cursor = tarefa.data_fim + timedelta(days=1)
            por_chave[chave_da_fase(fase.fase)].tarefas.append(tarefa)
            total += 1
    return total


def derrubar_sessoes(db: Session, usuario: Usuario) -> None:
    """Revoga os refresh tokens e invalida na hora os access tokens já emitidos (versão da sessão)."""
    usuario.versao_sessao += 1
    revogar_sessoes(db, usuario.id)


def revogar_sessoes(db: Session, usuario_id: int) -> None:
    """Invalida todos os refresh tokens ativos do usuário (logout em todos os dispositivos)."""
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.usuario_id == usuario_id, RefreshToken.revogado_em.is_(None))
        .values(revogado_em=agora_utc())
    )


# ---------- Modo 2: planilha ----------


def ler_planilha(conteudo: bytes, nome_arquivo: str) -> pd.DataFrame:
    extensao = Path(nome_arquivo).suffix.lower()
    if extensao not in (".csv", ".xlsx"):
        raise PlanilhaInvalida("Formato não suportado. Envie um arquivo .csv ou .xlsx.")

    try:
        if extensao == ".csv":
            df = _ler_csv(conteudo)
        else:
            _verificar_xlsx(conteudo)
            df = _ler_xlsx(conteudo)
    except PlanilhaInvalida:
        raise
    except Exception as exc:  # pandas/openpyxl levantam vários tipos para arquivo corrompido
        # O detalhe vai para o log, não para o cliente (não expomos detalhes internos).
        logger.warning("Falha ao ler planilha '%s': %s", nome_arquivo, exc)
        raise PlanilhaInvalida(
            "Não foi possível ler o arquivo. Verifique se é uma planilha válida."
        ) from exc

    originais = [str(c) for c in df.columns]
    df.columns = [ALIASES_COLUNAS.get(normalizar(c).replace(" ", "_"), c) for c in originais]
    repetidas = sorted({c for c in df.columns if list(df.columns).count(c) > 1})
    if repetidas:
        # Ex.: "Tarefa" e "Descrição" são a mesma coluna; ficar com uma delas em silêncio perderia dados.
        grupos = [" e ".join(f"'{o}'" for o, c in zip(originais, df.columns) if c == r) for r in repetidas]
        raise PlanilhaInvalida(f"Colunas repetidas na planilha: {'; '.join(grupos)}. Deixe só uma de cada.")
    faltando = COLUNAS_OBRIGATORIAS - set(df.columns)
    if faltando:
        raise PlanilhaInvalida(
            f"Colunas obrigatórias ausentes: {', '.join(sorted(faltando))}."
        )

    df = df.dropna(how="all")
    if df.empty:
        raise PlanilhaInvalida("A planilha não contém tarefas.")
    if len(df) > MAX_LINHAS:
        raise PlanilhaInvalida(f"A planilha excede o limite de {MAX_LINHAS:,} linhas.".replace(",", "."))

    # NaN/NaT -> None, para o Pydantic tratar células vazias como ausentes.
    return df.astype(object).where(pd.notna(df), None)


def _verificar_xlsx(conteudo: bytes) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(conteudo)) as arquivo_zip:
            total = sum(item.file_size for item in arquivo_zip.infolist())
    except zipfile.BadZipFile:
        raise PlanilhaInvalida("Arquivo .xlsx inválido ou corrompido.")
    if total > MAX_XLSX_DESCOMPACTADO:
        raise PlanilhaInvalida("O conteúdo do .xlsx é grande demais para importação.")


def _ler_xlsx(conteudo: bytes) -> pd.DataFrame:
    """
    Lê a primeira aba direto pelo openpyxl (e não pelo pandas) para enxergar o formato das células:
    50% numa célula com formato de porcentagem é guardado como 0,5 e precisa virar 50.
    """
    livro = openpyxl.load_workbook(io.BytesIO(conteudo), read_only=True, data_only=True)
    try:
        linhas = [[_valor_celula(c) for c in linha] for linha in livro.worksheets[0].iter_rows()]
    finally:
        livro.close()
    if not linhas:
        return pd.DataFrame()
    cabecalho = linhas[0]
    while cabecalho and cabecalho[-1] is None:  # colunas vazias à direita
        cabecalho = cabecalho[:-1]
    colunas = [str(c) if c is not None else f"coluna_{i + 1}" for i, c in enumerate(cabecalho)]
    dados = [(linha + [None] * len(colunas))[: len(colunas)] for linha in linhas[1:]]
    return pd.DataFrame(dados, columns=colunas, dtype=object)


def _valor_celula(celula: Any) -> Any:
    valor = celula.value
    numero = isinstance(valor, (int, float)) and not isinstance(valor, bool)
    if numero and "%" in (getattr(celula, "number_format", None) or ""):
        return valor * 100
    return valor


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    # sep=None detecta ',' ou ';' (Excel em pt-BR exporta com ';').
    # dtype=str evita que o pandas "adivinhe" datas e números; o Pydantic faz a conversão.
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            # Só célula vazia é "vazio": por padrão o pandas também apaga "NA", "null", "None"...
            # e uma tarefa chamada "NA" sumiria.
            return pd.read_csv(
                io.BytesIO(conteudo), sep=None, engine="python", dtype=str, encoding=encoding,
                keep_default_na=False, na_values=[""],
                # Linhas em branco viram linhas vazias (descartadas depois pelo dropna) em vez de
                # sumir: senão o "Linha N" do erro aponta para a linha errada da planilha.
                skip_blank_lines=False,
            )
        except UnicodeDecodeError:
            continue
    raise PlanilhaInvalida("Codificação do CSV não reconhecida. Salve como UTF-8.")


def validar_tarefas(df: pd.DataFrame) -> List[Tuple[str, TarefaPlanilha]]:
    """
    Valida todas as linhas e retorna (chave_da_fase, tarefa).
    Acumula os erros de todas as linhas para o usuário corrigir a planilha de uma vez só.
    A coluna Fase aceita os nomes do Waterfall e também fases próprias (criadas na ordem em que aparecem).
    """
    validas: List[Tuple[str, TarefaPlanilha]] = []
    erros: List[str] = []
    padrao = {chave_da_fase(t) for t in FASES_WATERFALL}
    novas: Dict[str, str] = {}  # chave -> título, na ordem em que aparecem

    for indice, registro in zip(df.index, df.to_dict("records")):
        linha = int(indice) + 2  # +1 do cabeçalho, +1 porque a planilha começa em 1

        try:
            tarefa = TarefaPlanilha.model_validate({c: registro.get(c) for c in CAMPOS_TAREFA})
        except ValidationError as exc:
            erros.extend(f"Linha {linha}: {_formatar_erro(e)}" for e in exc.errors())
            continue

        titulo = titulo_de_fase(tarefa.fase)
        chave = chave_da_fase(titulo)
        if not chave or len(titulo) > TAMANHO_TITULO_FASE:
            erros.append(f"Linha {linha}: nome de fase inválido (de 1 a {TAMANHO_TITULO_FASE} caracteres).")
            continue
        if chave not in padrao and chave not in novas:
            novas[chave] = titulo
        tarefa.fase = titulo
        validas.append((chave, tarefa))

    if erros:
        raise PlanilhaInvalida("A planilha contém erros de validação.", erros)
    if len(padrao) + len(novas) > MAX_FASES:
        raise PlanilhaInvalida(
            f"A planilha tem {len(novas)} fases além das 5 do Waterfall; o limite é {MAX_FASES} fases por projeto."
        )

    pilares_padrao = {chave_do_pilar(n) for n in PILARES_PADRAO}
    pilares_novos = {chave_do_pilar(t.pilar) for _, t in validas if t.pilar} - pilares_padrao
    if len(pilares_padrao) + len(pilares_novos) > MAX_PILARES:
        raise PlanilhaInvalida(
            f"A planilha tem {len(pilares_novos)} pilares além dos padrão; o limite é {MAX_PILARES} pilares por projeto."
        )
    return validas


def gravar_tarefas_em_lote(
    db: Session, projeto: Projeto, tarefas: List[Tuple[str, TarefaPlanilha]],
    ao_progredir: Optional[Callable[[int, int], None]] = None,
) -> None:
    """
    Grava as tarefas da planilha com INSERT em lote (milhares por comando), em vez de um objeto do ORM
    por linha: 100 mil linhas cabem em segundos. Espera o projeto já com id (flush).
    Coluna Pilar: "ti" casa com "TI"; um nome que o projeto não tem vira um pilar novo.
    """
    # Fases da planilha que o projeto ainda não tem: criadas no fim, na ordem em que aparecem.
    garantir_fases(projeto, list(dict.fromkeys(dados.fase for _, dados in tarefas)))
    pilares = {chave_do_pilar(p.nome): p for p in projeto.pilares}
    for _, dados in tarefas:
        if dados.pilar and chave_do_pilar(dados.pilar) not in pilares:
            pilar = Pilar(nome=" ".join(dados.pilar.split())[:60], ordem=len(pilares))
            pilares[chave_do_pilar(dados.pilar)] = pilar
            projeto.pilares.append(pilar)
    db.flush()  # ids das fases e dos pilares novos
    fases_por_chave = {f.titulo_chave: f.id for f in projeto.fases}

    linhas = [
        {
            **dados.model_dump(exclude={"fase", "pilar"}),
            "fase_id": fases_por_chave[chave],
            "pilar_id": pilares[chave_do_pilar(dados.pilar)].id if dados.pilar else None,
            "versao": 1,
        }
        for chave, dados in tarefas
    ]
    for inicio in range(0, len(linhas), LOTE_DE_INSERCAO):
        db.execute(insert(Tarefa), linhas[inicio: inicio + LOTE_DE_INSERCAO])
        if ao_progredir:
            ao_progredir(min(inicio + LOTE_DE_INSERCAO, len(linhas)), len(linhas))


def _formatar_erro(erro: dict) -> str:
    campo = ".".join(str(p) for p in erro["loc"])
    mensagem = erro["msg"].removeprefix("Value error, ")
    return f"{campo}: {mensagem}" if campo else mensagem


# ---------- Indicadores ----------


def _agregados(db: Session, agrupar_por: Any, filtro: Any, hoje: date) -> Dict[Any, Dict[str, Any]]:
    """Contas por grupo direto no SQL: não carrega as tarefas (100 mil tarefas = 1 query, não 100 mil objetos)."""
    atrasada = case((and_(Tarefa.data_fim < hoje, Tarefa.percentual_conclusao < 100), 1), else_=0)
    linhas = db.execute(
        select(
            agrupar_por,
            func.count(Tarefa.id),
            func.coalesce(func.sum(case((Tarefa.percentual_conclusao == 100, 1), else_=0)), 0),
            func.coalesce(func.sum(atrasada), 0),
            func.coalesce(func.sum(Tarefa.percentual_conclusao), 0),
            func.min(Tarefa.data_inicio),
            func.max(Tarefa.data_fim),
        ).join(Fase, Fase.id == Tarefa.fase_id).where(filtro).group_by(agrupar_por)
    ).all()
    return {
        chave: {"total": int(total), "concluidas": int(concl), "atrasadas": int(atras), "soma": int(soma),
                "inicio": ini_, "fim": fim_}
        for chave, total, concl, atras, soma, ini_, fim_ in linhas
    }


def _media(soma: int, total: int) -> float:
    return round(soma / total, 1) if total else 0.0


def andamento_por_projeto(db: Session, ids: List[int]) -> Dict[int, float]:
    """{projeto_id: % geral (média das tarefas)} de vários projetos numa consulta só (lista de projetos)."""
    if not ids:
        return {}
    return {
        projeto_id: _media(int(soma or 0), int(total))
        for projeto_id, total, soma in db.execute(
            select(Fase.projeto_id, func.count(Tarefa.id), func.sum(Tarefa.percentual_conclusao))
            .join(Fase, Fase.id == Tarefa.fase_id).where(Fase.projeto_id.in_(ids)).group_by(Fase.projeto_id)
        )
    }


def calcular_indicadores(db: Session, projeto: Projeto, hoje: Optional[date] = None) -> ProjetoIndicadores:
    """Indicadores do painel, calculados no banco (servem para projetos de 100 mil tarefas)."""
    hoje = hoje or tempo.hoje()
    por_fase = _agregados(db, Tarefa.fase_id, Fase.projeto_id == projeto.id, hoje)
    vazio = {"total": 0, "concluidas": 0, "atrasadas": 0, "soma": 0, "inicio": None, "fim": None}
    fases = []
    for f in projeto.fases:
        a = por_fase.get(f.id, vazio)
        fases.append(FaseIndicadores(
            fase_id=f.id, titulo=f.titulo, ordem_sequencia=f.ordem_sequencia, total_tarefas=a["total"],
            tarefas_concluidas=a["concluidas"], tarefas_atrasadas=a["atrasadas"],
            percentual_conclusao=_media(a["soma"], a["total"]),
        ))
    total = sum(a["total"] for a in por_fase.values())
    inicios = [a["inicio"] for a in por_fase.values() if a["inicio"]]
    fins = [a["fim"] for a in por_fase.values() if a["fim"]]
    # Seguindo a ordem (Waterfall): a fase atual é a primeira, na ordem, com trabalho pendente.
    # Sem seguir a ordem, as fases andam em paralelo e não existe "fase atual".
    fase_atual = next(
        (f.titulo for f in projeto.fases
         if (a := por_fase.get(f.id)) and a["concluidas"] < a["total"]),
        None,
    ) if projeto.seguir_ordem_fases else None
    return ProjetoIndicadores(
        projeto_id=projeto.id,
        percentual_conclusao=_media(sum(a["soma"] for a in por_fase.values()), total),
        total_tarefas=total,
        tarefas_concluidas=sum(f.tarefas_concluidas for f in fases),
        tarefas_atrasadas=sum(f.tarefas_atrasadas for f in fases),
        data_inicio_prevista=min(inicios) if inicios else None,
        data_fim_prevista=max(fins) if fins else None,
        fase_atual=fase_atual,
        fases=fases,
    )


def listar_pilares(db: Session, projeto_id: int) -> List[Pilar]:
    """Pilares do projeto na ordem do editor, já com quem atualizou o status (sem N+1)."""
    return list(db.scalars(
        select(Pilar).where(Pilar.projeto_id == projeto_id)
        .options(selectinload(Pilar.status_atualizado_por))
        .order_by(Pilar.ordem, Pilar.id)
    ))


def calcular_pilares(db: Session, projeto: Projeto, pilares: List[Pilar], hoje: Optional[date] = None) -> List[PilarRead]:
    """Andamento de cada pilar pelas tarefas dele (contas no banco) + farol e financeiro."""
    hoje = hoje or tempo.hoje()
    por_pilar = _agregados(db, Tarefa.pilar_id, and_(Fase.projeto_id == projeto.id, Tarefa.pilar_id.is_not(None)), hoje)
    vazio = {"total": 0, "concluidas": 0, "atrasadas": 0, "soma": 0}
    from app import orcamento  # importa services: aqui dentro evita o ciclo

    do_orcamento = orcamento.financeiro_por_pilar(db, projeto.id)
    resultado = []
    for p in pilares:
        a = por_pilar.get(p.id, vazio)
        # Pilar com itens na aba Orçamento: o financeiro vem de lá (itens x lançamentos), não do campo manual.
        orcado, realizado = do_orcamento.get(p.id, (p.valor_orcado, p.valor_realizado))
        resultado.append(PilarRead(
            id=p.id, nome=p.nome, ordem=p.ordem, farol=p.farol, status_texto=p.status_texto,
            status_atualizado_em=p.status_atualizado_em,
            status_atualizado_por=UsuarioResumo.model_validate(p.status_atualizado_por) if p.status_atualizado_por else None,
            valor_orcado=orcado, valor_realizado=realizado,
            desvio=realizado - orcado if orcado is not None and realizado is not None else None,
            execucao_financeira=(
                round(float(realizado) / float(orcado) * 100, 1) if orcado and realizado is not None else None
            ),
            financeiro_do_orcamento=p.id in do_orcamento,
            total_tarefas=a["total"], tarefas_concluidas=a["concluidas"], tarefas_atrasadas=a["atrasadas"],
            percentual_conclusao=_media(a["soma"], a["total"]),
            versao=p.versao,
        ))
    return resultado


LIMITE_TAREFAS_POR_FASE = 200  # no detalhe do projeto; o resto vem por GET /projetos/{id}/tarefas


def tarefas_por_fase(db: Session, projeto: Projeto, limite: int = LIMITE_TAREFAS_POR_FASE) -> Dict[int, Tuple[int, List[Tarefa]]]:
    """
    {fase_id: (total, primeiras `limite` tarefas)} em 2 queries fixas, qualquer que seja o tamanho:
    a contagem por fase e as primeiras de cada fase (ROW_NUMBER() OVER (PARTITION BY fase)).
    """
    totais = dict(db.execute(
        select(Tarefa.fase_id, func.count()).join(Fase).where(Fase.projeto_id == projeto.id).group_by(Tarefa.fase_id)
    ).all())
    numeradas = (
        select(Tarefa.id, func.row_number().over(partition_by=Tarefa.fase_id, order_by=Tarefa.id).label("posicao"))
        .join(Fase).where(Fase.projeto_id == projeto.id).subquery()
    )
    primeiras: Dict[int, List[Tarefa]] = {f.id: [] for f in projeto.fases}
    for tarefa in db.scalars(
        select(Tarefa).join(numeradas, numeradas.c.id == Tarefa.id).where(numeradas.c.posicao <= limite).order_by(Tarefa.id)
    ):
        primeiras.setdefault(tarefa.fase_id, []).append(tarefa)
    return {f.id: (totais.get(f.id, 0), primeiras.get(f.id, [])) for f in projeto.fases}


# ---------- Exportação ----------

COLUNAS_EXPORTACAO = ["Fase", "Descrição", "Data Início", "Data Fim", "Responsável", "% Conclusão", "Pilar"]
# Texto que começa com estes caracteres pode ser interpretado como fórmula (Formula Injection).
CARACTERES_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


LARGURAS_EXPORTACAO = (16, 60, 14, 14, 28, 14, 18)
LOTE_EXPORTACAO = 5000  # linhas lidas do banco por vez: a memória não cresce com o tamanho do projeto


def celula_texto_seguro(planilha: Any, valor: Optional[str]) -> Any:
    """
    Grava como TEXTO, sem alterar o conteúdo, o que poderia virar fórmula (Formula Injection):
    o Excel mostra "=SOMA(A1)" em vez de calcular, e "- revisar contrato" volta igual na
    reimportação (o antigo prefixo ' sujava o texto). quotePrefix mantém a célula como texto
    mesmo se a pessoa a editar no Excel. Texto comum vai direto, sem custo de célula estilizada.
    """
    if not (isinstance(valor, str) and valor.startswith(CARACTERES_DE_FORMULA)):
        return valor
    celula = WriteOnlyCell(planilha, value=valor)
    celula.data_type = "s"
    celula.quotePrefix = True
    return celula


def celula_data(planilha: Any, valor: Optional[date]) -> Any:
    if valor is None:
        return None
    celula = WriteOnlyCell(planilha, value=valor)
    celula.number_format = "DD/MM/YYYY"
    return celula


def exportar_planilha(db: Session, projeto: Projeto) -> bytes:
    """
    Gera um .xlsx no mesmo formato aceito pela importação (dá para editar e reimportar). Com orçamento, as abas
    seguintes trazem a parte financeira (resumo CAPEX/OPEX, itens, mês a mês e lançamentos).

    Feito para projetos grandes (100 mil tarefas): uma consulta só de colunas, lida em lotes
    (sem montar um objeto do ORM por tarefa) e a planilha no modo de escrita contínua do
    openpyxl, que grava linha a linha em vez de guardar a planilha inteira na memória.
    """
    consulta = (
        select(
            Fase.titulo, Tarefa.descricao, Tarefa.data_inicio, Tarefa.data_fim,
            func.coalesce(Usuario.nome, Tarefa.responsavel), Tarefa.percentual_conclusao, Pilar.nome,
        )
        .join(Fase, Tarefa.fase_id == Fase.id)
        .outerjoin(Usuario, Tarefa.responsavel_id == Usuario.id)
        .outerjoin(Pilar, Tarefa.pilar_id == Pilar.id)
        .where(Fase.projeto_id == projeto.id)
        .order_by(Fase.ordem_sequencia, Tarefa.id)
        .execution_options(yield_per=LOTE_EXPORTACAO)
    )

    livro = openpyxl.Workbook(write_only=True)
    planilha = livro.create_sheet("Cronograma")
    for letra, largura in zip("ABCDEFG", LARGURAS_EXPORTACAO):
        planilha.column_dimensions[letra].width = largura
    planilha.append(COLUNAS_EXPORTACAO)
    for fase, descricao, inicio, fim, responsavel, percentual, pilar in db.execute(consulta):
        planilha.append([
            celula_texto_seguro(planilha, fase), celula_texto_seguro(planilha, descricao),
            celula_data(planilha, inicio), celula_data(planilha, fim),
            celula_texto_seguro(planilha, responsavel), percentual, celula_texto_seguro(planilha, pilar),
        ])

    # A parte financeira vem nas abas seguintes (a importação só lê a 1ª aba, o cronograma).
    if db.scalar(select(ItemOrcamento.id).where(ItemOrcamento.projeto_id == projeto.id).limit(1)) is not None:
        from app import orcamento  # importa services: aqui dentro evita o ciclo

        orcamento.escrever_planilha(livro, db, projeto)

    buffer = io.BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


# ---------- Usuários vindos do Clerk ----------


class AcessoNegado(Exception):
    pass


def usuario_do_clerk(db: Session, identidade: "IdentidadeClerk") -> Usuario:
    """
    Liga a identidade do Clerk a um usuário local (o papel e os projetos vêm do nosso banco):
    1. já ligado pelo "sub" -> devolve;
    2. existe usuário com o mesmo e-mail e sem provedor externo -> liga a conta (mantém o papel).
       Seguro porque o Clerk só emite o e-mail primário depois de verificá-lo;
    3. não existe -> cria como "membro" (se CLERK_CADASTRO_AUTOMATICO), sem acesso a projeto algum.
    """
    usuario = db.scalar(
        select(Usuario).where(
            Usuario.provedor_auth == ProvedorAuth.CLERK, Usuario.provedor_sub == identidade.sub
        )
    )
    if usuario is not None:
        return usuario

    usuario = db.scalar(select(Usuario).where(Usuario.email == identidade.email))
    if usuario is not None:
        if usuario.provedor_sub is not None:
            # O e-mail já pertence a outra identidade externa: não sequestra a conta.
            logger.warning("Clerk sub %s tentou usar o e-mail do usuário %s", identidade.sub, usuario.id)
            raise AcessoNegado("E-mail já vinculado a outra conta.")
        usuario.provedor_auth = ProvedorAuth.CLERK
        usuario.provedor_sub = identidade.sub
        acao = "usuario.vinculado_clerk"
    else:
        if not settings.clerk_cadastro_automatico:
            raise AcessoNegado("Usuário não cadastrado. Peça acesso a um administrador.")
        usuario = Usuario(
            email=identidade.email,
            nome=identidade.nome or identidade.email.split("@")[0],
            papel=PapelUsuario.MEMBRO,  # menor privilégio: um admin promove depois
            provedor_auth=ProvedorAuth.CLERK,
            provedor_sub=identidade.sub,
        )
        db.add(usuario)
        acao = "usuario.criado_via_clerk"

    try:
        db.flush()
        auditoria.registrar(db, acao, "usuario", usuario.id, usuario=usuario, detalhes={"email": usuario.email})
        db.commit()
    except IntegrityError:
        # Duas requisições simultâneas do mesmo usuário novo: a outra já criou.
        db.rollback()
        usuario = db.scalar(
            select(Usuario).where(
                Usuario.provedor_auth == ProvedorAuth.CLERK, Usuario.provedor_sub == identidade.sub
            )
        )
        if usuario is None:
            raise AcessoNegado("Não foi possível vincular a conta.")
    return usuario
