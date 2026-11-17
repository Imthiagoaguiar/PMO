"""Primitivas de segurança: hash de senha, JWT de acesso e refresh tokens opacos."""

import hashlib
import hmac
import secrets
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import NamedTuple, Optional, Tuple

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import settings

# Argon2id com os parâmetros padrão do argon2-cffi (alinhados à recomendação da OWASP).
_hasher = PasswordHasher()

# Usado quando o e-mail não existe, para o login levar o mesmo tempo
# e não revelar quais e-mails estão cadastrados (timing attack).
_HASH_FICTICIO = _hasher.hash(secrets.token_urlsafe(16))


def agora_utc() -> datetime:
    return datetime.now(timezone.utc)


def como_utc(valor: datetime) -> datetime:
    """Alguns bancos (ex.: SQLite) devolvem datetimes sem fuso; tratamos todos como UTC."""
    return valor if valor.tzinfo else valor.replace(tzinfo=timezone.utc)


# ---------- Senhas ----------


# No máximo N Argon2 ao mesmo tempo por processo (cada um usa 64 MB e um núcleo inteiro): numa
# avalanche de logins, os excedentes esperam a vez em vez de estourar a memória do servidor.
_argon2_livres = threading.BoundedSemaphore(settings.argon2_simultaneos)


def gerar_hash_senha(senha: str) -> str:
    with _argon2_livres:
        return _hasher.hash(senha)


def verificar_senha(senha: str, hash_senha: Optional[str]) -> bool:
    try:
        with _argon2_livres:
            ok = _hasher.verify(hash_senha or _HASH_FICTICIO, senha)
    except (VerificationError, InvalidHashError):
        return False
    return ok and hash_senha is not None


def precisa_rehash(hash_senha: str) -> bool:
    """True quando os parâmetros do Argon2 mudaram desde que o hash foi gerado."""
    return _hasher.check_needs_rehash(hash_senha)


# ---------- Access token (JWT, curta duração, enviado no header Authorization) ----------


def criar_access_token(usuario_id: int, versao_sessao: int = 0) -> str:
    agora = agora_utc()
    payload = {
        "sub": str(usuario_id),
        "type": "access",
        "iss": settings.jwt_emissor,
        "iat": agora,
        "exp": agora + timedelta(minutes=settings.access_token_minutos),
        "jti": uuid.uuid4().hex,
        "ver": versao_sessao,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algoritmo)


class TokenAcesso(NamedTuple):
    usuario_id: int
    versao_sessao: int  # claim "ver"


ID_MAXIMO = 2**31 - 1  # INTEGER do Postgres


def decodificar_access_token(token: str) -> TokenAcesso:
    """Levanta jwt.InvalidTokenError se o token for inválido ou expirado."""
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algoritmo],  # lista fixa: impede ataque com alg=none
        issuer=settings.jwt_emissor,
        options={"require": ["sub", "type", "iss", "iat", "exp"]},
    )
    if payload["type"] != "access":
        raise jwt.InvalidTokenError("Tipo de token inválido")
    try:
        usuario_id, versao = int(payload["sub"]), int(payload.get("ver", 0))
    except (TypeError, ValueError) as exc:  # "abc", [1], {"a": 1}...
        raise jwt.InvalidTokenError("sub/ver inválido") from exc
    if not 0 < usuario_id <= ID_MAXIMO:
        # Um id fora do INTEGER do banco estouraria na consulta (erro 500 em vez de 401).
        raise jwt.InvalidTokenError("sub fora do intervalo")
    return TokenAcesso(usuario_id, versao)


# ---------- Desafio do 2FA (entre a senha certa e o código) ----------

DESAFIO_2FA_MINUTOS = 5


def criar_desafio_2fa(usuario_id: int, versao_sessao: int) -> str:
    """Prova de que a senha foi aceita. Sozinho não dá acesso a nada: só serve em /auth/login/2fa."""
    agora = agora_utc()
    payload = {
        "sub": str(usuario_id),
        "type": "2fa",
        "iss": settings.jwt_emissor,
        "iat": agora,
        "exp": agora + timedelta(minutes=DESAFIO_2FA_MINUTOS),
        "jti": uuid.uuid4().hex,
        "ver": versao_sessao,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algoritmo)


def decodificar_desafio_2fa(token: str) -> TokenAcesso:
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algoritmo],
        issuer=settings.jwt_emissor,
        options={"require": ["sub", "type", "iss", "iat", "exp"]},
    )
    if payload["type"] != "2fa":  # um access token não serve como desafio (e vice-versa)
        raise jwt.InvalidTokenError("Tipo de token inválido")
    try:
        usuario_id, versao = int(payload["sub"]), int(payload.get("ver", 0))
    except (TypeError, ValueError) as exc:  # "abc", [1], {"a": 1}...
        raise jwt.InvalidTokenError("sub/ver inválido") from exc
    if not 0 < usuario_id <= ID_MAXIMO:
        # Um id fora do INTEGER do banco estouraria na consulta (erro 500 em vez de 401).
        raise jwt.InvalidTokenError("sub fora do intervalo")
    return TokenAcesso(usuario_id, versao)


# ---------- Link de redefinição de senha (enviado por e-mail) ----------


class TokenRedefinicao(NamedTuple):
    usuario_id: int
    versao_sessao: int
    impressao_senha: str


def impressao_da_senha(senha_hash: Optional[str]) -> str:
    """
    Resumo da senha ATUAL (HMAC do hash com a SECRET_KEY): o link deixa de valer assim que a
    senha muda, por qualquer caminho. Não dá para chegar ao hash a partir dele.
    """
    return hmac.new(settings.secret_key.encode(), (senha_hash or "").encode(), hashlib.sha256).hexdigest()[:32]


def criar_token_redefinicao(usuario_id: int, versao_sessao: int, senha_hash: Optional[str]) -> str:
    agora = agora_utc()
    payload = {
        "sub": str(usuario_id),
        "type": "redefinir_senha",
        "iss": settings.jwt_emissor,
        "iat": agora,
        "exp": agora + timedelta(minutes=settings.redefinicao_senha_minutos),
        "jti": uuid.uuid4().hex,
        "ver": versao_sessao,
        "pwd": impressao_da_senha(senha_hash),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algoritmo)


def decodificar_token_redefinicao(token: str) -> TokenRedefinicao:
    payload = jwt.decode(
        token,
        settings.secret_key,
        algorithms=[settings.jwt_algoritmo],
        issuer=settings.jwt_emissor,
        options={"require": ["sub", "type", "iss", "iat", "exp", "ver", "pwd"]},
    )
    if payload["type"] != "redefinir_senha":  # um access token ou desafio do 2FA não serve aqui
        raise jwt.InvalidTokenError("Tipo de token inválido")
    try:
        usuario_id, versao, impressao = int(payload["sub"]), int(payload["ver"]), payload["pwd"]
    except (TypeError, ValueError) as exc:
        raise jwt.InvalidTokenError("sub/ver inválido") from exc
    if not 0 < usuario_id <= ID_MAXIMO or not isinstance(impressao, str):
        raise jwt.InvalidTokenError("sub/pwd inválido")
    return TokenRedefinicao(usuario_id, versao, impressao)


# ---------- Refresh token (opaco, longa duração, cookie httpOnly) ----------


def gerar_refresh_token() -> Tuple[str, str]:
    """Retorna (token_para_o_cliente, hash_para_o_banco). O banco nunca guarda o token em claro."""
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
