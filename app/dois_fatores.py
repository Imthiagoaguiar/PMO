"""
Verificação em duas etapas: TOTP (RFC 6238, apps como Google/Microsoft Authenticator)
e códigos de recuperação de uso único.
"""

import hmac
import re
import secrets
import time
from typing import List

import pyotp
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.cripto import cifrar, decifrar
from app.models import CodigoRecuperacao, Usuario
from app.security import agora_utc, hash_token

EMISSOR = "PMO Waterfall"  # nome que aparece no app autenticador
PERIODO_SEGUNDOS = 30
QTD_CODIGOS_RECUPERACAO = 10
# Sem 0/O e 1/I/L: o código vai ser digitado a partir de um papel.
_ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"


def iniciar_configuracao(usuario: Usuario) -> str:
    """Gera um segredo novo (ainda inativo) e devolve a URI otpauth:// para o QR code."""
    segredo = pyotp.random_base32()
    usuario.totp_segredo_cifrado = cifrar(segredo)
    usuario.totp_ultimo_passo = None
    return segredo


def uri_provisionamento(usuario: Usuario, segredo: str) -> str:
    return pyotp.TOTP(segredo).provisioning_uri(name=usuario.email, issuer_name=EMISSOR)


def verificar_totp(usuario: Usuario, codigo: str) -> bool:
    """
    Aceita o passo atual e um de cada lado (relógio do celular adiantado/atrasado até 30 s).
    Um passo já usado não vale de novo: impede reaproveitar um código visto por cima do ombro.
    """
    codigo = re.sub(r"\s", "", codigo)
    if not usuario.totp_segredo_cifrado or not re.fullmatch(r"\d{6}", codigo):
        return False
    totp = pyotp.TOTP(decifrar(usuario.totp_segredo_cifrado))
    passo_atual = int(time.time() // PERIODO_SEGUNDOS)
    for passo in (passo_atual - 1, passo_atual, passo_atual + 1):
        if usuario.totp_ultimo_passo is not None and passo <= usuario.totp_ultimo_passo:
            continue
        if hmac.compare_digest(totp.generate_otp(passo), codigo):
            usuario.totp_ultimo_passo = passo
            return True
    return False


def _normalizar_codigo(codigo: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", codigo.upper())


def gerar_codigos_recuperacao(db: Session, usuario: Usuario) -> List[str]:
    """Substitui os códigos anteriores. Os novos só são mostrados agora: o banco guarda o hash."""
    db.execute(delete(CodigoRecuperacao).where(CodigoRecuperacao.usuario_id == usuario.id))
    codigos = []
    for _ in range(QTD_CODIGOS_RECUPERACAO):
        bruto = "".join(secrets.choice(_ALFABETO) for _ in range(10))  # ~49 bits
        codigos.append(f"{bruto[:5]}-{bruto[5:]}")
        db.add(CodigoRecuperacao(usuario_id=usuario.id, codigo_hash=hash_token(bruto)))
    return codigos


def usar_codigo_recuperacao(db: Session, usuario: Usuario, codigo: str) -> bool:
    normalizado = _normalizar_codigo(codigo)
    if len(normalizado) != 10:
        return False
    registro = db.scalar(
        select(CodigoRecuperacao).where(
            CodigoRecuperacao.usuario_id == usuario.id,
            CodigoRecuperacao.codigo_hash == hash_token(normalizado),
            CodigoRecuperacao.usado_em.is_(None),
        )
    )
    if registro is None:
        return False
    registro.usado_em = agora_utc()
    return True


def verificar_codigo(db: Session, usuario: Usuario, codigo: str) -> str:
    """Aceita o código do app ou um de recuperação. Retorna o método usado ("totp"/"recuperacao") ou ""."""
    if verificar_totp(usuario, codigo):
        return "totp"
    if usar_codigo_recuperacao(db, usuario, codigo):
        return "recuperacao"
    return ""


def codigos_restantes(db: Session, usuario: Usuario) -> int:
    return db.scalar(
        select(func.count()).where(
            CodigoRecuperacao.usuario_id == usuario.id, CodigoRecuperacao.usado_em.is_(None)
        )
    ) or 0


def desativar(db: Session, usuario: Usuario) -> None:
    usuario.totp_ativo = False
    usuario.totp_segredo_cifrado = None
    usuario.totp_ultimo_passo = None
    db.execute(delete(CodigoRecuperacao).where(CodigoRecuperacao.usuario_id == usuario.id))
