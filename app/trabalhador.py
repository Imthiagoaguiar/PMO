"""
Worker da fila de importações. Rode AO LADO da API (outro terminal ou serviço):

    python -m app.trabalhador

Precisa do Redis no ar (REDIS_URL). Pode rodar mais de um para importar várias planilhas ao mesmo tempo.
"""

import logging
import os
import sys

from rq import SimpleWorker, Worker

from app import registro
from app.config import settings
from app.fila import conexao, fila


def main() -> int:
    registro.configurar(settings.log_arquivo_trabalhador)
    if settings.fila_sincrona:
        logging.getLogger("pmo").error("FILA_SINCRONA=true: os jobs rodam dentro da API; não há o que o worker fazer")
        return 1
    # No macOS o fork() de processos com Objective-C/SSL carregados é instável (e no Windows nem existe):
    # lá o job roda no próprio processo do worker. No Linux, cada job roda num processo filho isolado.
    classe = Worker if sys.platform.startswith("linux") and hasattr(os, "fork") else SimpleWorker
    trabalhador = classe([fila()], connection=conexao())
    trabalhador.work(with_scheduler=False, logging_level="INFO")
    return 0


if __name__ == "__main__":
    sys.exit(main())
