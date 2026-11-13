"""Regras de negócio de criação de projetos (independentes do FastAPI)."""

import io
import logging
import zipfile
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

import pandas as pd
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auditoria
from app.config import settings
from app.integracoes.feriados import calcular_data_fim, proximo_dia_util
from app.models import (
    FASES_WATERFALL,
    Fase,
    PapelProjeto,
    PapelUsuario,
    Projeto,
    ProjetoMembro,
    ProvedorAuth,
    RefreshToken,
    Tarefa,
    Usuario,
)
from app.schemas import FaseIndicadores, FaseNova, ProjetoIndicadores, TarefaPlanilha
from app.security import agora_utc
from app.texto import normalizar

if TYPE_CHECKING:
    from app.integracoes.clerk import IdentidadeClerk

logger = logging.getLogger(__name__)

MAX_LINHAS = 5_000
# .xlsx é um ZIP: um arquivo pequeno pode "explodir" ao descompactar (zip bomb).
MAX_XLSX_DESCOMPACTADO = 50 * 1024 * 1024  # 50 MB

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
) -> Projeto:
    """
    Cria o projeto já com as 5 fases do Waterfall; o criador vira dono e membro.
    Não faz commit (responsabilidade de quem chama).
    """
    projeto = Projeto(
        nome_projeto=nome_projeto,
        orcamento_aprovado=orcamento_aprovado,
        data_inicio=data_inicio,
        dono=dono,
    )
    projeto.fases = [
        Fase(titulo=titulo, ordem_sequencia=ordem)
        for ordem, titulo in enumerate(FASES_WATERFALL, start=1)
    ]
    projeto.membros = [ProjetoMembro(usuario=dono, papel=PapelProjeto.EDITOR)]
    db.add(projeto)
    return projeto


def adicionar_cronograma(projeto: Projeto, fases: List[FaseNova], inicio: Optional[date]) -> int:
    """
    Distribui as tarefas nas fases e encadeia as datas na ordem do Waterfall: cada tarefa com
    duração começa no dia útil seguinte ao fim da anterior (pulando fins de semana e feriados).
    Tarefas sem duração entram sem datas e não empurram o cronograma. Retorna o total de tarefas.
    """
    fases_por_titulo = {f.titulo: f for f in projeto.fases}
    ordem = {titulo: i for i, titulo in enumerate(FASES_WATERFALL)}
    cursor = inicio
    total = 0
    for fase in sorted(fases, key=lambda f: ordem[f.fase]):
        for nova in fase.tarefas:
            tarefa = Tarefa(descricao=nova.descricao, responsavel=nova.responsavel)
            if nova.duracao_dias_uteis is not None and cursor is not None:
                tarefa.data_inicio = proximo_dia_util(cursor)
                tarefa.data_fim = calcular_data_fim(tarefa.data_inicio, nova.duracao_dias_uteis)
                cursor = tarefa.data_fim + timedelta(days=1)
            fases_por_titulo[fase.fase].tarefas.append(tarefa)
            total += 1
    return total


# ---------- Sessões ----------


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
            df = pd.read_excel(io.BytesIO(conteudo), engine="openpyxl")
    except PlanilhaInvalida:
        raise
    except Exception as exc:  # pandas/openpyxl levantam vários tipos para arquivo corrompido
        # O detalhe vai para o log, não para o cliente (não expomos detalhes internos).
        logger.warning("Falha ao ler planilha '%s': %s", nome_arquivo, exc)
        raise PlanilhaInvalida(
            "Não foi possível ler o arquivo. Verifique se é uma planilha válida."
        ) from exc

    df.columns = [ALIASES_COLUNAS.get(normalizar(str(c)).replace(" ", "_"), c) for c in df.columns]
    faltando = COLUNAS_OBRIGATORIAS - set(df.columns)
    if faltando:
        raise PlanilhaInvalida(
            f"Colunas obrigatórias ausentes: {', '.join(sorted(faltando))}."
        )

    df = df.dropna(how="all")
    if df.empty:
        raise PlanilhaInvalida("A planilha não contém tarefas.")
    if len(df) > MAX_LINHAS:
        raise PlanilhaInvalida(f"A planilha excede o limite de {MAX_LINHAS} linhas.")

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


def _ler_csv(conteudo: bytes) -> pd.DataFrame:
    # sep=None detecta ',' ou ';' (Excel em pt-BR exporta com ';').
    # dtype=str evita que o pandas "adivinhe" datas e números; o Pydantic faz a conversão.
    for encoding in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(io.BytesIO(conteudo), sep=None, engine="python", dtype=str, encoding=encoding)
        except UnicodeDecodeError:
            continue
    raise PlanilhaInvalida("Codificação do CSV não reconhecida. Salve como UTF-8.")


def validar_tarefas(df: pd.DataFrame) -> List[Tuple[int, TarefaPlanilha]]:
    """
    Valida todas as linhas e retorna (ordem_da_fase, tarefa).
    Acumula os erros de todas as linhas para o usuário corrigir a planilha de uma vez só.
    """
    ordem_por_fase = {normalizar(t): i for i, t in enumerate(FASES_WATERFALL, start=1)}
    validas: List[Tuple[int, TarefaPlanilha]] = []
    erros: List[str] = []

    for indice, registro in zip(df.index, df.to_dict("records")):
        linha = int(indice) + 2  # +1 do cabeçalho, +1 porque a planilha começa em 1

        try:
            tarefa = TarefaPlanilha.model_validate({c: registro.get(c) for c in CAMPOS_TAREFA})
        except ValidationError as exc:
            erros.extend(f"Linha {linha}: {_formatar_erro(e)}" for e in exc.errors())
            continue

        ordem = ordem_por_fase.get(normalizar(tarefa.fase))
        if ordem is None:
            erros.append(
                f"Linha {linha}: fase '{tarefa.fase}' inválida. "
                f"Valores aceitos: {', '.join(FASES_WATERFALL)}."
            )
            continue

        validas.append((ordem, tarefa))

    if erros:
        raise PlanilhaInvalida("A planilha contém erros de validação.", erros)
    return validas


def adicionar_tarefas(projeto: Projeto, tarefas: List[Tuple[int, TarefaPlanilha]]) -> None:
    fases_por_ordem = {f.ordem_sequencia: f for f in projeto.fases}
    for ordem, dados in tarefas:
        fases_por_ordem[ordem].tarefas.append(Tarefa(**dados.model_dump(exclude={"fase"})))


def _formatar_erro(erro: dict) -> str:
    campo = ".".join(str(p) for p in erro["loc"])
    mensagem = erro["msg"].removeprefix("Value error, ")
    return f"{campo}: {mensagem}" if campo else mensagem


# ---------- Indicadores ----------


def calcular_indicadores(projeto: Projeto, hoje: Optional[date] = None) -> ProjetoIndicadores:
    """Indicadores do painel. Espera `projeto.fases[*].tarefas` já carregados."""
    hoje = hoje or date.today()

    def _atrasada(t: Tarefa) -> bool:
        return t.data_fim is not None and t.data_fim < hoje and t.percentual_conclusao < 100

    def _media(tarefas: List[Tarefa]) -> float:
        return round(sum(t.percentual_conclusao for t in tarefas) / len(tarefas), 1) if tarefas else 0.0

    fases = [
        FaseIndicadores(
            fase_id=f.id,
            titulo=f.titulo,
            ordem_sequencia=f.ordem_sequencia,
            total_tarefas=len(f.tarefas),
            tarefas_concluidas=sum(t.percentual_conclusao == 100 for t in f.tarefas),
            tarefas_atrasadas=sum(_atrasada(t) for t in f.tarefas),
            percentual_conclusao=_media(f.tarefas),
        )
        for f in projeto.fases
    ]
    todas = [t for f in projeto.fases for t in f.tarefas]
    inicios = [t.data_inicio for t in todas if t.data_inicio]
    fins = [t.data_fim for t in todas if t.data_fim]
    # Waterfall: a fase atual é a primeira (na ordem) que ainda tem trabalho pendente.
    fase_atual = next(
        (f.titulo for f in projeto.fases if any(t.percentual_conclusao < 100 for t in f.tarefas)),
        None,
    )

    return ProjetoIndicadores(
        projeto_id=projeto.id,
        percentual_conclusao=_media(todas),
        total_tarefas=len(todas),
        tarefas_concluidas=sum(f.tarefas_concluidas for f in fases),
        tarefas_atrasadas=sum(f.tarefas_atrasadas for f in fases),
        data_inicio_prevista=min(inicios) if inicios else None,
        data_fim_prevista=max(fins) if fins else None,
        fase_atual=fase_atual,
        fases=fases,
    )


# ---------- Exportação ----------

COLUNAS_EXPORTACAO = ["Fase", "Descrição", "Data Início", "Data Fim", "Responsável", "% Conclusão"]
# Células que começam com estes caracteres podem ser interpretadas como fórmula pelo
# Excel/LibreOffice (CSV/Formula Injection). Prefixar com ' força o conteúdo a ser texto.
CARACTERES_DE_FORMULA = ("=", "+", "-", "@", "\t", "\r")


def _texto_seguro(valor: Optional[str]) -> Optional[str]:
    if isinstance(valor, str) and valor.startswith(CARACTERES_DE_FORMULA):
        return "'" + valor
    return valor


def exportar_planilha(projeto: Projeto) -> bytes:
    """Gera um .xlsx no mesmo formato aceito pela importação (dá para editar e reimportar)."""
    linhas = [
        {
            "Fase": fase.titulo,
            "Descrição": _texto_seguro(tarefa.descricao),
            "Data Início": tarefa.data_inicio,
            "Data Fim": tarefa.data_fim,
            "Responsável": _texto_seguro(
                tarefa.responsavel_usuario.nome if tarefa.responsavel_usuario else tarefa.responsavel
            ),
            "% Conclusão": tarefa.percentual_conclusao,
        }
        for fase in projeto.fases
        for tarefa in fase.tarefas
    ]
    df = pd.DataFrame(linhas, columns=COLUNAS_EXPORTACAO)

    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        df.to_excel(writer, index=False, sheet_name="Cronograma")
        planilha = writer.sheets["Cronograma"]
        for coluna, largura in zip("ABCDEF", (16, 60, 14, 14, 28, 14)):
            planilha.column_dimensions[coluna].width = largura
        for coluna in ("C", "D"):
            for celula in planilha[coluna][1:]:
                celula.number_format = "DD/MM/YYYY"
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
