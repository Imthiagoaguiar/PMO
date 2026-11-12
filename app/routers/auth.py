"""
Fluxo de autenticação pensado para o front React:

- POST /auth/login    -> devolve o access token (JWT, 15 min) no corpo e grava o refresh token
                         num cookie httpOnly (o JavaScript não consegue lê-lo, o que protege contra XSS).
- POST /auth/refresh  -> troca o refresh token por um novo par (rotação). Reuso de um refresh
                         já trocado indica roubo: todas as sessões do usuário são revogadas.
- POST /auth/logout   -> revoga o refresh token atual e apaga o cookie.

O front guarda o access token só em memória e chama /auth/refresh ao recarregar a página.
"""

import logging
from datetime import timedelta
from typing import Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auditoria, services
from app.config import settings
from app.database import get_db
from app.deps import get_usuario_atual
from app.models import ProvedorAuth, RefreshToken, Usuario
from app.rate_limit import limite_login, limite_refresh
from app.schemas import SENHA_MAX, AlterarSenha, TokenResposta, UsuarioRead
from app.security import (
    agora_utc,
    como_utc,
    criar_access_token,
    gerar_hash_senha,
    gerar_refresh_token,
    hash_token,
    precisa_rehash,
    verificar_senha,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Autenticação"])

COOKIE_REFRESH = "pmo_refresh"
COOKIE_PATH = "/auth"  # o navegador só envia o cookie para as rotas de /auth


def _definir_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        COOKIE_REFRESH,
        token,
        max_age=settings.refresh_token_dias * 86_400,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",  # bloqueia o envio em requisições de outros sites (CSRF)
        path=COOKIE_PATH,
    )


def _apagar_cookie(response: Response) -> None:
    response.delete_cookie(
        COOKIE_REFRESH,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
    )


def _emitir_sessao(db: Session, response: Response, usuario: Usuario) -> TokenResposta:
    token, token_hash = gerar_refresh_token()
    db.add(
        RefreshToken(
            usuario_id=usuario.id,
            token_hash=token_hash,
            expira_em=agora_utc() + timedelta(days=settings.refresh_token_dias),
        )
    )
    db.commit()
    _definir_cookie(response, token)
    return TokenResposta(
        access_token=criar_access_token(usuario.id),
        expira_em_segundos=settings.access_token_minutos * 60,
    )


@router.post("/login", response_model=TokenResposta, dependencies=[Depends(limite_login)])
def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResposta:
    """Login com e-mail (campo `username`, padrão OAuth2) e senha."""
    credenciais_invalidas = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="E-mail ou senha inválidos."
    )
    if len(form.password) > SENHA_MAX or len(form.username) > 254:
        raise credenciais_invalidas  # evita gastar CPU com Argon2 em entradas gigantes

    agora = agora_utc()
    email = form.username.strip().lower()
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))

    if usuario and usuario.bloqueado_ate and como_utc(usuario.bloqueado_ate) > agora:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Muitas tentativas de login. Tente novamente mais tarde.",
        )

    # Sempre roda o Argon2, mesmo se o e-mail não existir (mesmo tempo de resposta).
    if not verificar_senha(form.password, usuario.senha_hash if usuario else None):
        auditoria.registrar(
            db, "auth.login_falhou", "usuario", usuario.id if usuario else None,
            usuario=usuario, detalhes={"email": email},
        )
        if usuario:
            usuario.tentativas_falhas += 1
            if usuario.tentativas_falhas >= settings.max_tentativas_login:
                usuario.bloqueado_ate = agora + timedelta(minutes=settings.bloqueio_minutos)
                usuario.tentativas_falhas = 0
                logger.warning("Usuário %s bloqueado por excesso de tentativas de login", usuario.id)
                auditoria.registrar(db, "auth.conta_bloqueada", "usuario", usuario.id, usuario=usuario)
        db.commit()
        raise credenciais_invalidas

    if not usuario.ativo:
        raise credenciais_invalidas

    usuario.tentativas_falhas = 0
    usuario.bloqueado_ate = None
    if precisa_rehash(usuario.senha_hash):
        usuario.senha_hash = gerar_hash_senha(form.password)
    auditoria.registrar(db, "auth.login", "usuario", usuario.id, usuario=usuario)
    return _emitir_sessao(db, response, usuario)


@router.post("/refresh", response_model=TokenResposta, dependencies=[Depends(limite_refresh)])
def renovar_sessao(
    response: Response,
    refresh_token: Optional[str] = Cookie(None, alias=COOKIE_REFRESH),
    db: Session = Depends(get_db),
) -> TokenResposta:
    sessao_invalida = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Sessão inválida ou expirada."
    )
    if not refresh_token:
        raise sessao_invalida

    registro = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
    )
    if registro is None:
        raise sessao_invalida

    if registro.revogado_em is not None:
        # Token já usado/revogado apareceu de novo: provável roubo. Derruba todas as sessões.
        logger.warning("Reuso de refresh token revogado (usuário %s)", registro.usuario_id)
        services.revogar_sessoes(db, registro.usuario_id)
        auditoria.registrar(
            db, "auth.reuso_refresh_token", "usuario", registro.usuario_id,
            usuario=db.get(Usuario, registro.usuario_id),
        )
        db.commit()
        _apagar_cookie(response)
        raise sessao_invalida

    usuario = db.get(Usuario, registro.usuario_id)
    if como_utc(registro.expira_em) <= agora_utc() or usuario is None or not usuario.ativo:
        raise sessao_invalida

    registro.revogado_em = agora_utc()  # rotação: cada refresh token vale uma única vez
    return _emitir_sessao(db, response, usuario)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(None, alias=COOKIE_REFRESH),
    db: Session = Depends(get_db),
) -> None:
    if refresh_token:
        registro = db.scalar(
            select(RefreshToken).where(RefreshToken.token_hash == hash_token(refresh_token))
        )
        if registro and registro.revogado_em is None:
            registro.revogado_em = agora_utc()
            auditoria.registrar(
                db, "auth.logout", "usuario", registro.usuario_id,
                usuario=db.get(Usuario, registro.usuario_id),
            )
            db.commit()
    _apagar_cookie(response)


@router.get("/me", response_model=UsuarioRead)
def usuario_logado(usuario: Usuario = Depends(get_usuario_atual)) -> Usuario:
    return usuario


@router.post("/alterar-senha", status_code=status.HTTP_204_NO_CONTENT)
def alterar_senha(
    payload: AlterarSenha,
    response: Response,
    usuario: Usuario = Depends(get_usuario_atual),
    db: Session = Depends(get_db),
) -> None:
    if usuario.provedor_auth != ProvedorAuth.LOCAL or not verificar_senha(
        payload.senha_atual, usuario.senha_hash
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha atual incorreta.")

    usuario.senha_hash = gerar_hash_senha(payload.nova_senha)
    services.revogar_sessoes(db, usuario.id)  # força novo login em todos os dispositivos
    auditoria.registrar(db, "auth.senha_alterada", "usuario", usuario.id, usuario=usuario)
    db.commit()
    _apagar_cookie(response)
