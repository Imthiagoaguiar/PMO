import os

# Configuração precisa existir ANTES de importar a aplicação.
# Padrão: SQLite em memória (rápido, some ao fim da execução; chaves estrangeiras ligadas em app/database.py).
# Use TEST_DATABASE_URL para rodar contra um Postgres descartável (NUNCA o banco real: as tabelas são apagadas).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", "sqlite://")
os.environ["SECRET_KEY"] = "chave-de-teste-" + "x" * 40
os.environ["COOKIE_SECURE"] = "false"  # o TestClient usa http://
os.environ["AMBIENTE"] = "dev"
os.environ["VERIFICAR_SENHAS_VAZADAS"] = "false"  # nenhum teste acessa a internet
os.environ["EXIGIR_2FA_ADMIN"] = "false"
os.environ["LOG_ARQUIVO"] = ""  # os testes não escrevem em logs/pmo.log
os.environ["LOG_ARQUIVO_TRABALHADOR"] = ""
os.environ["LOG_ARQUIVO_LIMPEZA"] = ""
os.environ["LOG_ARQUIVO_BACKUP"] = ""
os.environ["BACKUP_DIR"] = ""  # o vigia não cobra backup nos testes (tests/test_backup.py liga)
os.environ["LIMITES_BACKEND"] = "memoria"  # tests/test_limites_redis.py testa o Redis
os.environ["FILA_SINCRONA"] = "true"  # importação roda na hora (fakeredis): sem Redis nem worker  # ligado só nos testes de 2FA (test_dois_fatores.py)

from typing import Callable, Dict  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.models import PapelUsuario, Usuario  # noqa: E402
from app.rate_limit import resetar_todos  # noqa: E402
from app.security import gerar_hash_senha  # noqa: E402

SENHA = "SenhaForte!2026"


@pytest.fixture(autouse=True)
def banco_limpo():
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    resetar_todos()
    yield


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture
def criar_usuario() -> Callable[..., Usuario]:
    def _criar(email: str, papel: PapelUsuario = PapelUsuario.MEMBRO, ativo: bool = True) -> Usuario:
        with SessionLocal() as db:
            usuario = Usuario(
                email=email, nome=email.split("@")[0], senha_hash=gerar_hash_senha(SENHA),
                papel=papel, ativo=ativo,
            )
            db.add(usuario)
            db.commit()
            return usuario

    return _criar


@pytest.fixture
def login(client) -> Callable[[str], Dict[str, str]]:
    def _login(email: str, senha: str = SENHA) -> Dict[str, str]:
        r = client.post("/auth/login", data={"username": email, "password": senha})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    return _login


class _RespostaImportacao:
    """Resposta no formato antigo (síncrono) de POST /projetos/importar, para os testes de PLANILHA."""

    def __init__(self, status_code: int, corpo: dict, headers) -> None:
        self.status_code, self._corpo, self.headers = status_code, corpo, headers
        self.text = str(corpo)

    def json(self) -> dict:
        return self._corpo


def postar_importacao(client, url: str, **kwargs):
    """
    POST /projetos/importar agora responde 202 e processa na fila (síncrona nos testes: o job já
    terminou quando a resposta chega). Converte para o formato antigo: 201 + projeto, ou 422 + erros.
    Os testes da FILA em si (202, andamento, falhas) estão em tests/test_importacao_fila.py.
    """
    r = client.post(url, **kwargs)
    if r.status_code != 202:
        return r
    imp = r.json()
    if imp["status"] == "concluida":
        projeto = client.get(f"/projetos/{imp['projeto_id']}", headers=kwargs.get("headers")).json()
        return _RespostaImportacao(201, {"projeto": projeto, "total_tarefas_importadas": imp["total_linhas"],
                                         "importacao": imp}, r.headers)
    if imp["status"] == "falhou":
        return _RespostaImportacao(422, {"detail": {"mensagem": imp["mensagem"], "erros": imp["erros"]},
                                         "importacao": imp}, r.headers)
    return r
