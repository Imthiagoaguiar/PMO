"""
Log estruturado da API (uma linha JSON por evento) — a matéria-prima do vigia (app/vigia).

- Toda requisição vira um evento "acesso": método, ROTA (o molde, ex.: /projetos/{projeto_id},
  para agrupar), status e duração. A query string não entra (pode ter dados pessoais).
- Erro não tratado vira um evento ERROR com o stack trace, ANTES de virar 500 para o cliente.
- Arquivo com rotação (10 MB x 5) em LOG_ARQUIVO; sem a variável, só no terminal.
"""

import json
import logging
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger_acesso = logging.getLogger("pmo.acesso")
logger_erro = logging.getLogger("pmo.erro")

# Atributos do próprio LogRecord (e os que outros formatadores acrescentam): não são "extras".
_CAMPOS_PADRAO = set(vars(logging.makeLogRecord({}))) | {"message", "asctime"}


class FormatoJson(logging.Formatter):
    def format(self, registro: logging.LogRecord) -> str:
        evento: Dict[str, Any] = {
            "ts": datetime.fromtimestamp(registro.created, tz=timezone.utc).isoformat(timespec="milliseconds"),
            "nivel": registro.levelname,
            "logger": registro.name,
            "msg": registro.getMessage(),
        }
        # Campos passados em `extra=` (ex.: rota, status, duracao_ms).
        evento.update({k: v for k, v in vars(registro).items() if k not in _CAMPOS_PADRAO and not k.startswith("_")})
        if registro.exc_info:
            evento["excecao"] = registro.exc_info[0].__name__ if registro.exc_info[0] else None
            evento["stack"] = self.formatException(registro.exc_info)[-8000:]
        return json.dumps(evento, ensure_ascii=False, default=str)


def configurar(arquivo: Optional[str]) -> None:
    raiz = logging.getLogger()
    raiz.setLevel(logging.INFO)
    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) for h in raiz.handlers):
        terminal = logging.StreamHandler()
        terminal.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
        raiz.addHandler(terminal)
    if arquivo and not any(isinstance(h, RotatingFileHandler) for h in raiz.handlers):
        Path(arquivo).parent.mkdir(parents=True, exist_ok=True)
        em_arquivo = RotatingFileHandler(arquivo, maxBytes=10 * 1024 * 1024, backupCount=5, encoding="utf-8")
        em_arquivo.setFormatter(FormatoJson())
        raiz.addHandler(em_arquivo)
    # O httpx registra em INFO a URL COMPLETA de cada chamada de saída (com a query string):
    # só avisos e erros dele entram no log.
    logging.getLogger("httpx").setLevel(logging.WARNING)


class RegistroDeAcesso:
    """Middleware ASGI: um evento por requisição + o stack trace de qualquer erro não tratado."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inicio = time.perf_counter()
        status = 500

        async def send_observando(mensagem: Message) -> None:
            nonlocal status
            if mensagem["type"] == "http.response.start":
                status = mensagem["status"]
            await send(mensagem)

        try:
            await self.app(scope, receive, send_observando)
        except Exception:
            logger_erro.exception("Erro não tratado em %s %s", scope.get("method"), _rota(scope),
                                  extra={"metodo": scope.get("method"), "rota": _rota(scope)})
            status = 500
            raise
        finally:
            duracao = round((time.perf_counter() - inicio) * 1000, 1)
            nivel = logging.ERROR if status >= 500 else logging.INFO
            logger_acesso.log(nivel, "%s %s %s", scope.get("method"), _rota(scope), status, extra={
                "tipo": "acesso", "metodo": scope.get("method"), "rota": _rota(scope),
                "status": status, "duracao_ms": duracao,
            })


def _rota(scope: Scope) -> str:
    """O molde da rota (/projetos/{projeto_id}), para agrupar as requisições."""
    # Caminhos que não existem viram um item só: um robô testando mil URLs não pode lotar o relatório.
    rota = scope.get("route")
    return getattr(rota, "path", None) or "(rota inexistente)"
