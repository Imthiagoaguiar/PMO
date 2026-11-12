"""Primitivas de segurança: hash de senha, JWT de acesso e refresh tokens opacos."""

import hashlib
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Tuple

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


def gerar_hash_senha(senha: str) -> str:
    return _hasher.hash(senha)


def verificar_senha(senha: str, hash_senha: Optional[str]) -> bool:
    try:
        ok = _hasher.verify(hash_senha or _HASH_FICTICIO, senha)
    except (VerificationError, InvalidHashError):
        return False
    return ok and hash_senha is not None


def precisa_rehash(hash_senha: str) -> bool:
    """True quando os parâmetros do Argon2 mudaram desde que o hash foi gerado."""
    return _hasher.check_needs_rehash(hash_senha)


# ---------- Access token (JWT, curta duração, enviado no header Authorization) ----------


def criar_access_token(usuario_id: int) -> str:
    agora = agora_utc()
    payload = {
        "sub": str(usuario_id),
        "type": "access",
        "iss": settings.jwt_emissor,
        "iat": agora,
        "exp": agora + timedelta(minutes=settings.access_token_minutos),
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.jwt_algoritmo)


def decodificar_access_token(token: str) -> int:
    """Retorna o id do usuário. Levanta jwt.InvalidTokenError se o token for inválido ou expirado."""
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
        return int(payload["sub"])
    except ValueError as exc:
        raise jwt.InvalidTokenError("sub inválido") from exc


# ---------- Refresh token (opaco, longa duração, cookie httpOnly) ----------


def gerar_refresh_token() -> Tuple[str, str]:
    """Retorna (token_para_o_cliente, hash_para_o_banco). O banco nunca guarda o token em claro."""
    token = secrets.token_urlsafe(48)
    return token, hash_token(token)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()
