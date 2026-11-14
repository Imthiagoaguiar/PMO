"""
Fixtures dos testes críticos (importação, APIs externas, autenticação e Waterfall).

Herdam de tests/conftest.py: banco SQLite em memória recriado a cada teste (`banco_limpo`),
`client` (TestClient sobre httpx), `criar_usuario` e `login`.
Nenhum teste daqui acessa a rede: Brasil API, Groq e Clerk são sempre simulados.
"""

import io
from datetime import date, timedelta
from typing import Any, Callable, Dict, Iterable, List, Optional
from unittest import mock

import httpx
import jwt
import openpyxl
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.integracoes import clerk, feriados, ia
from app.models import Fase, PapelUsuario, Projeto, Tarefa
from app.security import agora_utc
from tests.conftest import postar_importacao

GERENTE = "gerente@empresa.com.br"
MEMBRO = "membro@empresa.com.br"
EMISSOR_CLERK = "https://teste.clerk.accounts.dev"
FERIADOS_2026 = [
    {"date": "2026-04-21", "name": "Tiradentes", "type": "national"},
    {"date": "2026-05-01", "name": "Dia do Trabalho", "type": "national"},
]


# ---------- Banco ----------


@pytest.fixture
def db() -> Iterable[Session]:
    """Sessão direta no banco de teste, para conferir o que a API gravou de verdade."""
    with SessionLocal() as sessao:
        yield sessao


def contar(db: Session, modelo: Any, *filtros: Any) -> int:
    db.expire_all()  # enxerga o que a API acabou de gravar
    return db.scalar(select(func.count()).select_from(modelo).where(*filtros)) or 0


@pytest.fixture
def banco_vazio(db) -> Callable[[], None]:
    """Afirma que nada ficou gravado (operações tudo-ou-nada)."""

    def _afirmar() -> None:
        assert (contar(db, Projeto), contar(db, Fase), contar(db, Tarefa)) == (0, 0, 0)

    return _afirmar


# ---------- Usuários ----------


@pytest.fixture
def gerente(criar_usuario, login) -> Dict[str, str]:
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    return login(GERENTE)


# ---------- Brasil API ----------


@pytest.fixture(autouse=True)
def _cache_de_feriados_limpo():
    feriados.limpar_cache()
    yield
    feriados.limpar_cache()


@pytest.fixture
def brasil_api():
    """
    Substitui httpx.get do módulo de feriados por um Mock (unittest.mock).
    Por padrão responde 200 com os feriados de 2026; cada teste troca o `side_effect`.
    """

    def _ok(url: str, **_: Any) -> httpx.Response:
        ano = int(url.rsplit("/", 1)[-1])
        corpo = FERIADOS_2026 if ano == 2026 else []
        return httpx.Response(200, json=corpo, request=httpx.Request("GET", url))

    with mock.patch.object(feriados.httpx, "get", side_effect=_ok) as simulado:
        yield simulado


def resposta_http(status: int, url: str = "https://brasilapi.com.br/api/feriados/v1/2026", **kwargs: Any):
    return httpx.Response(status, request=httpx.Request("GET", url), **kwargs)


# ---------- Groq ----------


class GroqFalso:
    """Imita groq.Groq(...).chat.completions.create(...) e guarda como foi construído."""

    construido_com: Dict[str, Any] = {}
    comportamento: Any = None  # exceção a lançar ou objeto de resposta

    def __init__(self, **kwargs: Any) -> None:
        GroqFalso.construido_com = kwargs
        self.chat = self
        self.completions = self

    def create(self, **_: Any) -> Any:
        if isinstance(self.comportamento, BaseException):
            raise self.comportamento
        return self.comportamento


def resposta_groq(conteudo: Optional[str], sem_choices: bool = False) -> Any:
    mensagem = mock.Mock(content=conteudo)
    return mock.Mock(choices=[] if sem_choices else [mock.Mock(message=mensagem)])


@pytest.fixture
def groq_falso(monkeypatch) -> type:
    monkeypatch.setattr(settings, "groq_api_key", "gsk_chave_secreta_de_teste")
    monkeypatch.setattr(ia.groq, "Groq", GroqFalso)
    GroqFalso.construido_com, GroqFalso.comportamento = {}, None
    return GroqFalso


# ---------- Clerk ----------

CHAVE_CLERK = rsa.generate_private_key(public_exponent=65537, key_size=2048)
CHAVE_ATACANTE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def clerk_ligado(monkeypatch):
    monkeypatch.setattr(settings, "clerk_emissor", EMISSOR_CLERK)
    monkeypatch.setattr(settings, "clerk_cadastro_automatico", True)
    monkeypatch.setattr(clerk, "_chave_publica", lambda token: CHAVE_CLERK.public_key())


def token_clerk(chave: Any = CHAVE_CLERK, alg: str = "RS256", **sobrescrever: Any) -> str:
    agora = agora_utc()
    claims = {
        "iss": EMISSOR_CLERK, "sub": "user_2abc", "azp": "http://localhost:5173",
        "iat": agora, "nbf": agora, "exp": agora + timedelta(minutes=1),
        "sid": "sess_1", "primaryEmail": "nova@empresa.com.br", "fullName": "Nova Pessoa",
    }
    claims.update(sobrescrever)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, chave, alg, headers={"kid": "k1"})


def bearer(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------- Planilhas ----------


def xlsx(linhas: List[List[Any]]) -> bytes:
    livro = openpyxl.Workbook()
    for linha in linhas:
        livro.active.append(linha)
    buffer = io.BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


@pytest.fixture
def importar(client, gerente) -> Callable[..., httpx.Response]:
    def _importar(conteudo: bytes, nome_arquivo: str = "plano.csv", tipo: Optional[str] = None,
                  **form: Any) -> httpx.Response:
        arquivo = (nome_arquivo, conteudo, tipo) if tipo else (nome_arquivo, conteudo)
        return postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Importado", **form},
                           files={"arquivo": arquivo}, headers=gerente)

    return _importar


SEGUNDA = date(2026, 3, 2)  # 02/03/2026 é uma segunda-feira sem feriado
