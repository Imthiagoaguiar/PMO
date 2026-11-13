"""
Limites de requisições e contadores de falhas (janela deslizante, em memória).

Regra de ouro: limitar pelo que identifica o abuso.
- Rotas autenticadas e caras (IA, importação): por USUÁRIO. Num escritório todo mundo sai pelo
  mesmo IP (NAT); limitar por IP faria uma pessoa gastar a cota dos colegas.
- Login: por IP só como teto de CPU (o Argon2 é caro de propósito) e contando FALHAS, não
  logins certos: 30 pessoas entrando às 9h não podem ser confundidas com um ataque.

Limitação conhecida: os contadores vivem em cada processo. Com vários workers/servidores,
troque o armazenamento por Redis (as interfaces continuam as mesmas).
Atrás de proxy reverso, rode o uvicorn com `--proxy-headers --forwarded-allow-ips=<ip do proxy>`
para que `request.client.host` seja o IP real do usuário.
"""

import threading
import time
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Hashable, List, Optional

from fastapi import HTTPException, Request, status

from app.config import settings

_REGISTRADOS: List["LimitadorTaxa | ContadorFalhas"] = []


def ip_de(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


class LimitadorTaxa:
    """No máximo N requisições por chave numa janela de tempo; acima disso, 429 com Retry-After."""

    def __init__(self, nome: str, max_requisicoes: int, janela_segundos: int) -> None:
        self.nome = nome
        self.max_requisicoes = max_requisicoes
        self.janela = janela_segundos
        self._acessos: Dict[Hashable, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        _REGISTRADOS.append(self)

    def __call__(self, request: Request) -> None:
        """Como dependência do FastAPI (`Depends(limitador)`): limita por IP."""
        self.verificar(("ip", ip_de(request)))

    def verificar(self, chave: Hashable) -> None:
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
            if len(self._acessos) > 10_000:  # não cresce para sempre com chaves que não voltaram
                for c in [c for c, f in self._acessos.items() if not f or f[-1] <= agora - self.janela]:
                    del self._acessos[c]

    def por_usuario(self, usuario_id: int) -> None:
        self.verificar(("usuario", usuario_id))

    def resetar(self) -> None:
        with self._lock:
            self._acessos.clear()


class ContadorFalhas:
    """Falhas por chave numa janela de BLOQUEIO_MINUTOS; bloqueia ao atingir o limite."""

    def __init__(self, limite: Callable[[], int]) -> None:
        self._limite = limite  # função: lê a configuração na hora (os testes a alteram)
        self._falhas: Dict[Hashable, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        _REGISTRADOS.append(self)

    def _janela(self) -> float:
        return settings.bloqueio_minutos * 60

    def _podar(self, chave: Hashable, agora: float) -> Deque[float]:
        fila = self._falhas[chave]
        while fila and fila[0] <= agora - self._janela():
            fila.popleft()
        return fila

    def bloqueado(self, chave: Hashable) -> bool:
        with self._lock:
            return len(self._podar(chave, time.monotonic())) >= self._limite()

    def registrar(self, chave: Hashable) -> None:
        agora = time.monotonic()
        with self._lock:
            self._podar(chave, agora).append(agora)
            if len(self._falhas) > 50_000:
                for c in [c for c, f in self._falhas.items() if not f or f[-1] <= agora - self._janela()]:
                    del self._falhas[c]

    def limpar(self, condicao: Callable[[Hashable], bool]) -> None:
        with self._lock:
            for chave in [c for c in self._falhas if condicao(c)]:
                del self._falhas[chave]

    def resetar(self) -> None:
        with self._lock:
            self._falhas.clear()


class FalhasLogin:
    """
    Duas contagens de falhas de login (senha ou código do 2FA errado):
    - (e-mail, IP): barra quem chuta senhas de UMA conta, sem travar o dono que entra de outro lugar;
    - IP: barra quem testa uma senha comum em MUITAS contas (password spraying).
    Logins certos não contam em nenhuma das duas.
    """

    def __init__(self) -> None:
        self.por_email_ip = ContadorFalhas(lambda: settings.max_tentativas_login)
        self.por_ip = ContadorFalhas(lambda: settings.max_falhas_por_ip)

    def motivo_bloqueio(self, email: str, ip: str) -> Optional[str]:
        """None se pode tentar; senão o motivo (vai só para a auditoria, nunca para o cliente)."""
        if self.por_ip.bloqueado(ip):
            return "IP testando muitas contas"
        if self.por_email_ip.bloqueado((email, ip)):
            return "e-mail + IP"
        return None

    def registrar(self, email: str, ip: str) -> None:
        self.por_email_ip.registrar((email, ip))
        self.por_ip.registrar(ip)

    def limpar(self, email: str) -> None:
        """Login bem-sucedido ou senha redefinida: zera as falhas DESSE e-mail (não as do IP)."""
        self.por_email_ip.limpar(lambda chave: chave[0] == email)


def resetar_todos() -> None:
    """Usado nos testes."""
    for item in _REGISTRADOS:
        item.resetar()


# Login: teto por IP só contra sobrecarga de CPU; o que barra ataque são as FalhasLogin.
limite_login = LimitadorTaxa("login", max_requisicoes=settings.limite_logins_por_ip_minuto, janela_segundos=60)
# Segunda etapa do login: contador próprio (um login com 2FA são duas requisições).
limite_login_2fa = LimitadorTaxa("login-2fa", max_requisicoes=settings.limite_logins_por_ip_minuto, janela_segundos=60)
# Refresh token tem 384 bits: não há o que adivinhar. O teto é só contra sobrecarga, e cada aba
# aberta renova ao carregar a página — um escritório inteiro chegando às 9h passa daqui fácil.
limite_refresh = LimitadorTaxa("refresh", max_requisicoes=300, janela_segundos=60)

# Por usuário (ver `por_usuario`).
limite_importacao = LimitadorTaxa("importacao", max_requisicoes=10, janela_segundos=60)
limite_ia = LimitadorTaxa("ia", max_requisicoes=5, janela_segundos=60)  # cada chamada custa tokens
limite_config_2fa = LimitadorTaxa("config-2fa", max_requisicoes=10, janela_segundos=60)
limite_relatorio = LimitadorTaxa("relatorio", max_requisicoes=10, janela_segundos=60)  # PDF gasta CPU

falhas_login = FalhasLogin()
