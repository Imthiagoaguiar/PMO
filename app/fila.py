"""
Fila de trabalhos pesados (Redis + RQ). Hoje: a importação de planilhas.

A API só ENFILEIRA (responde na hora); o worker (`python -m app.trabalhador`) processa.
O status durável fica no Postgres (tabela importacoes); o Redis guarda o trabalho na fila e o
progresso ao vivo. Se o Redis reiniciar, o pior caso é uma importação "na fila" que o vigia aponta.
"""

from functools import lru_cache
from typing import Any, Optional

from redis import Redis
from redis.exceptions import RedisError
from rq import Queue
from rq.job import Job

from app.config import settings

NOME_FILA = "importacoes"
TEMPO_MAXIMO_SEGUNDOS = 30 * 60  # um job travado é morto pelo worker depois disso


@lru_cache(maxsize=1)
def conexao() -> Redis:
    if settings.fila_sincrona:
        import fakeredis  # só em testes/desenvolvimento (requirements-dev)

        return fakeredis.FakeStrictRedis()
    return Redis.from_url(settings.redis_url, socket_connect_timeout=2, socket_timeout=10)


def fila() -> Queue:
    return Queue(NOME_FILA, connection=conexao(), is_async=not settings.fila_sincrona)


def id_do_job(importacao_id: int) -> str:
    return f"importacao-{importacao_id}"


def enfileirar_importacao(importacao_id: int) -> None:
    """Levanta RedisError se a fila estiver fora do ar (a rota responde 503)."""
    fila().enqueue(
        "app.importacao.processar", importacao_id,
        job_id=id_do_job(importacao_id),
        job_timeout=TEMPO_MAXIMO_SEGUNDOS,
        result_ttl=3600, failure_ttl=7 * 24 * 3600,
    )


def progresso_ao_vivo(importacao_id: int) -> Optional[dict]:
    """O progresso fica no Redis (job.meta): atualizá-lo não mexe na transação da importação."""
    try:
        job = Job.fetch(id_do_job(importacao_id), connection=conexao())
    except Exception:  # job expirado/inexistente ou Redis fora do ar: sem progresso ao vivo
        return None
    meta: Any = job.meta or {}
    return {"progresso": meta.get("progresso"), "etapa": meta.get("etapa")} if meta else None


__all__ = ["RedisError", "conexao", "enfileirar_importacao", "fila", "progresso_ao_vivo"]
