"""Cifra de dados sensíveis guardados no banco (hoje: segredos TOTP do 2FA)."""

import base64
import functools

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.config import settings


@functools.lru_cache(maxsize=1)
def _fernet() -> Fernet:
    if settings.chave_2fa:
        return Fernet(settings.chave_2fa.encode())
    # Só em dev (em produção a config exige CHAVE_2FA): deriva uma chave da SECRET_KEY.
    derivada = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=b"pmo-2fa").derive(
        settings.secret_key.encode()
    )
    return Fernet(base64.urlsafe_b64encode(derivada))


def cifrar(texto: str) -> str:
    return _fernet().encrypt(texto.encode()).decode()


def decifrar(cifrado: str) -> str:
    try:
        return _fernet().decrypt(cifrado.encode()).decode()
    except InvalidToken as exc:
        # Chave trocada ou dado adulterado: não dá para validar o código.
        raise ValueError("Não foi possível decifrar o segredo do 2FA (CHAVE_2FA mudou?)") from exc
