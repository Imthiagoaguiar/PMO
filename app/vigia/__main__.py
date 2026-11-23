"""
Vigia do PMO: procura erros e possíveis falhas no sistema rodando, a cada VIGIA_INTERVALO_MINUTOS (60).

    python -m app.vigia            # fica rodando: watchdog no log + uma rodada por hora
    python -m app.vigia --uma-vez  # uma rodada agora e sai (ex.: para testar, ou agendar no cron)

Rode AO LADO da API (outro terminal / outro serviço). Ele só observa: nunca altera código nem dados.
Os relatórios aparecem na página "Vigia" para os admins listados em VIGIA_EMAILS.
"""

import argparse
import logging
import signal
import sys
import threading
import time
from contextlib import contextmanager
from datetime import timedelta
from pathlib import Path
from typing import Iterator, List, Optional

from sqlalchemy import text
from watchdog.observers import Observer

from app import registro, tempo
from app.config import settings
from app.database import SessionLocal, engine
from app.security import agora_utc
from app.vigia import rodada, sondas
from app.vigia.coletor import ColetorDeLog

logger = logging.getLogger("pmo.vigia")
TRAVA_POSTGRES = 7_220_431  # número arbitrário e fixo para o pg_advisory_lock


@contextmanager
def instancia_unica() -> Iterator[bool]:
    """Dois vigias ao mesmo tempo duplicariam os relatórios: no Postgres, só um consegue a trava."""
    if engine.dialect.name != "postgresql":
        yield True
        return
    with engine.connect() as conexao:
        conseguiu = conexao.execute(text("SELECT pg_try_advisory_lock(:n)"), {"n": TRAVA_POSTGRES}).scalar()
        try:
            yield bool(conseguiu)
        finally:
            if conseguiu:
                conexao.execute(text("SELECT pg_advisory_unlock(:n)"), {"n": TRAVA_POSTGRES})


def _coletores() -> List[ColetorDeLog]:
    """Um por arquivo: o log da API, o do worker da fila e o da limpeza do disco."""
    arquivos = [a for a in (settings.log_arquivo, settings.log_arquivo_trabalhador, settings.log_arquivo_limpeza,
                            settings.log_arquivo_backup) if a]
    if not settings.log_arquivo:
        logger.warning("Vigia: LOG_ARQUIVO não configurado; as rodadas não terão os sinais do log da API")
    return [ColetorDeLog(Path(a)) for a in arquivos]


def esperar_api(parar: threading.Event, limite_s: float, passo_s: float = 3.0) -> bool:
    """
    Espera a API responder ao /saude (até `limite_s`). Ao ligar o Mac, os serviços sobem juntos e a
    API leva alguns segundos: a primeira rodada não pode acusar "API fora do ar" por isso.
    Passado o limite, segue mesmo assim (aí ela realmente não subiu, e a rodada deve dizer).
    """
    fim = time.monotonic() + limite_s
    while not parar.is_set():
        if sondas.saude_api(settings.vigia_api_url).get("ok"):
            return True
        if time.monotonic() >= fim:
            logger.warning("Vigia: a API não respondeu em %s s; fazendo a primeira rodada assim mesmo", limite_s)
            return False
        parar.wait(passo_s)
    return False


def main(argv: Optional[list] = None) -> int:
    argumentos = argparse.ArgumentParser(description="Vigia do PMO (só observa, nunca altera)")
    argumentos.add_argument("--uma-vez", action="store_true", help="faz uma rodada agora e sai")
    args = argumentos.parse_args(argv)
    registro.configurar(None)  # o vigia escreve no terminal; o log que ele LÊ é o da API
    logging.getLogger("alembic").setLevel(logging.WARNING)  # a sonda de migração não precisa narrar o alembic

    with instancia_unica() as sou_o_unico:
        if not sou_o_unico:
            logger.error("Vigia: já existe outro vigia rodando neste banco; saindo")
            return 1

        intervalo = timedelta(minutes=settings.vigia_intervalo_minutos)
        inicio = agora_utc()
        coletores = _coletores()
        for coletor in coletores:
            coletor.iniciar(desde=inicio - intervalo)  # aproveita a última hora que já está no arquivo

        if args.uma_vez:
            with SessionLocal() as db:
                feita = rodada.executar(db, coletores, janela_inicio=inicio - intervalo)
            gravidade = feita.pior_gravidade.value if feita.pior_gravidade else "sem achados"
            print(f"Rodada {feita.id}: {gravidade} — {feita.resumo}")
            return 0

        observador = None
        if coletores:
            observador = Observer()
            for coletor in coletores:
                coletor.arquivo.parent.mkdir(parents=True, exist_ok=True)
                observador.schedule(coletor, str(coletor.arquivo.parent), recursive=False)
            observador.start()

        parar = threading.Event()
        for sinal in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sinal, lambda *_: parar.set())
        logger.info("Vigia ligado: uma rodada a cada %s min (Ctrl+C para parar)", settings.vigia_intervalo_minutos)

        esperar_api(parar, settings.vigia_espera_api_segundos)
        janela = [inicio - intervalo]

        def _rodada(agora) -> None:
            try:
                with SessionLocal() as db:
                    rodada.executar(db, coletores, janela_inicio=janela[0], agora=agora)
            except Exception:  # banco fora do ar etc.: tenta de novo na próxima vez
                logger.exception("Vigia: a rodada falhou")
            janela[0] = agora

        try:
            tempo.repetir_pelo_relogio(parar, intervalo, _rodada)
        finally:
            if observador:
                observador.stop()
                observador.join(timeout=5)
        logger.info("Vigia desligado")
        return 0


if __name__ == "__main__":
    sys.exit(main())
