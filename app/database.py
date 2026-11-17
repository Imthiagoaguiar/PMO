from typing import Any, AsyncGenerator, Dict

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import settings


def criar_engine(url: str) -> Engine:
    if not url.startswith("sqlite"):
        # Por processo da API: db_pool_size fixas + db_max_overflow extras. Esperar mais que
        # db_pool_timeout por uma conexão vira 503 ("sistema ocupado"), não 500 (app/main.py).
        return create_engine(
            url, pool_pre_ping=True, pool_size=settings.db_pool_size,
            max_overflow=settings.db_max_overflow, pool_timeout=settings.db_pool_timeout,
        )

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


async def get_db() -> AsyncGenerator[Session, None]:
    """
    Sessão por requisição. ASSÍNCRONA de propósito (as rotas continuam síncronas):

    numa dependência síncrona com yield, o FastAPI fecha a sessão numa thread do MESMO grupo que
    atende as rotas. No teste de carga com 300 usuários (16/11/2026), as 40 threads ficaram todas
    esperando conexão do pool (15) e nenhuma sobrava para fechar as sessões que DEVOLVERIAM as
    conexões: impasse, CPU em 0% e 500 em cascata a cada 30 s. Aqui o fechamento roda no laço de
    eventos (um ROLLBACK de milissegundos) e não depende de thread livre.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
