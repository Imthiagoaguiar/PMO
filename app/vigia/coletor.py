"""
Coletor do log (watchdog): acompanha o arquivo de log da API em tempo real e guarda os eventos
até a próxima rodada. Lê só o que foi acrescentado (posição salva) e percebe a rotação do arquivo.
"""

import json
import logging
import os
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional

from watchdog.events import FileSystemEvent, FileSystemEventHandler

logger = logging.getLogger("pmo.vigia")


def _instante(texto: Any) -> Optional[datetime]:
    try:
        valor = datetime.fromisoformat(str(texto))
    except ValueError:
        return None
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


class ColetorDeLog(FileSystemEventHandler):
    def __init__(self, arquivo: Path, max_eventos: int = 50_000) -> None:
        self.arquivo = Path(arquivo)
        self._eventos: Deque[Dict[str, Any]] = deque(maxlen=max_eventos)  # protege a memória num pico de erros
        self._posicao = 0
        self._inode: Optional[int] = None
        self._resto = b""  # linha ainda sem "\n" (a API está no meio da escrita)
        self._trava = threading.Lock()
        self.linhas_ilegiveis = 0
        self.descartados = 0

    # ---------- watchdog ----------

    def on_modified(self, event: FileSystemEvent) -> None:
        self._se_for_o_log(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        self._se_for_o_log(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:  # rotação: pmo.log -> pmo.log.1
        self._se_for_o_log(getattr(event, "dest_path", event.src_path))
        self._se_for_o_log(event.src_path)

    def _se_for_o_log(self, caminho: Any) -> None:
        if Path(os.fsdecode(caminho)).resolve() == self.arquivo.resolve():
            self.ler_novidades()

    # ---------- leitura ----------

    def iniciar(self, desde: datetime) -> None:
        """Na partida: aproveita o que já está no arquivo (rotacionado .1 + atual) a partir de `desde`."""
        anterior = self.arquivo.with_name(self.arquivo.name + ".1")
        if anterior.exists():
            with anterior.open("rb") as f:
                self._processar(f.read() + b"\n", desde)  # arquivo fechado: não sobra linha pela metade
        self.ler_novidades(desde)

    def ler_novidades(self, desde: Optional[datetime] = None) -> None:
        with self._trava:
            try:
                info = self.arquivo.stat()
            except FileNotFoundError:
                return
            if self._inode is not None and (info.st_ino != self._inode or info.st_size < self._posicao):
                # Arquivo rotacionado ou truncado: começa do zero no arquivo novo.
                self._posicao, self._resto = 0, b""
            self._inode = info.st_ino
            with self.arquivo.open("rb") as f:
                f.seek(self._posicao)
                bloco = f.read()
                self._posicao = f.tell()
            self._resto = self._processar(self._resto + bloco, desde)

    def _processar(self, bruto: bytes, desde: Optional[datetime] = None) -> bytes:
        """Guarda os eventos das linhas completas e devolve o pedaço final sem "\n"."""
        *linhas, resto = bruto.split(b"\n")
        for linha in linhas:
            if not linha.strip():
                continue
            try:
                evento = json.loads(linha)
                if not isinstance(evento, dict):
                    raise ValueError
            except ValueError:
                self.linhas_ilegiveis += 1
                continue
            instante = _instante(evento.get("ts"))
            if instante is None or (desde is not None and instante < desde):
                continue
            evento["_instante"] = instante
            if len(self._eventos) == self._eventos.maxlen:
                self.descartados += 1
            self._eventos.append(evento)
        return resto

    def retirar(self, ate: datetime) -> List[Dict[str, Any]]:
        """Entrega (e esquece) os eventos até `ate`: cada evento entra em UMA rodada só."""
        self.ler_novidades()
        with self._trava:
            entregues = [e for e in self._eventos if e["_instante"] <= ate]
            restantes = [e for e in self._eventos if e["_instante"] > ate]
            self._eventos.clear()
            self._eventos.extend(restantes)
        return entregues
