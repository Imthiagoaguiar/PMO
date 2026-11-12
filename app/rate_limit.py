"""
Limite de requisições por IP (janela deslizante, em memória).

Limitação conhecida: o contador vive em cada processo. Com vários workers/servidores,
troque o armazenamento por Redis (a interface `LimitadorTaxa` continua a mesma).
Atrás de proxy reverso, rode o uvicorn com `--proxy-headers --forwarded-allow-ips=<ip do proxy>`
para que `request.client.host` seja o IP real do usuário.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Deque, Dict, List

from fastapi import HTTPException, Request, status

_LIMITADORES: List["LimitadorTaxa"] = []


class LimitadorTaxa:
    def __init__(self, nome: str, max_requisicoes: int, janela_segundos: int) -> None:
        self.nome = nome
        self.max_requisicoes = max_requisicoes
        self.janela = janela_segundos
        self._acessos: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        _LIMITADORES.append(self)

    def __call__(self, request: Request) -> None:
        """Usado como dependência do FastAPI: `Depends(limitador)`."""
        chave = request.client.host if request.client else "desconhecido"
        agora = time.monotonic()
        with self._lock:
            fila = self._acessos[chave]
            while fila and fila[0] <= agora - self.janela:
                fila.popleft()
            if len(fila) >= self.max_requisicoes:
                espera = int(self.janela - (agora - fila[0])) + 1
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Muitas requisições. Aguarde um pouco e tente novamente.",
                    headers={"Retry-After": str(espera)},
                )
            fila.append(agora)
            self._limpar_ips_inativos(agora)

    def _limpar_ips_inativos(self, agora: float) -> None:
        # Evita que o dicionário cresça para sempre com IPs que não voltaram.
        if len(self._acessos) > 10_000:
            for ip in [ip for ip, f in self._acessos.items() if not f or f[-1] <= agora - self.janela]:
                del self._acessos[ip]

    def resetar(self) -> None:
        with self._lock:
            self._acessos.clear()


def resetar_todos() -> None:
    """Usado nos testes."""
    for limitador in _LIMITADORES:
        limitador.resetar()


limite_login = LimitadorTaxa("login", max_requisicoes=10, janela_segundos=60)
limite_refresh = LimitadorTaxa("refresh", max_requisicoes=30, janela_segundos=60)
limite_importacao = LimitadorTaxa("importacao", max_requisicoes=10, janela_segundos=60)
# Cada chamada à IA custa tokens: limite mais apertado.
limite_ia = LimitadorTaxa("ia", max_requisicoes=5, janela_segundos=60)
