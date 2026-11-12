"""
Feriados nacionais (Brasil API) e cálculo de data_fim em dias úteis.

Se a Brasil API estiver fora do ar, o cálculo continua considerando só os fins de semana
(o cronograma não pode travar por causa de uma API de terceiros) e o aviso vai para o log.
A falha fica em cache por alguns minutos para não pagar o timeout em cada requisição.
"""

import logging
import threading
import time
from datetime import date, timedelta
from typing import Dict, FrozenSet

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

TIMEOUT_SEGUNDOS = 5.0
NOVA_TENTATIVA_APOS_FALHA_SEGUNDOS = 300
MAX_DIAS_UTEIS = 1000

_cache: Dict[int, FrozenSet[date]] = {}        # feriados de um ano não mudam: cache para sempre
_falhas: Dict[int, float] = {}                 # ano -> momento da última falha
_lock = threading.Lock()


def feriados_nacionais(ano: int) -> FrozenSet[date]:
    with _lock:
        if ano in _cache:
            return _cache[ano]
        falhou_em = _falhas.get(ano)
        if falhou_em is not None and time.monotonic() - falhou_em < NOVA_TENTATIVA_APOS_FALHA_SEGUNDOS:
            return frozenset()

    try:
        resposta = httpx.get(f"{settings.brasil_api_url}/{ano}", timeout=TIMEOUT_SEGUNDOS)
        resposta.raise_for_status()
        datas = frozenset(date.fromisoformat(item["date"]) for item in resposta.json())
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        # ValueError cobre JSON inválido e data em formato inesperado.
        logger.warning("Brasil API indisponível para %s (%s): usando só fins de semana", ano, exc)
        with _lock:
            _falhas[ano] = time.monotonic()
        return frozenset()

    with _lock:
        _cache[ano] = datas
        _falhas.pop(ano, None)
    return datas


def eh_dia_util(dia: date) -> bool:
    return dia.weekday() < 5 and dia not in feriados_nacionais(dia.year)


def proximo_dia_util(dia: date) -> date:
    """O próprio dia, se for útil; senão o próximo dia útil."""
    while not eh_dia_util(dia):
        dia += timedelta(days=1)
    return dia


def calcular_data_fim(inicio: date, dias_uteis: int) -> date:
    """
    Data em que termina uma tarefa de `dias_uteis` dias, contando o próprio dia de início
    (se for útil). Ex.: início sexta 01/05 (feriado), 2 dias -> segunda 04/05 e terça 05/05.
    """
    if not 1 <= dias_uteis <= MAX_DIAS_UTEIS:
        raise ValueError(f"dias_uteis deve estar entre 1 e {MAX_DIAS_UTEIS}")
    dia, contados = inicio, 0
    while True:
        if eh_dia_util(dia):
            contados += 1
            if contados == dias_uteis:
                return dia
        dia += timedelta(days=1)


def limpar_cache() -> None:
    """Usado nos testes."""
    with _lock:
        _cache.clear()
        _falhas.clear()
