from typing import Any, Dict, Generator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


def criar_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        return create_engine(url, pool_pre_ping=True)

    # SQLite (testes e desenvolvimento local).
    opcoes: Dict[str, Any] = {"connect_args": {"check_same_thread": False}}
    if url in ("sqlite://", "sqlite:///:memory:"):
        # Banco em memória: cada conexão nova seria um banco vazio. StaticPool faz a app
        # e os testes usarem a MESMA conexão (e portanto o mesmo banco).
        opcoes["poolclass"] = StaticPool
    motor = create_engine(url, **opcoes)

    @event.listens_for(motor, "connect")
    def _ligar_chaves_estrangeiras(conexao: Any, _registro: Any) -> None:
        # Sem isto o SQLite IGNORA as chaves estrangeiras e o ON DELETE CASCADE/SET NULL,
        # e os testes passariam com um comportamento diferente do Postgres de produção.
        cursor = conexao.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    return motor


engine = criar_engine(settings.database_url)

# expire_on_commit=False: os objetos continuam legíveis após o commit,
# o que permite serializá-los na resposta sem novas queries.
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
