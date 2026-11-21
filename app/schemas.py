import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Annotated, Any, Dict, Generic, List, Literal, Optional, TypeVar

from pydantic import AfterValidator, BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import FASES_WATERFALL, MAX_FASES, CategoriaOrcamento, CorMarco, FarolPilar, FormaMarco, TipoDependencia, chave_da_fase, GravidadeAchado, StatusImportacao, PapelProjeto, PapelUsuario, ProvedorAuth, StatusProjeto
from app.security import ID_MAXIMO
from app.texto import normalizar

# NIST 800-63B: comprimento importa mais que regras de "caractere especial".
# Limite máximo evita DoS com senhas gigantes no Argon2.
SENHA_MIN, SENHA_MAX = 12, 128

T = TypeVar("T")

# ID enviado no corpo (responsável, pilar, fase, membro...): de 1 a 2^31-1, o INTEGER do Postgres.
IdBanco = Annotated[int, Field(ge=1, le=ID_MAXIMO)]


class Pagina(BaseModel, Generic[T]):
    itens: List[T]
    total: int
    limite: int
    offset: int

# Datas fora daqui são erro de digitação (ex.: 2062 em vez de 2026) e, perto do ano 9999,
# somar dias úteis estoura o calendário do Python.
DATA_MINIMA, DATA_MAXIMA = date(1900, 1, 1), date(2199, 12, 31)


_DATA_CIVIL = re.compile(r"\d{4}-\d{2}-\d{2}")


def _so_data_civil(v: Any) -> Any:
    """
    Datas do cronograma são datas CIVIS (o "dia 15" em São Paulo), sem hora e sem fuso.
    Recusamos "2026-03-14T23:30:00-03:00", "...Z" e timestamps: para virar um dia, alguém teria de
    escolher um fuso, e é aí que o dia 14 vira 15. O front manda o valor do <input type="date">.
    """
    if isinstance(v, datetime):  # (datetime é subclasse de date)
        raise ValueError("envie só a data, no formato AAAA-MM-DD (sem hora nem fuso)")
    if isinstance(v, date):
        return v
    if isinstance(v, str) and _DATA_CIVIL.fullmatch(v.strip()):
        return v.strip()
    raise ValueError("envie só a data, no formato AAAA-MM-DD (sem hora nem fuso)")


def _data_no_intervalo(v: date) -> date:
    if not DATA_MINIMA <= v <= DATA_MAXIMA:
        raise ValueError(f"data fora do intervalo aceito ({DATA_MINIMA.year} a {DATA_MAXIMA.year})")
    return v


DataProjeto = Annotated[date, BeforeValidator(_so_data_civil), AfterValidator(_data_no_intervalo)]


def _em_utc(v: datetime) -> datetime:
    # SQLite devolve sem fuso (gravamos em UTC); Postgres devolve no fuso da sessão. Saída: sempre UTC.
    return v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)


# Instante (não data civil): sai sempre com "+00:00", para o navegador converter para a hora local.
InstanteUTC = Annotated[datetime, AfterValidator(_em_utc)]

# Formatos de data aceitos nas células de texto da planilha (CSV ou Excel com texto).
FORMATOS_DATA = ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%y", "%Y-%m-%d %H:%M:%S")


# ---------- Entrada ----------


MAX_TAREFAS_NA_CRIACAO = 300
_FASE_POR_NOME = {normalizar(f): f for f in FASES_WATERFALL}


class TarefaNova(BaseModel):
    descricao: str = Field(..., min_length=1, max_length=500)
    duracao_dias_uteis: Optional[int] = Field(None, ge=1, le=1000)
    responsavel: Optional[str] = Field(None, max_length=150)

    @field_validator("descricao", "responsavel", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)


class FaseNova(BaseModel):
    fase: str = Field(
        ..., min_length=1, max_length=50,
        description="Nome da fase: uma do Waterfall (aceita sem acento/maiúsculas) ou uma fase própria",
    )
    tarefas: List[TarefaNova] = Field(default_factory=list, max_length=100)

    @field_validator("fase", mode="before")
    @classmethod
    def _nome(cls, v: Any) -> Any:
        if not isinstance(v, str):
            return v
        v = " ".join(v.split())
        # "execucao" vira "Execução": as fases do Waterfall ficam com o nome oficial.
        return _FASE_POR_NOME.get(normalizar(v), v)


class ProjetoManualCreate(BaseModel):
    nome_projeto: str = Field(..., min_length=3, max_length=200)
    orcamento_aprovado: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)
    data_inicio: Optional[DataProjeto] = Field(None, description="Também é o início do cronograma encadeado")
    modelo_fases: Literal["waterfall", "em_branco"] = Field(
        "waterfall",
        description='"waterfall": nasce com as 5 fases (Iniciação... Encerramento); "em_branco": só com as fases de `fases`',
    )
    fases: List[FaseNova] = Field(
        default_factory=list, max_length=MAX_FASES,
        description="Opcional: tarefas iniciais (o mesmo formato que POST /projetos/ia devolve)",
    )

    @field_validator("nome_projeto", mode="before")
    @classmethod
    def _strip_nome(cls, v: Any) -> Any:
        if isinstance(v, str) and "\x00" in v:  # formulário multipart da importação (o JSON o middleware barra)
            raise ValueError("caractere inválido (NUL) no nome")
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _cronograma(self) -> "ProjetoManualCreate":
        nomes = [chave_da_fase(f.fase) for f in self.fases]
        if len(nomes) != len(set(nomes)):
            raise ValueError("cada fase só pode aparecer uma vez em 'fases'")
        if not all(nomes):
            raise ValueError("nome de fase vazio")
        padrao = {chave_da_fase(t) for t in FASES_WATERFALL} if self.modelo_fases == "waterfall" else set()
        if len(padrao | set(nomes)) > MAX_FASES:
            raise ValueError(f"no máximo {MAX_FASES} fases por projeto")
        tarefas = [t for f in self.fases for t in f.tarefas]
        if len(tarefas) > MAX_TAREFAS_NA_CRIACAO:
            raise ValueError(f"no máximo {MAX_TAREFAS_NA_CRIACAO} tarefas por projeto na criação")
        if self.data_inicio is None and any(t.duracao_dias_uteis for t in tarefas):
            raise ValueError("informe data_inicio para calcular as datas das tarefas com duração")
        return self


class TarefaPlanilha(BaseModel):
    """Valida uma linha da planilha antes de virar uma Tarefa."""

    fase: str = Field(..., min_length=1)
    descricao: str = Field(..., min_length=1, max_length=500)
    data_inicio: Optional[DataProjeto] = None
    data_fim: Optional[DataProjeto] = None
    responsavel: Optional[str] = Field(None, max_length=150)
    percentual_conclusao: int = Field(0, ge=0, le=100)
    pilar: Optional[str] = Field(None, max_length=60)

    @field_validator("fase", "descricao", "responsavel", "pilar", mode="before")
    @classmethod
    def _texto(cls, v: Any) -> Optional[str]:
        # Excel pode entregar números em colunas de texto; normaliza tudo para str.
        if v is None:
            return None
        texto = str(v).replace("\x00", "").strip()  # lixo de exportação de outros sistemas
        return texto or None

    @field_validator("data_inicio", "data_fim", mode="before")
    @classmethod
    def _data(cls, v: Any) -> Optional[date]:
        if v is None or (isinstance(v, str) and not v.strip()):
            return None
        if isinstance(v, datetime):  # inclui pandas.Timestamp
            return v.date()
        if isinstance(v, date):
            return v
        texto = str(v).strip()
        for formato in FORMATOS_DATA:
            try:
                return datetime.strptime(texto, formato).date()
            except ValueError:
                continue
        raise ValueError(f"data inválida '{texto}' (use DD/MM/AAAA)")

    @field_validator("percentual_conclusao", mode="before")
    @classmethod
    def _percentual(cls, v: Any) -> int:
        if v is None or (isinstance(v, str) and not v.strip()):
            return 0
        if isinstance(v, str):
            v = v.replace("%", "").replace(",", ".").strip()
        try:
            numero = float(v)
        except (TypeError, ValueError):
            raise ValueError(f"percentual inválido '{v}'")
        if not math.isfinite(numero):  # "inf", "nan", "1e400"
            raise ValueError(f"percentual inválido '{v}'")
        return math.floor(numero + 0.5)  # 50,5 -> 51 (round() do Python daria 50)

    @model_validator(mode="after")
    def _datas_coerentes(self) -> "TarefaPlanilha":
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            raise ValueError("data_fim não pode ser anterior à data_inicio")
        return self


# ---------- Saída ----------


def _proibir_nulos(modelo: BaseModel, campos: tuple) -> None:
    """Em PATCH, omitir um campo = não alterar; enviar null em campo obrigatório = erro."""
    nulos = [c for c in campos if c in modelo.model_fields_set and getattr(modelo, c) is None]
    if nulos:
        raise ValueError(f"Os campos {', '.join(nulos)} não podem ser nulos.")


def _strip_ou_none(v: Any) -> Any:
    if isinstance(v, str):
        v = v.strip()
        return v or None
    return v


class ProjetoUpdate(BaseModel):
    nome_projeto: Optional[str] = Field(None, min_length=3, max_length=200)
    orcamento_aprovado: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)
    data_inicio: Optional[DataProjeto] = None
    status: Optional[StatusProjeto] = None
    seguir_ordem_fases: Optional[bool] = Field(
        None, description="true: a fase atual é a primeira, na ordem, com pendência; false: fases em paralelo",
    )
    versao: Optional[int] = Field(
        None, ge=1, le=ID_MAXIMO,
        description="Versão que você leu/editou. Se alguém salvou depois dela, a resposta é 409 (nada é sobrescrito).",
    )

    @field_validator("nome_projeto", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "ProjetoUpdate":
        _proibir_nulos(self, ("nome_projeto", "status", "seguir_ordem_fases"))
        return self


class TarefaCreate(BaseModel):
    descricao: str = Field(..., min_length=1, max_length=500)
    data_inicio: Optional[DataProjeto] = None
    data_fim: Optional[DataProjeto] = None
    responsavel: Optional[str] = Field(None, max_length=150, description="Nome livre (pessoa sem conta)")
    responsavel_id: Optional[IdBanco] = Field(None, description="Usuário membro do projeto")
    pilar_id: Optional[IdBanco] = Field(None, description="Pilar (frente de trabalho) deste projeto")
    percentual_conclusao: int = Field(0, ge=0, le=100)
    duracao_dias_uteis: Optional[int] = Field(
        None, ge=1, le=1000,
        description="Calcula a data_fim a partir da data_inicio, pulando fins de semana e feriados nacionais",
    )

    @field_validator("descricao", "responsavel", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _datas(self) -> "TarefaCreate":
        if self.data_inicio and self.data_fim and self.data_fim < self.data_inicio:
            raise ValueError("data_fim não pode ser anterior à data_inicio")
        if self.duracao_dias_uteis is not None:
            if self.data_fim is not None:
                raise ValueError("informe data_fim OU duracao_dias_uteis, não os dois")
            if self.data_inicio is None:
                raise ValueError("duracao_dias_uteis exige data_inicio")
        return self


class TarefaUpdate(BaseModel):
    fase_id: Optional[IdBanco] = Field(None, description="Move a tarefa para outra fase do mesmo projeto")
    descricao: Optional[str] = Field(None, min_length=1, max_length=500)
    data_inicio: Optional[DataProjeto] = None
    data_fim: Optional[DataProjeto] = None
    responsavel: Optional[str] = Field(None, max_length=150)
    responsavel_id: Optional[IdBanco] = Field(None, description="null remove o responsável")
    pilar_id: Optional[IdBanco] = Field(None, description="null tira a tarefa do pilar")
    percentual_conclusao: Optional[int] = Field(None, ge=0, le=100)
    duracao_dias_uteis: Optional[int] = Field(
        None, ge=1, le=1000,
        description="Calcula a data_fim a partir da data_inicio, pulando fins de semana e feriados nacionais",
    )
    versao: Optional[int] = Field(
        None, ge=1, le=ID_MAXIMO,
        description="Versão que você leu/editou. Se alguém salvou depois dela, a resposta é 409 (nada é sobrescrito).",
    )

    @field_validator("descricao", "responsavel", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "TarefaUpdate":
        _proibir_nulos(self, ("fase_id", "descricao", "percentual_conclusao", "duracao_dias_uteis"))
        if "duracao_dias_uteis" in self.model_fields_set and "data_fim" in self.model_fields_set:
            raise ValueError("informe data_fim OU duracao_dias_uteis, não os dois")
        return self


class TarefaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fase_id: int
    descricao: str
    data_inicio: Optional[date]
    data_fim: Optional[date]
    responsavel: Optional[str]
    responsavel_id: Optional[int]
    pilar_id: Optional[int] = None
    percentual_conclusao: int
    concluida_em: Optional[InstanteUTC] = Field(None, description="Quando chegou a 100% (nulo se aberta)")
    versao: int = Field(..., description="Envie de volta no PATCH (bloqueio otimista)")


# ---------- Dependências ----------


class DependenciaCreate(BaseModel):
    predecessora_id: IdBanco = Field(..., description="A: a tarefa que precisa terminar antes")
    sucessora_id: IdBanco = Field(..., description="B: a tarefa que depende de A")
    tipo: TipoDependencia = Field(
        TipoDependencia.INICIO_APOS_FIM,
        description="inicio_apos_fim: B só começa depois que A terminar; fim_apos_fim: B só termina depois de A",
    )
    ajustar_datas: bool = Field(True, description="Empurra as datas de B (e de quem depende de B) para depois de A")


class DependenciaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    predecessora_id: int
    sucessora_id: int
    tipo: TipoDependencia


class DependenciaCriada(BaseModel):
    dependencia: DependenciaRead
    reprogramadas: List["TarefaRead"] = Field(default_factory=list, description="Tarefas cujas datas foram empurradas")


class Reprogramacao(BaseModel):
    reprogramadas: List["TarefaRead"]


# ---------- Marcos da linha do tempo ----------


class MarcoBase(BaseModel):
    @field_validator("titulo", "descricao", mode="before", check_fields=False)
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)


class MarcoCreate(MarcoBase):
    titulo: str = Field(..., min_length=1, max_length=80, description="Ex.: Pré-entrega, Go-live, Auditoria")
    descricao: Optional[str] = Field(None, max_length=500)
    data: DataProjeto
    data_fim: Optional[DataProjeto] = Field(None, description="Só na forma 'faixa' (um período)")
    forma: FormaMarco = FormaMarco.BANDEIRA
    cor: CorMarco = CorMarco.AZUL

    @model_validator(mode="after")
    def _periodo(self) -> "MarcoCreate":
        conferir_periodo_do_marco(self.forma, self.data, self.data_fim)
        return self


class MarcoUpdate(MarcoBase):
    titulo: Optional[str] = Field(None, min_length=1, max_length=80)
    descricao: Optional[str] = Field(None, max_length=500)
    data: Optional[DataProjeto] = None
    data_fim: Optional[DataProjeto] = None
    forma: Optional[FormaMarco] = None
    cor: Optional[CorMarco] = None
    versao: Optional[int] = Field(None, ge=1, le=ID_MAXIMO, description="Bloqueio otimista (409 se alguém salvou antes)")

    @model_validator(mode="after")
    def _obrigatorios(self) -> "MarcoUpdate":
        _proibir_nulos(self, ("titulo", "data", "forma", "cor"))
        return self


def conferir_periodo_do_marco(forma: FormaMarco, data: date, data_fim: Optional[date]) -> None:
    if forma == FormaMarco.FAIXA:
        if data_fim is None:
            raise ValueError("a faixa precisa de data_fim (o fim do período)")
        if data_fim < data:
            raise ValueError("data_fim não pode ser anterior à data")
    elif data_fim is not None:
        raise ValueError("só a forma 'faixa' tem data_fim; os outros marcos são um dia só")


class MarcoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    descricao: Optional[str]
    data: date
    data_fim: Optional[date]
    forma: FormaMarco
    cor: CorMarco
    versao: int


# ---------- Orçamento ----------

# Aceita negativo (estorno); o Dinheiro dos pilares, mais abaixo, não.
ValorMonetario = Annotated[Decimal, Field(max_digits=14, decimal_places=2)]


class ItemOrcamentoCreate(BaseModel):
    descricao: str = Field(..., min_length=1, max_length=150)
    categoria: CategoriaOrcamento = CategoriaOrcamento.OPEX
    pilar_id: Optional[IdBanco] = Field(None, description="Pilar deste projeto (o financeiro do pilar passa a vir daqui)")
    valor_orcado: ValorMonetario = Field(..., ge=0)

    @field_validator("descricao", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)


class ItemOrcamentoUpdate(BaseModel):
    descricao: Optional[str] = Field(None, min_length=1, max_length=150)
    categoria: Optional[CategoriaOrcamento] = None
    pilar_id: Optional[IdBanco] = Field(None, description="null tira o item do pilar")
    valor_orcado: Optional[ValorMonetario] = Field(None, ge=0)
    versao: Optional[int] = Field(None, ge=1, le=ID_MAXIMO, description="Bloqueio otimista (409 se alguém salvou antes)")

    @field_validator("descricao", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "ItemOrcamentoUpdate":
        _proibir_nulos(self, ("descricao", "categoria", "valor_orcado"))
        return self


class LancamentoCreate(BaseModel):
    item_id: IdBanco
    data: DataProjeto
    descricao: str = Field(..., min_length=1, max_length=200)
    documento: Optional[str] = Field(None, max_length=60, description="Nota fiscal, pedido de compra...")
    valor: ValorMonetario = Field(..., description="Gasto (positivo) ou estorno (negativo); zero não")

    @field_validator("descricao", "documento", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @field_validator("valor")
    @classmethod
    def _nao_zero(cls, v: Decimal) -> Decimal:
        if v == 0:
            raise ValueError("o valor não pode ser zero")
        return v


class LancamentoUpdate(BaseModel):
    item_id: Optional[IdBanco] = None
    data: Optional[DataProjeto] = None
    descricao: Optional[str] = Field(None, min_length=1, max_length=200)
    documento: Optional[str] = Field(None, max_length=60)
    valor: Optional[ValorMonetario] = None

    @field_validator("descricao", "documento", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "LancamentoUpdate":
        _proibir_nulos(self, ("item_id", "data", "descricao", "valor"))
        if self.valor == 0:
            raise ValueError("o valor não pode ser zero")
        return self


class LancamentoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    item_id: int
    data: date
    descricao: str
    documento: Optional[str]
    valor: Decimal
    criado_por: Optional["UsuarioResumo"] = None
    criado_em: InstanteUTC


class ItemOrcamentoRead(BaseModel):
    id: int
    descricao: str
    categoria: CategoriaOrcamento
    pilar_id: Optional[int]
    pilar_nome: Optional[str]
    valor_orcado: Decimal
    valor_realizado: Decimal
    saldo: Decimal = Field(..., description="Orçado - realizado (negativo = estourou)")
    execucao: Optional[float] = Field(None, description="Realizado / orçado, em %")
    estourado: bool
    lancamentos: int = Field(..., description="Quantos lançamentos o item tem")
    versao: int


class TotalOrcamento(BaseModel):
    chave: str = Field(..., description="Categoria (capex/opex) ou id do pilar")
    nome: str
    orcado: Decimal
    realizado: Decimal


class PontoCurva(BaseModel):
    mes: str = Field(..., description="AAAA-MM")
    realizado_no_mes: Decimal
    realizado_acumulado: Decimal
    planejado_acumulado: Optional[Decimal] = Field(None, description="Orçado distribuído por igual no período do projeto")


class AlertaOrcamento(BaseModel):
    nivel: Literal["alto", "medio", "info"]
    mensagem: str


class ResumoOrcamento(BaseModel):
    orcamento_aprovado: Optional[Decimal] = Field(None, description="O valor aprovado do projeto (cadastro)")
    total_orcado: Decimal = Field(..., description="Soma dos itens")
    total_realizado: Decimal
    saldo: Decimal
    execucao: Optional[float] = Field(None, description="Realizado / orçado, em %")
    avanco_fisico: float = Field(..., description="% concluído do cronograma")
    previsao_final: Optional[Decimal] = Field(
        None, description="Custo previsto ao final no ritmo atual: realizado / avanço físico (EAC). Só a partir de 10% de avanço",
    )
    itens: List[ItemOrcamentoRead]
    por_categoria: List[TotalOrcamento]
    por_pilar: List[TotalOrcamento]
    curva: List[PontoCurva]
    alertas: List[AlertaOrcamento]


# ---------- Anexos ----------


class AnexoRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tarefa_id: Optional[int] = Field(None, description="Nulo se a tarefa foi excluída (o arquivo continua no histórico)")
    tarefa_descricao: str
    nome_original: str
    tamanho: int = Field(..., description="Bytes")
    tipo_mime: str
    sha256: str
    enviado_por: Optional["UsuarioResumo"] = None
    enviado_em: InstanteUTC


# ---------- Gargalos ----------


class GrupoGargalo(BaseModel):
    nome: str
    tarefas_com_prazo: int
    entregues_no_prazo: int
    entregues_atrasadas: int
    abertas_atrasadas: int
    dias_atraso_entregues: int = Field(..., description="Soma dos dias de atraso das tarefas entregues depois do prazo")
    dias_atraso_abertas: int = Field(..., description="Soma dos dias de atraso das tarefas ainda abertas e vencidas")
    atraso_medio: Optional[float] = Field(None, description="Dias, entre as tarefas atrasadas (entregues ou abertas)")
    pior_atraso: int
    pontualidade: Optional[float] = Field(None, description="% das entregues (com data conhecida) que saíram no prazo")


class TarefaGargalo(BaseModel):
    tarefa_id: int
    descricao: str
    fase: str
    pilar: Optional[str]
    responsavel: Optional[str]
    prazo: date
    dias_atraso: int
    situacao: Literal["entregue_atrasada", "aberta_atrasada"]


class Gargalos(BaseModel):
    """Dias corridos. Entregue = chegou a 100% (data registrada desde 20/11/2026 ou recuperada do histórico)."""

    dias_atraso_total: int
    pontualidade: Optional[float]
    entregues_sem_data: int = Field(..., description="Concluídas sem data de conclusão conhecida (fora das contas de atraso)")
    por_fase: List[GrupoGargalo]
    por_pilar: List[GrupoGargalo]
    por_responsavel: List[GrupoGargalo]
    piores: List[TarefaGargalo] = Field(..., description="As 10 tarefas com mais dias de atraso")


class MinhaTarefa(TarefaRead):
    projeto_id: int
    nome_projeto: str
    fase_titulo: str
    atrasada: bool


class FaseCreate(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=50)
    posicao: Optional[int] = Field(
        None, ge=1, le=MAX_FASES, description="1 = primeira. Sem posição, entra no fim.",
    )

    @field_validator("titulo", mode="before")
    @classmethod
    def _limpar(cls, v: Any) -> Any:
        return " ".join(v.split()) if isinstance(v, str) else v


class FaseUpdate(BaseModel):
    titulo: str = Field(..., min_length=1, max_length=50)

    @field_validator("titulo", mode="before")
    @classmethod
    def _limpar(cls, v: Any) -> Any:
        return " ".join(v.split()) if isinstance(v, str) else v


class OrdemFases(BaseModel):
    ids: List[IdBanco] = Field(..., min_length=1, max_length=MAX_FASES, description="Todas as fases, na nova ordem")


class FaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    ordem_sequencia: int
    tarefas: List[TarefaRead] = Field(default_factory=list, description="As primeiras 200; o resto em GET /projetos/{id}/tarefas")
    total_tarefas: int = Field(0, description="Total de tarefas da fase (pode ser maior que len(tarefas))")


class ProjetoResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome_projeto: str
    orcamento_aprovado: Optional[Decimal]
    data_inicio: Optional[date]
    status: StatusProjeto
    dono_id: int
    seguir_ordem_fases: bool = True
    versao: int = Field(..., description="Envie de volta no PATCH (bloqueio otimista)")


class PermissoesProjeto(BaseModel):
    """Para o front decidir o que mostrar. A API valida tudo de novo em cada requisição."""

    editar: bool = Field(..., description="Alterar dados do projeto e o cronograma")
    gerenciar: bool = Field(..., description="Gerenciar membros e excluir o projeto")
    comentar: bool = True


class PilarResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    ordem: int
    farol: Optional[FarolPilar]


class ProjetoRead(ProjetoResumo):
    fases: List[FaseRead]
    pilares: List[PilarResumo] = []
    permissoes: Optional[PermissoesProjeto] = None


class ProjetoIAEntrada(BaseModel):
    texto: str = Field(
        ..., min_length=50, max_length=15_000,
        description="Texto do Termo de Abertura (ou um resumo do projeto)",
    )


class TarefaSugerida(BaseModel):
    descricao: str
    duracao_dias_uteis: Optional[int] = None


class FaseSugerida(BaseModel):
    fase: str
    tarefas: List[TarefaSugerida]


class SugestaoProjetoIA(BaseModel):
    """Sugestão para revisão: nada é gravado. Para criar, envie `fases` (editadas ou não) para /projetos/manual."""

    nome_projeto: str
    fases: List[FaseSugerida] = Field(..., description="Sempre as 5 fases, na ordem do Waterfall")
    descartadas: int = Field(..., description="Tarefas da IA descartadas por virem fora do formato")
    modelo: str


class ImportacaoRead(BaseModel):
    """Importação de planilha em segundo plano. Consulte até `status` ser "concluida" ou "falhou"."""

    id: int
    status: StatusImportacao
    progresso: int = Field(..., description="0 a 100 (ao vivo enquanto processa)")
    etapa: Optional[str]
    nome_projeto: str
    arquivo_nome: str
    arquivo_bytes: int
    total_linhas: Optional[int] = Field(None, description="Tarefas importadas (quando concluída)")
    projeto_id: Optional[int] = Field(None, description="Projeto criado (quando concluída)")
    mensagem: Optional[str] = Field(None, description="Por que falhou")
    erros: List[str] = Field(default_factory=list, description="Erros por linha da planilha (quando falhou)")
    criada_em: InstanteUTC
    iniciada_em: Optional[InstanteUTC]
    concluida_em: Optional[InstanteUTC]


class FaseIndicadores(BaseModel):
    fase_id: int
    titulo: str
    ordem_sequencia: int
    total_tarefas: int
    tarefas_concluidas: int
    tarefas_atrasadas: int
    percentual_conclusao: float


class ProjetoIndicadores(BaseModel):
    projeto_id: int
    percentual_conclusao: float = Field(..., description="Média simples do percentual das tarefas")
    total_tarefas: int
    tarefas_concluidas: int
    tarefas_atrasadas: int = Field(..., description="Prazo vencido e percentual abaixo de 100")
    data_inicio_prevista: Optional[date] = Field(None, description="Menor data de início entre as tarefas")
    data_fim_prevista: Optional[date] = Field(None, description="Maior data de fim entre as tarefas")
    fase_atual: Optional[str] = Field(
        None, description="Primeira fase, na ordem, com tarefa pendente (só quando o projeto segue a ordem das fases)",
    )
    fases: List[FaseIndicadores]


# ---------- Usuários e autenticação ----------


class UsuarioResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome: str
    email: str


class UsuarioRead(UsuarioResumo):
    papel: PapelUsuario
    ativo: bool
    provedor_auth: ProvedorAuth
    aguardando_primeiro_login: bool = Field(
        ..., description="Conta externa (Clerk) criada pelo admin que ainda não foi usada"
    )
    trocar_senha: bool = Field(..., description="Precisa trocar a senha antes de usar o sistema")
    dois_fatores_ativo: bool
    exige_configurar_2fa: bool = Field(..., description="Admin sem 2FA: precisa ativar antes de usar o sistema")
    pode_ver_vigia: bool = Field(False, description="Admin listado em VIGIA_EMAILS: vê os relatórios do vigia")
    lembretes_silenciados_ate: Optional[InstanteUTC] = Field(
        None, description="Até quando não recebe o e-mail de tarefas atrasadas (nulo = recebendo)",
    )

    @field_validator("lembretes_silenciados_ate")
    @classmethod
    def _so_se_ainda_vale(cls, v: Optional[datetime]) -> Optional[datetime]:
        # Silêncio que já passou é o mesmo que "recebendo": a tela não precisa comparar com o relógio.
        from app import tempo

        return v if v is not None and v > tempo.agora_utc() else None


class SilenciarLembretes(BaseModel):
    horas: int = Field(24, ge=1, le=24 * 30, description="Por quantas horas (padrão: 1 dia)")


class UsuarioCreate(BaseModel):
    email: EmailStr
    nome: str = Field(..., min_length=2, max_length=150)
    provedor_auth: Literal[ProvedorAuth.LOCAL, ProvedorAuth.CLERK] = Field(
        ProvedorAuth.LOCAL,
        description="local: entra com e-mail e senha. clerk: entra pelo Clerk com este e-mail (sem senha aqui).",
    )
    senha: Optional[str] = Field(None, min_length=SENHA_MIN, max_length=SENHA_MAX)
    papel: PapelUsuario = PapelUsuario.MEMBRO

    @model_validator(mode="after")
    def _senha_conforme_provedor(self) -> "UsuarioCreate":
        if self.provedor_auth == ProvedorAuth.LOCAL and self.senha is None:
            raise ValueError("senha é obrigatória para usuários locais")
        if self.provedor_auth == ProvedorAuth.CLERK and self.senha is not None:
            # Senha "esquecida" numa conta do Clerk seria um segundo caminho de login sem uso.
            raise ValueError("usuários do Clerk não têm senha no PMO: remova o campo senha")
        return self

    @field_validator("email")
    @classmethod
    def _email_minusculo(cls, v: str) -> str:
        return v.lower()

    @field_validator("nome", mode="before")
    @classmethod
    def _strip_nome(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class UsuarioUpdate(BaseModel):
    nome: Optional[str] = Field(None, min_length=2, max_length=150)
    papel: Optional[PapelUsuario] = None
    ativo: Optional[bool] = None

    @field_validator("nome", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "UsuarioUpdate":
        _proibir_nulos(self, ("nome", "papel", "ativo"))
        return self


class RedefinirSenha(BaseModel):
    nova_senha: str = Field(..., min_length=SENHA_MIN, max_length=SENHA_MAX, description="Senha temporária")


class AlterarSenha(BaseModel):
    senha_atual: str = Field(..., max_length=SENHA_MAX)
    nova_senha: str = Field(..., min_length=SENHA_MIN, max_length=SENHA_MAX)

    @model_validator(mode="after")
    def _senha_diferente(self) -> "AlterarSenha":
        if self.senha_atual == self.nova_senha:
            raise ValueError("A nova senha deve ser diferente da atual.")
        return self


class EsqueciSenha(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


class RedefinirSenhaPorLink(BaseModel):
    token: str = Field(..., min_length=1, max_length=2000)
    nova_senha: str = Field(..., min_length=SENHA_MIN, max_length=SENHA_MAX)


class PedidoRecebido(BaseModel):
    mensagem: str


class TokenResposta(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_em_segundos: int


# ---------- Verificação em duas etapas ----------


class DesafioDoisFatores(BaseModel):
    """Senha aceita, falta o código. Nenhuma sessão foi aberta ainda."""

    requer_2fa: Literal[True] = True
    desafio: str
    expira_em_segundos: int


class LoginDoisFatores(BaseModel):
    desafio: str = Field(..., max_length=2000)
    codigo: str = Field(..., min_length=6, max_length=20, description="6 dígitos do app ou um código de recuperação")


class ConfirmarSenha(BaseModel):
    senha: str = Field(..., max_length=SENHA_MAX)


class ConfirmarSenhaECodigo(ConfirmarSenha):
    codigo: str = Field(..., min_length=6, max_length=20)


class CodigoDoisFatores(BaseModel):
    codigo: str = Field(..., min_length=6, max_length=20)


class ConfiguracaoDoisFatores(BaseModel):
    segredo: str = Field(..., description="Para digitar no app, se não der para ler o QR code")
    uri: str = Field(..., description="otpauth:// — vira o QR code no front")


class CodigosRecuperacao(BaseModel):
    codigos_recuperacao: List[str] = Field(..., description="Mostrados uma única vez")


class StatusDoisFatores(BaseModel):
    ativo: bool
    obrigatorio: bool
    codigos_restantes: int


class MembroAdicionar(BaseModel):
    usuario_id: IdBanco
    papel: PapelProjeto = PapelProjeto.VISUALIZADOR


class MembroAlterar(BaseModel):
    papel: PapelProjeto


class MembroRead(UsuarioResumo):
    papel: PapelProjeto
    dono: bool


# ---------- Pilares ----------

Dinheiro = Annotated[Decimal, Field(ge=0, max_digits=14, decimal_places=2)]


class PilarCreate(BaseModel):
    nome: str = Field(..., min_length=1, max_length=60)
    valor_orcado: Optional[Dinheiro] = None

    @field_validator("nome", mode="before")
    @classmethod
    def _limpar(cls, v: Any) -> Any:
        return " ".join(v.split()) if isinstance(v, str) else v


class PilarUpdate(BaseModel):
    nome: Optional[str] = Field(None, min_length=1, max_length=60)
    ordem: Optional[int] = Field(None, ge=0, le=1000)
    farol: Optional[FarolPilar] = Field(None, description="null = ainda sem avaliação")
    status_texto: Optional[str] = Field(None, max_length=1000, description="Situação da frente, em poucas linhas")
    valor_orcado: Optional[Dinheiro] = None
    valor_realizado: Optional[Dinheiro] = None
    versao: Optional[int] = Field(None, ge=1, le=ID_MAXIMO, description="Versão que você editou (409 se alguém salvou depois)")

    @field_validator("nome", mode="before")
    @classmethod
    def _limpar_nome(cls, v: Any) -> Any:
        return (" ".join(v.split()) or None) if isinstance(v, str) else v

    @field_validator("status_texto", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "PilarUpdate":
        _proibir_nulos(self, ("nome", "ordem"))
        return self


class PilarRead(BaseModel):
    id: int
    nome: str
    ordem: int
    farol: Optional[FarolPilar]
    status_texto: Optional[str]
    status_atualizado_em: Optional[InstanteUTC]
    status_atualizado_por: Optional[UsuarioResumo]
    valor_orcado: Optional[Decimal]
    valor_realizado: Optional[Decimal]
    desvio: Optional[Decimal] = Field(None, description="realizado - orçado (positivo = acima do orçado)")
    execucao_financeira: Optional[float] = Field(None, description="% do orçado já realizado")
    financeiro_do_orcamento: bool = Field(
        False, description="true: orçado/realizado vêm dos itens da aba Orçamento ligados a este pilar (não se editam aqui)",
    )
    total_tarefas: int
    tarefas_concluidas: int
    tarefas_atrasadas: int
    percentual_conclusao: float = Field(..., description="Média do % das tarefas do pilar")
    versao: int


# ---------- Comentários ----------


class ComentarioCreate(BaseModel):
    texto: str = Field(..., min_length=1, max_length=2000)
    tarefa_id: Optional[IdBanco] = Field(None, description="Vazio = comentário no projeto")

    @field_validator("texto", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class ComentarioUpdate(BaseModel):
    texto: str = Field(..., min_length=1, max_length=2000)

    @field_validator("texto", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v


class ComentarioRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    projeto_id: int
    tarefa_id: Optional[int]
    texto: str
    criado_em: InstanteUTC
    editado_em: Optional[InstanteUTC]
    autor: Optional[UsuarioResumo] = Field(None, description="Nulo se o usuário foi excluído")


# ---------- Auditoria ----------


class AuditoriaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    criado_em: InstanteUTC
    usuario_id: Optional[int]
    acao: str
    entidade: str
    entidade_id: Optional[int]
    projeto_id: Optional[int]
    detalhes: Optional[Dict[str, Any]]
    ip: Optional[str]
    usuario: Optional[UsuarioResumo] = Field(None, description="Quem fez a ação (nulo: anônimo ou excluído)")


# ---------- Vigia ----------


class AchadoVigia(BaseModel):
    codigo: Optional[str] = None
    titulo: str
    gravidade: GravidadeAchado
    evidencias: List[str] = []
    causa_provavel: Optional[str] = None
    impacto: Optional[str] = None
    como_investigar: List[str] = []
    origem: Literal["regra", "ia"]


class VigiaRodadaResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    iniciada_em: InstanteUTC
    concluida_em: InstanteUTC
    janela_inicio: InstanteUTC
    pior_gravidade: Optional[GravidadeAchado]
    resumo: str
    total_achados: int = 0
    ia_situacao: str


class VigiaRodadaRead(VigiaRodadaResumo):
    achados: List[AchadoVigia]
    sinais: Dict[str, Any]
    ia_modelo: Optional[str]


class VigiaSituacao(BaseModel):
    intervalo_minutos: int
    ultima_rodada_em: Optional[InstanteUTC]
    atrasado: bool = Field(..., description="Sem rodada há mais de 2 intervalos: o vigia provavelmente parou")


# Referências a classes definidas mais abaixo no arquivo.
LancamentoRead.model_rebuild()
AnexoRead.model_rebuild()
