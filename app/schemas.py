from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Generic, List, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator

from app.models import FASES_WATERFALL, PapelUsuario, ProvedorAuth, StatusProjeto
from app.texto import normalizar

# NIST 800-63B: comprimento importa mais que regras de "caractere especial".
# Limite máximo evita DoS com senhas gigantes no Argon2.
SENHA_MIN, SENHA_MAX = 12, 128

T = TypeVar("T")


class Pagina(BaseModel, Generic[T]):
    itens: List[T]
    total: int
    limite: int
    offset: int

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
    fase: str = Field(..., description="Um dos 5 nomes do Waterfall (aceita sem acento/maiúsculas)")
    tarefas: List[TarefaNova] = Field(default_factory=list, max_length=100)

    @field_validator("fase")
    @classmethod
    def _fase_oficial(cls, v: str) -> str:
        oficial = _FASE_POR_NOME.get(normalizar(v))
        if oficial is None:
            raise ValueError(f"fase inválida: use uma de {', '.join(FASES_WATERFALL)}")
        return oficial


class ProjetoManualCreate(BaseModel):
    nome_projeto: str = Field(..., min_length=3, max_length=200)
    orcamento_aprovado: Optional[Decimal] = Field(None, ge=0, max_digits=14, decimal_places=2)
    data_inicio: Optional[date] = Field(None, description="Também é o início do cronograma encadeado")
    fases: List[FaseNova] = Field(
        default_factory=list, max_length=len(FASES_WATERFALL),
        description="Opcional: tarefas iniciais (o mesmo formato que POST /projetos/ia devolve)",
    )

    @field_validator("nome_projeto", mode="before")
    @classmethod
    def _strip_nome(cls, v: Any) -> Any:
        return v.strip() if isinstance(v, str) else v

    @model_validator(mode="after")
    def _cronograma(self) -> "ProjetoManualCreate":
        nomes = [f.fase for f in self.fases]
        if len(nomes) != len(set(nomes)):
            raise ValueError("cada fase só pode aparecer uma vez em 'fases'")
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
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    responsavel: Optional[str] = Field(None, max_length=150)
    percentual_conclusao: int = Field(0, ge=0, le=100)

    @field_validator("fase", "descricao", "responsavel", mode="before")
    @classmethod
    def _texto(cls, v: Any) -> Optional[str]:
        # Excel pode entregar números em colunas de texto; normaliza tudo para str.
        if v is None:
            return None
        texto = str(v).strip()
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
            return round(float(v))
        except (TypeError, ValueError):
            raise ValueError(f"percentual inválido '{v}'")

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
    data_inicio: Optional[date] = None
    status: Optional[StatusProjeto] = None

    @field_validator("nome_projeto", mode="before")
    @classmethod
    def _limpar_texto(cls, v: Any) -> Any:
        return _strip_ou_none(v)

    @model_validator(mode="after")
    def _obrigatorios(self) -> "ProjetoUpdate":
        _proibir_nulos(self, ("nome_projeto", "status"))
        return self


class TarefaCreate(BaseModel):
    descricao: str = Field(..., min_length=1, max_length=500)
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    responsavel: Optional[str] = Field(None, max_length=150, description="Nome livre (pessoa sem conta)")
    responsavel_id: Optional[int] = Field(None, description="Usuário membro do projeto")
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
    fase_id: Optional[int] = Field(None, description="Move a tarefa para outra fase do mesmo projeto")
    descricao: Optional[str] = Field(None, min_length=1, max_length=500)
    data_inicio: Optional[date] = None
    data_fim: Optional[date] = None
    responsavel: Optional[str] = Field(None, max_length=150)
    responsavel_id: Optional[int] = Field(None, description="null remove o responsável")
    percentual_conclusao: Optional[int] = Field(None, ge=0, le=100)
    duracao_dias_uteis: Optional[int] = Field(
        None, ge=1, le=1000,
        description="Calcula a data_fim a partir da data_inicio, pulando fins de semana e feriados nacionais",
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
    percentual_conclusao: int


class MinhaTarefa(TarefaRead):
    projeto_id: int
    nome_projeto: str
    fase_titulo: str
    atrasada: bool


class FaseRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    titulo: str
    ordem_sequencia: int
    tarefas: List[TarefaRead] = []


class ProjetoResumo(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    nome_projeto: str
    orcamento_aprovado: Optional[Decimal]
    data_inicio: Optional[date]
    status: StatusProjeto
    dono_id: int


class ProjetoRead(ProjetoResumo):
    fases: List[FaseRead]


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


class ImportacaoResultado(BaseModel):
    projeto: ProjetoRead
    total_tarefas_importadas: int


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
    fase_atual: Optional[str] = Field(None, description="Primeira fase, na ordem do Waterfall, com tarefa pendente")
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


class UsuarioCreate(BaseModel):
    email: EmailStr
    nome: str = Field(..., min_length=2, max_length=150)
    senha: str = Field(..., min_length=SENHA_MIN, max_length=SENHA_MAX)
    papel: PapelUsuario = PapelUsuario.MEMBRO

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


class AlterarSenha(BaseModel):
    senha_atual: str = Field(..., max_length=SENHA_MAX)
    nova_senha: str = Field(..., min_length=SENHA_MIN, max_length=SENHA_MAX)

    @model_validator(mode="after")
    def _senha_diferente(self) -> "AlterarSenha":
        if self.senha_atual == self.nova_senha:
            raise ValueError("A nova senha deve ser diferente da atual.")
        return self


class TokenResposta(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expira_em_segundos: int


class MembroAdicionar(BaseModel):
    usuario_id: int


# ---------- Auditoria ----------


class AuditoriaRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    criado_em: datetime
    usuario_id: Optional[int]
    acao: str
    entidade: str
    entidade_id: Optional[int]
    projeto_id: Optional[int]
    detalhes: Optional[Dict[str, Any]]
    ip: Optional[str]
