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

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auditoria, senhas, services
from app.config import settings
from app.database import get_db
from app.deps import get_usuario_sessao
from app.models import ProvedorAuth, RefreshToken, Usuario
from app.rate_limit import falhas_login, limite_login, limite_refresh
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
MENSAGEM_FALHA_LOGIN = "E-mail ou senha inválidos. Após várias tentativas, o acesso fica bloqueado por alguns minutos."


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
        access_token=criar_access_token(usuario.id, usuario.versao_sessao),
        expira_em_segundos=settings.access_token_minutos * 60,
    )


@router.post("/login", response_model=TokenResposta, dependencies=[Depends(limite_login)])
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> TokenResposta:
    """
    Login com e-mail (campo `username`, padrão OAuth2) e senha.

    Toda falha devolve a MESMA resposta e leva o mesmo tempo (o Argon2 roda sempre): e-mail
    inexistente, senha errada, conta desativada ou bloqueada são indistinguíveis por fora.
    """
    credenciais_invalidas = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=MENSAGEM_FALHA_LOGIN)
    if len(form.password) > SENHA_MAX or len(form.username) > 254:
        raise credenciais_invalidas  # evita gastar CPU com Argon2 em entradas gigantes

    agora = agora_utc()
    email = form.username.strip().lower()
    ip = request.client.host if request.client else "desconhecido"
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))

    barrado_neste_ip = falhas_login.bloqueado(email, ip)
    conta_bloqueada = bool(usuario and usuario.bloqueado_ate and como_utc(usuario.bloqueado_ate) > agora)
    senha_correta = verificar_senha(form.password, usuario.senha_hash if usuario else None)

    if barrado_neste_ip or conta_bloqueada or not senha_correta or not usuario.ativo:
        if barrado_neste_ip or conta_bloqueada:
            # Tentativa durante o bloqueio: registra, mas não conta de novo (não prolonga o bloqueio).
            auditoria.registrar(
                db, "auth.login_barrado", "usuario", usuario.id if usuario else None, usuario=usuario,
                detalhes={"email": email, "motivo": "conta bloqueada" if conta_bloqueada else "e-mail + IP"},
            )
        elif not senha_correta:
            falhas_login.registrar(email, ip)
            auditoria.registrar(
                db, "auth.login_falhou", "usuario", usuario.id if usuario else None,
                usuario=usuario, detalhes={"email": email},
            )
            if usuario:
                usuario.tentativas_falhas += 1
                if usuario.tentativas_falhas >= settings.max_tentativas_conta:
                    usuario.bloqueado_ate = agora + timedelta(minutes=settings.bloqueio_minutos)
                    usuario.tentativas_falhas = 0
                    logger.warning("Conta %s bloqueada: tentativas de vários lugares", usuario.id)
                    auditoria.registrar(db, "auth.conta_bloqueada", "usuario", usuario.id, usuario=usuario)
        db.commit()
        raise credenciais_invalidas

    falhas_login.limpar(email)
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
def usuario_logado(usuario: Usuario = Depends(get_usuario_sessao)) -> Usuario:
    """`trocar_senha: true` -> o front deve mostrar só a tela de troca de senha."""
    return usuario


@router.post("/alterar-senha", status_code=status.HTTP_204_NO_CONTENT)
def alterar_senha(
    payload: AlterarSenha,
    response: Response,
    usuario: Usuario = Depends(get_usuario_sessao),
    db: Session = Depends(get_db),
) -> None:
    """Também é o caminho da troca obrigatória (senha definida pelo admin)."""
    if usuario.provedor_auth != ProvedorAuth.LOCAL or not verificar_senha(
        payload.senha_atual, usuario.senha_hash
    ):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha atual incorreta.")
    try:
        senhas.validar_senha(payload.nova_senha, usuario.email, usuario.nome)
    except senhas.SenhaFraca as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    usuario.senha_hash = gerar_hash_senha(payload.nova_senha)
    usuario.trocar_senha = False
    # Derruba todos os dispositivos: refresh tokens revogados e access tokens em uso recusados na hora.
    services.derrubar_sessoes(db, usuario)
    auditoria.registrar(db, "auth.senha_alterada", "usuario", usuario.id, usuario=usuario)
    db.commit()
    _apagar_cookie(response)
