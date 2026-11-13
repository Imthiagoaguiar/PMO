"""
Relógio do sistema.

- Instantes (auditoria, comentários, sessões): sempre UTC (`agora_utc`).
- Datas civis (o "hoje" que decide se uma tarefa está atrasada): no fuso de quem usa o sistema
  (FUSO_HORARIO, padrão America/Sao_Paulo). Na nuvem o servidor costuma rodar em UTC, e
  `date.today()` viraria o dia às 21h de Brasília.
"""

from datetime import date
from zoneinfo import ZoneInfo

from app.config import settings
from app.security import agora_utc


def hoje() -> date:
    return agora_utc().astimezone(ZoneInfo(settings.fuso_horario)).date()
