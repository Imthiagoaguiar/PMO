import os
import tempfile

# Configuração precisa existir ANTES de importar a aplicação.
_db_dir = tempfile.mkdtemp(prefix="pmo-tests-")
# Use TEST_DATABASE_URL para rodar contra um Postgres descartável (NUNCA o banco real: as tabelas são apagadas).
os.environ["DATABASE_URL"] = os.environ.get("TEST_DATABASE_URL", f"sqlite:///{_db_dir}/test.db")
os.environ["SECRET_KEY"] = "chave-de-teste-" + "x" * 40
os.environ["COOKIE_SECURE"] = "false"  # o TestClient usa http://
os.environ["AMBIENTE"] = "dev"
os.environ["VERIFICAR_SENHAS_VAZADAS"] = "false"  # nenhum teste acessa a internet

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
