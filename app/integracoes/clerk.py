"""
Validação do session token (JWT RS256) emitido pelo Clerk.

O Clerk só AUTENTICA (prova quem é a pessoa). A AUTORIZAÇÃO continua no nosso banco:
papel (admin/gerente/membro) e participação em projetos. O usuário do Clerk é ligado a um
`Usuario` local pelo claim "sub" (ver `services.usuario_do_clerk`).

Configuração necessária no Dashboard do Clerk (Sessions > Customize session token):
    {"primaryEmail": "{{user.primary_email_address}}", "fullName": "{{user.full_name}}"}
O token padrão não traz o e-mail, e sem ele não dá para ligar a conta a um usuário existente.
"""

import logging
from dataclasses import dataclass
from typing import Any, List, Optional

import jwt

from app.config import settings

logger = logging.getLogger(__name__)

ALGORITMOS = ["RS256"]  # fixo: nunca aceitar o "alg" que vier no token
TOLERANCIA_RELOGIO_SEGUNDOS = 5

_cliente_jwks: Optional[jwt.PyJWKClient] = None


class TokenClerkInvalido(Exception):
    pass


@dataclass(frozen=True)
class IdentidadeClerk:
    sub: str
    email: str
    nome: Optional[str]


def habilitado() -> bool:
    return bool(settings.clerk_emissor)


def emitido_pelo_clerk(token: str) -> bool:
    """
    Olha o "iss" SEM validar, só para decidir qual validador usar.
    Seguro porque cada caminho valida assinatura, emissor e algoritmo por conta própria.
    """
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
    except jwt.PyJWTError:
        return False
    return habilitado() and claims.get("iss") == settings.clerk_emissor


def _partes_autorizadas() -> List[str]:
    return settings.clerk_partes_autorizadas or settings.cors_origens


def _chave_publica(token: str) -> Any:
    """Busca no JWKS do Clerk a chave do "kid" do token (com cache; refaz o download se o kid for novo)."""
    global _cliente_jwks
    if _cliente_jwks is None:
        url = settings.clerk_jwks_url or f"{settings.clerk_emissor}/.well-known/jwks.json"
        _cliente_jwks = jwt.PyJWKClient(url, cache_keys=True, lifespan=3600, timeout=5)
    return _cliente_jwks.get_signing_key_from_jwt(token).key


def validar_token(token: str) -> IdentidadeClerk:
    try:
        claims = jwt.decode(
            token,
            _chave_publica(token),
            algorithms=ALGORITMOS,
            issuer=settings.clerk_emissor,
            leeway=TOLERANCIA_RELOGIO_SEGUNDOS,
            options={"require": ["exp", "nbf", "iat", "iss", "sub"]},
        )
    except jwt.PyJWKClientConnectionError:
        # Clerk fora do ar / sem rede: o usuário recebe 401 e o motivo real fica no log.
        logger.exception("Não foi possível baixar o JWKS do Clerk")
        raise TokenClerkInvalido("JWKS indisponível")
    except jwt.PyJWTError as exc:
        raise TokenClerkInvalido(str(exc))

    # "azp" = origem do front que pediu o token. Impede que um token emitido para outro
    # site que use a mesma instância do Clerk seja aceito aqui.
    azp = claims.get("azp")
    if azp is not None and azp not in _partes_autorizadas():
        raise TokenClerkInvalido(f"azp não autorizado: {azp}")

    email = claims.get("primaryEmail")
    if not isinstance(email, str) or "@" not in email or len(email) > 254:
        logger.warning("Token do Clerk sem o claim primaryEmail: configure o session token no Dashboard")
        raise TokenClerkInvalido("sem primaryEmail")

    nome = claims.get("fullName")
    nome = nome.strip()[:150] if isinstance(nome, str) and nome.strip() else None
    return IdentidadeClerk(sub=str(claims["sub"]), email=email.strip().lower(), nome=nome)
