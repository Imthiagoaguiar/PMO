"""
Limites de requisições e contadores de falhas (janela deslizante).

Regra de ouro: limitar pelo que identifica o abuso.
- Rotas autenticadas e caras (IA, importação): por USUÁRIO. Num escritório todo mundo sai pelo
  mesmo IP (NAT); limitar por IP faria uma pessoa gastar a cota dos colegas.
- Login: por IP só como teto de CPU (o Argon2 é caro de propósito) e contando FALHAS, não
  logins certos: 30 pessoas entrando às 9h não podem ser confundidas com um ataque.

Onde os contadores ficam (LIMITES_BACKEND):
- "redis" / "auto" (padrão): no Redis, COMPARTILHADOS entre todos os processos e servidores da API.
  Em memória, cada worker contaria sozinho: com 4 workers, 5 tentativas de senha virariam 20.
- "memoria": em cada processo (testes, ou um único processo sem Redis).
Se o Redis cair, os contadores voltam para a memória (e voltam ao Redis sozinhos): a proteção
contra força bruta nunca é desligada por falta de Redis.

Atrás de proxy reverso, rode o uvicorn com `--proxy-headers --forwarded-allow-ips=<ip do proxy>`
para que `request.client.host` seja o IP real do usuário.
"""

import logging
import threading
import time
import uuid
from collections import defaultdict, deque
from typing import Callable, Deque, Dict, Hashable, List, Optional, Tuple

from fastapi import HTTPException, Request, status
from redis.exceptions import RedisError

from app.config import settings

logger = logging.getLogger("pmo.limites")
PREFIXO = "pmo:limites:"
_REGISTRADOS: List["LimitadorTaxa | ContadorFalhas"] = []


def ip_de(request: Request) -> str:
    return request.client.host if request.client else "desconhecido"


def _texto(chave: Hashable) -> str:
    return "|".join(map(str, chave)) if isinstance(chave, tuple) else str(chave)


# ---------- Armazenamento das janelas ----------


class JanelasEmMemoria:
    def __init__(self) -> None:
        self._dados: Dict[str, Deque[float]] = defaultdict(deque)
        self._trava = threading.Lock()

    def _podar(self, chave: str, agora: float, janela: float) -> Deque[float]:
        fila = self._dados[chave]
        while fila and fila[0] <= agora - janela:
            fila.popleft()
        return fila

    def tentar(self, chave: str, agora: float, janela: float, maximo: int) -> Tuple[bool, float]:
        """(permitido, instante do registro mais antigo da janela)."""
        with self._trava:
            fila = self._podar(chave, agora, janela)
            if len(fila) >= maximo:
                return False, fila[0]
            fila.append(agora)
            if len(self._dados) > 50_000:  # não cresce para sempre com chaves que não voltaram
                for c in [c for c, f in self._dados.items() if not f or f[-1] <= agora - janela]:
                    del self._dados[c]
            return True, fila[0]

    def registrar(self, chave: str, agora: float, janela: float) -> None:
        with self._trava:
            self._podar(chave, agora, janela).append(agora)

    def contar(self, chave: str, agora: float, janela: float) -> int:
        with self._trava:
            return len(self._podar(chave, agora, janela))

    def apagar_prefixo(self, prefixo: str) -> None:
        with self._trava:
            for c in [c for c in self._dados if c.startswith(prefixo)]:
                del self._dados[c]


class JanelasNoRedis:
    """
    Um ZSET por chave (score = instante). "Adiciona, conta e desfaz se passou" numa transação MULTI:
    com N processos ao mesmo tempo, ninguém passa do máximo (no pior caso, um a mais é barrado).
    """

    def __init__(self, conexao) -> None:
        self.redis = conexao

    def tentar(self, chave: str, agora: float, janela: float, maximo: int) -> Tuple[bool, float]:
        membro = f"{agora:.6f}:{uuid.uuid4().hex[:8]}"
        pipe = self.redis.pipeline(transaction=True)
        pipe.zremrangebyscore(chave, 0, agora - janela)
        pipe.zadd(chave, {membro: agora})
        pipe.zcard(chave)
        pipe.zrange(chave, 0, 0, withscores=True)
        pipe.expire(chave, int(janela) + 1)
        _, _, quantidade, mais_antigo, _ = pipe.execute()
        if quantidade > maximo:
            self.redis.zrem(chave, membro)  # não conta a tentativa barrada
            return False, mais_antigo[0][1] if mais_antigo else agora
        return True, mais_antigo[0][1] if mais_antigo else agora

    def registrar(self, chave: str, agora: float, janela: float) -> None:
        pipe = self.redis.pipeline(transaction=True)
        pipe.zremrangebyscore(chave, 0, agora - janela)
        pipe.zadd(chave, {f"{agora:.6f}:{uuid.uuid4().hex[:8]}": agora})
        pipe.expire(chave, int(janela) + 1)
        pipe.execute()

    def contar(self, chave: str, agora: float, janela: float) -> int:
        pipe = self.redis.pipeline(transaction=True)
        pipe.zremrangebyscore(chave, 0, agora - janela)
        pipe.zcard(chave)
        return pipe.execute()[1]

    def apagar_prefixo(self, prefixo: str) -> None:
        # O e-mail vai no nome da chave: escapa os curingas do MATCH ([, ], *, ?).
        escapado = "".join("\\" + c if c in "[]*?\\" else c for c in prefixo)
        chaves = list(self.redis.scan_iter(match=escapado + "*", count=500))
        if chaves:
            self.redis.delete(*chaves)


_memoria = JanelasEmMemoria()
_redis_fora_ate = 0.0


def _armazem():
    """Redis quando configurado e no ar; senão memória (e tenta o Redis de novo em 30 s)."""
    global _redis_fora_ate
    if settings.limites_backend == "memoria" or time.monotonic() < _redis_fora_ate:
        return _memoria
    from app import fila  # mesma conexão da fila (fakeredis nos testes)

    return JanelasNoRedis(fila.conexao())


def _com_fallback(operacao: Callable[[object], object]):
    global _redis_fora_ate
    armazem = _armazem()
    try:
        return operacao(armazem)
    except RedisError as exc:
        if armazem is _memoria:
            raise
        _redis_fora_ate = time.monotonic() + 30
        logger.warning("Limites: Redis indisponível (%s); usando a memória deste processo por 30 s", type(exc).__name__)
        return operacao(_memoria)


# ---------- Limitadores ----------


class LimitadorTaxa:
    """No máximo N requisições por chave numa janela de tempo; acima disso, 429 com Retry-After."""

    def __init__(self, nome: str, max_requisicoes: int, janela_segundos: int) -> None:
        self.nome = nome
        self.max_requisicoes = max_requisicoes
        self.janela = janela_segundos
        _REGISTRADOS.append(self)

    def __call__(self, request: Request) -> None:
        """Como dependência do FastAPI (`Depends(limitador)`): limita por IP."""
        self.verificar(("ip", ip_de(request)))

    def verificar(self, chave: Hashable) -> None:
        agora = time.time()  # relógio de parede: precisa valer igual em todos os processos
        permitido, mais_antigo = _com_fallback(
            lambda a: a.tentar(f"{PREFIXO}taxa:{self.nome}:{_texto(chave)}", agora, self.janela, self.max_requisicoes)
        )
        if not permitido:
            espera = int(self.janela - (agora - mais_antigo)) + 1
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Muitas requisições. Aguarde um pouco e tente novamente.",
                headers={"Retry-After": str(max(espera, 1))},
            )

    def por_usuario(self, usuario_id: int) -> None:
        self.verificar(("usuario", usuario_id))

    def resetar(self) -> None:
        _com_fallback(lambda a: a.apagar_prefixo(f"{PREFIXO}taxa:{self.nome}:"))


class ContadorFalhas:
    """Falhas por chave numa janela de BLOQUEIO_MINUTOS; bloqueia ao atingir o limite."""

    def __init__(self, nome: str, limite: Callable[[], int]) -> None:
        self.nome = nome
        self._limite = limite  # função: lê a configuração na hora (os testes a alteram)
        _REGISTRADOS.append(self)

    def _chave(self, chave: Hashable) -> str:
        return f"{PREFIXO}falhas:{self.nome}:{_texto(chave)}"

    @staticmethod
    def _janela() -> float:
        return settings.bloqueio_minutos * 60

    def bloqueado(self, chave: Hashable) -> bool:
        return _com_fallback(lambda a: a.contar(self._chave(chave), time.time(), self._janela())) >= self._limite()

    def registrar(self, chave: Hashable) -> None:
        _com_fallback(lambda a: a.registrar(self._chave(chave), time.time(), self._janela()))

    def limpar_prefixo(self, inicio_da_chave: str) -> None:
        _com_fallback(lambda a: a.apagar_prefixo(f"{PREFIXO}falhas:{self.nome}:{inicio_da_chave}"))

    def resetar(self) -> None:
        self.limpar_prefixo("")


class FalhasLogin:
    """
    Duas contagens de falhas de login (senha ou código do 2FA errado):
    - (e-mail, IP): barra quem chuta senhas de UMA conta, sem travar o dono que entra de outro lugar;
    - IP: barra quem testa uma senha comum em MUITAS contas (password spraying).
    Logins certos não contam em nenhuma das duas.
    """

    def __init__(self) -> None:
        self.por_email_ip = ContadorFalhas("email_ip", lambda: settings.max_tentativas_login)
        self.por_ip = ContadorFalhas("ip", lambda: settings.max_falhas_por_ip)

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
        self.por_email_ip.limpar_prefixo(email + "|")


def resetar_todos() -> None:
    """Usado nos testes."""
    global _redis_fora_ate
    _redis_fora_ate = 0.0
    _memoria.apagar_prefixo("")
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
