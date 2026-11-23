"""
Relógio do sistema.

- Instantes (auditoria, comentários, sessões): sempre UTC (`agora_utc`).
- Datas civis (o "hoje" que decide se uma tarefa está atrasada): no fuso de quem usa o sistema
  (FUSO_HORARIO, padrão America/Sao_Paulo). Na nuvem o servidor costuma rodar em UTC, e
  `date.today()` viraria o dia às 21h de Brasília.
"""

import threading
from datetime import date, datetime, timedelta
from typing import Callable
from zoneinfo import ZoneInfo

from app.config import settings
from app.security import agora_utc


def hoje() -> date:
    return agora_utc().astimezone(ZoneInfo(settings.fuso_horario)).date()


def repetir_pelo_relogio(parar: threading.Event, intervalo: timedelta, tarefa: Callable[[datetime], None],
                         conferir_a_cada: float = 30.0) -> None:
    """
    Roda `tarefa(agora)` agora e depois a cada `intervalo`, medido pelo RELÓGIO DE VERDADE.

    Por quê (21/11/2026): esperar `intervalo` de uma vez (Event.wait) usa um relógio que, no Mac,
    para enquanto o computador dorme; um lembrete marcado para 15 min depois saía uma hora mais tarde.
    Aqui o processo confere a cada `conferir_a_cada` segundos se a hora já passou: ao acordar, roda
    em no máximo esse tempo. Se o relógio voltar (ajuste de horário), a próxima vez não fica no futuro distante.
    """
    proxima = agora_utc()
    while not parar.is_set():
        agora = agora_utc()
        if proxima - agora > intervalo:  # relógio andou para trás
            proxima = agora
        if agora >= proxima:
            tarefa(agora)
            proxima = agora + intervalo
        parar.wait(conferir_a_cada)
