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
from typing import Optional, Union

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
import jwt
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app import auditoria, dois_fatores, senhas, services
from app.config import settings
from app.database import get_db
from app.deps import get_usuario_sessao
from app.models import PapelUsuario, ProvedorAuth, RefreshToken, Usuario
from app.rate_limit import falhas_login, ip_de, limite_config_2fa, limite_login, limite_login_2fa, limite_refresh
from app.schemas import (
    SENHA_MAX,
    AlterarSenha,
    CodigoDoisFatores,
    CodigosRecuperacao,
    ConfiguracaoDoisFatores,
    ConfirmarSenha,
    ConfirmarSenhaECodigo,
    DesafioDoisFatores,
    LoginDoisFatores,
    StatusDoisFatores,
    TokenResposta,
    UsuarioRead,
)
from app.security import (
    DESAFIO_2FA_MINUTOS,
    agora_utc,
    como_utc,
    criar_access_token,
    criar_desafio_2fa,
    decodificar_desafio_2fa,
    gerar_hash_senha,
    gerar_refresh_token,
    hash_token,
    precisa_rehash,
    verificar_senha,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["Autenticação"])

COOKIE_REFRESH = "pmo_refresh"
# O navegador só envia o cookie para as rotas de /auth. Atrás de um proxy que põe a API em /api
# (deploy/Caddyfile), o caminho visto pelo navegador é /api/auth: COOKIE_PATH=/api/auth.
COOKIE_PATH = settings.cookie_path
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


def _registrar_falha(db: Session, usuario: Optional[Usuario], email: str, ip: str, acao: str) -> None:
    """Senha ou código errado: conta para o bloqueio por e-mail + IP e para o bloqueio da conta."""
    falhas_login.registrar(email, ip)
    auditoria.registrar(
        db, acao, "usuario", usuario.id if usuario else None, usuario=usuario, detalhes={"email": email}
    )
    if usuario:
        usuario.tentativas_falhas += 1
        if usuario.tentativas_falhas >= settings.max_tentativas_conta:
            usuario.bloqueado_ate = agora_utc() + timedelta(minutes=settings.bloqueio_minutos)
            usuario.tentativas_falhas = 0
            logger.warning("Conta %s bloqueada: tentativas de vários lugares", usuario.id)
            auditoria.registrar(db, "auth.conta_bloqueada", "usuario", usuario.id, usuario=usuario)


def _barrado(usuario: Optional[Usuario], email: str, ip: str) -> Optional[str]:
    motivo = falhas_login.motivo_bloqueio(email, ip)
    if motivo:
        return motivo
    if usuario and usuario.bloqueado_ate and como_utc(usuario.bloqueado_ate) > agora_utc():
        return "conta bloqueada"
    return None


def _concluir_login(db: Session, response: Response, usuario: Usuario, **detalhes: str) -> TokenResposta:
    falhas_login.limpar(usuario.email)
    usuario.tentativas_falhas = 0
    usuario.bloqueado_ate = None
    auditoria.registrar(db, "auth.login", "usuario", usuario.id, usuario=usuario, detalhes=detalhes or None)
    return _emitir_sessao(db, response, usuario)


@router.post(
    "/login",
    response_model=Union[TokenResposta, DesafioDoisFatores],
    dependencies=[Depends(limite_login)],
)
def login(
    request: Request,
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
) -> Union[TokenResposta, DesafioDoisFatores]:
    """
    Login com e-mail (campo `username`, padrão OAuth2) e senha.

    Toda falha devolve a MESMA resposta e leva o mesmo tempo (o Argon2 roda sempre): e-mail
    inexistente, senha errada, conta desativada ou bloqueada são indistinguíveis por fora.

    Com 2FA ativo, a senha certa devolve `{"requer_2fa": true, "desafio": ...}` e NENHUMA sessão:
    o acesso só sai em POST /auth/login/2fa, com o código do app.
    """
    credenciais_invalidas = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=MENSAGEM_FALHA_LOGIN)
    if len(form.password) > SENHA_MAX or len(form.username) > 254:
        raise credenciais_invalidas  # evita gastar CPU com Argon2 em entradas gigantes

    email = form.username.strip().lower()
    ip = ip_de(request)
    usuario = db.scalar(select(Usuario).where(Usuario.email == email))
    motivo_barrado = _barrado(usuario, email, ip)
    senha_correta = verificar_senha(form.password, usuario.senha_hash if usuario else None)

    if motivo_barrado or not senha_correta or not usuario.ativo:
        if motivo_barrado:
            # Tentativa durante o bloqueio: registra, mas não conta de novo (não prolonga o bloqueio).
            auditoria.registrar(
                db, "auth.login_barrado", "usuario", usuario.id if usuario else None, usuario=usuario,
                detalhes={"email": email, "motivo": motivo_barrado},
            )
        elif not senha_correta:
            _registrar_falha(db, usuario, email, ip, "auth.login_falhou")
        db.commit()
        raise credenciais_invalidas

    if precisa_rehash(usuario.senha_hash):
        usuario.senha_hash = gerar_hash_senha(form.password)

    if usuario.totp_ativo:
        # Os contadores de falha NÃO são zerados aqui: senão quem tem só a senha poderia
        # alternar "senha certa" e "código chutado" sem nunca ser bloqueado.
        db.commit()
        return DesafioDoisFatores(
            desafio=criar_desafio_2fa(usuario.id, usuario.versao_sessao),
            expira_em_segundos=DESAFIO_2FA_MINUTOS * 60,
        )
    return _concluir_login(db, response, usuario)


@router.post("/login/2fa", response_model=TokenResposta, dependencies=[Depends(limite_login_2fa)])
def login_segunda_etapa(
    payload: LoginDoisFatores,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> TokenResposta:
    """Segunda etapa: código de 6 dígitos do app (ou um código de recuperação)."""
    desafio_invalido = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED, detail="Verificação expirada. Entre novamente com e-mail e senha."
    )
    try:
        desafio = decodificar_desafio_2fa(payload.desafio)
    except jwt.InvalidTokenError:
        raise desafio_invalido
    usuario = db.get(Usuario, desafio.usuario_id)
    if (
        usuario is None
        or not usuario.ativo
        or not usuario.totp_ativo
        or usuario.versao_sessao != desafio.versao_sessao  # senha trocada / sessões encerradas no meio
    ):
        raise desafio_invalido

    # Quem chegou aqui já provou a senha: dá para dizer claramente que está bloqueado.
    ip = ip_de(request)
    if _barrado(usuario, usuario.email, ip):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Muitas tentativas. Aguarde alguns minutos e entre novamente.",
        )

    metodo = dois_fatores.verificar_codigo(db, usuario, payload.codigo)
    if not metodo:
        _registrar_falha(db, usuario, usuario.email, ip, "auth.2fa_falhou")
        db.commit()
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Código inválido ou já usado.")

    if metodo == "recuperacao":
        restantes = dois_fatores.codigos_restantes(db, usuario)  # o usado já foi marcado (UPDATE atômico)
        auditoria.registrar(
            db, "auth.codigo_recuperacao_usado", "usuario", usuario.id, usuario=usuario,
            detalhes={"restantes": restantes},
        )
    return _concluir_login(db, response, usuario, metodo=metodo)


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
        if registro.substituido_em is not None and agora_utc() - como_utc(registro.substituido_em) <= timedelta(
            seconds=settings.janela_renovacao_paralela_segundos
        ):
            # Duas abas renovaram juntas com o mesmo cookie: a outra já trocou o token e o navegador
            # já guardou o cookie novo. Não é roubo (e nenhum token sai daqui): só tentar de novo.
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A sessão acabou de ser renovada em outra aba. Tente novamente.",
            )
        # Token já usado/revogado apareceu de novo bem depois: provável roubo. Derruba todas as sessões.
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

    # Rotação: cada refresh token vale uma única vez. `substituido_em` distingue a troca normal
    # (que abre a janela de corrida entre abas) de uma revogação por logout/troca de senha.
    # UPDATE condicional (atômico): com 5 renovações simultâneas do mesmo cookie, todas passam pela
    # checagem acima, mas só a 1ª consegue revogar o token; as outras são a "outra aba" (409).
    agora = agora_utc()
    trocado = db.execute(
        update(RefreshToken)
        .where(RefreshToken.id == registro.id, RefreshToken.revogado_em.is_(None))
        .values(revogado_em=agora, substituido_em=agora)
        .execution_options(synchronize_session=False)
    )
    if trocado.rowcount != 1:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A sessão acabou de ser renovada em outra aba. Tente novamente.",
        )
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


# ---------- Configuração do 2FA (pelo próprio usuário) ----------


def _exigir_login_local(usuario: Usuario) -> None:
    if usuario.provedor_auth != ProvedorAuth.LOCAL:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Sua conta entra por um provedor externo: configure a verificação em duas etapas nele.",
        )


def _conferir_senha(usuario: Usuario, senha: str) -> None:
    # Pedir a senha de novo impede que alguém com um token roubado cadastre o próprio celular
    # (ou desligue o 2FA) e tome a conta.
    if not verificar_senha(senha, usuario.senha_hash):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Senha incorreta.")


def _conferir_codigo(db: Session, usuario: Usuario, codigo: str) -> None:
    if not dois_fatores.verificar_codigo(db, usuario, codigo):
        db.commit()  # grava o passo/código consumido mesmo na falha seguinte
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Código inválido ou já usado.")


@router.get("/2fa", response_model=StatusDoisFatores)
def status_2fa(usuario: Usuario = Depends(get_usuario_sessao), db: Session = Depends(get_db)) -> StatusDoisFatores:
    return StatusDoisFatores(
        ativo=usuario.totp_ativo,
        obrigatorio=settings.exigir_2fa_admin and usuario.papel == PapelUsuario.ADMIN,
        codigos_restantes=dois_fatores.codigos_restantes(db, usuario) if usuario.totp_ativo else 0,
    )


@router.post("/2fa/iniciar", response_model=ConfiguracaoDoisFatores)
def iniciar_2fa(
    payload: ConfirmarSenha,
    usuario: Usuario = Depends(get_usuario_sessao),
    db: Session = Depends(get_db),
) -> ConfiguracaoDoisFatores:
    """Passo 1: gera o segredo (QR code). Só passa a valer depois de confirmado em /2fa/ativar."""
    limite_config_2fa.por_usuario(usuario.id)
    _exigir_login_local(usuario)
    if usuario.totp_ativo:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A verificação em duas etapas já está ativa.")
    _conferir_senha(usuario, payload.senha)
    segredo = dois_fatores.iniciar_configuracao(usuario)
    db.commit()
    return ConfiguracaoDoisFatores(segredo=segredo, uri=dois_fatores.uri_provisionamento(usuario, segredo))


@router.post("/2fa/ativar", response_model=CodigosRecuperacao)
def ativar_2fa(
    payload: CodigoDoisFatores,
    response: Response,
    usuario: Usuario = Depends(get_usuario_sessao),
    db: Session = Depends(get_db),
) -> CodigosRecuperacao:
    """
    Passo 2: confirma que o app está gerando os códigos certos e ativa.
    Devolve os códigos de recuperação (únicos e mostrados só agora) e encerra todas as sessões:
    as próximas entradas já passam pela verificação em duas etapas.
    """
    limite_config_2fa.por_usuario(usuario.id)
    _exigir_login_local(usuario)
    if usuario.totp_ativo:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="A verificação em duas etapas já está ativa.")
    if not usuario.totp_segredo_cifrado:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Comece pela leitura do QR code.")
    if not dois_fatores.verificar_totp(db, usuario, payload.codigo):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Código não confere. Verifique se o relógio do celular está certo e tente o código atual.",
        )

    usuario.totp_ativo = True
    codigos = dois_fatores.gerar_codigos_recuperacao(db, usuario)
    services.derrubar_sessoes(db, usuario)
    auditoria.registrar(db, "auth.2fa_ativado", "usuario", usuario.id, usuario=usuario)
    db.commit()
    _apagar_cookie(response)
    return CodigosRecuperacao(codigos_recuperacao=codigos)


@router.post("/2fa/codigos", response_model=CodigosRecuperacao)
def novos_codigos_recuperacao(
    payload: ConfirmarSenhaECodigo,
    usuario: Usuario = Depends(get_usuario_sessao),
    db: Session = Depends(get_db),
) -> CodigosRecuperacao:
    """Gera 10 códigos novos; os anteriores deixam de valer."""
    limite_config_2fa.por_usuario(usuario.id)
    if not usuario.totp_ativo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A verificação em duas etapas não está ativa.")
    _conferir_senha(usuario, payload.senha)
    _conferir_codigo(db, usuario, payload.codigo)
    codigos = dois_fatores.gerar_codigos_recuperacao(db, usuario)
    auditoria.registrar(db, "auth.codigos_recuperacao_gerados", "usuario", usuario.id, usuario=usuario)
    db.commit()
    return CodigosRecuperacao(codigos_recuperacao=codigos)


@router.post("/2fa/desativar", status_code=status.HTTP_204_NO_CONTENT)
def desativar_2fa(
    payload: ConfirmarSenhaECodigo,
    usuario: Usuario = Depends(get_usuario_sessao),
    db: Session = Depends(get_db),
) -> None:
    limite_config_2fa.por_usuario(usuario.id)
    if not usuario.totp_ativo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="A verificação em duas etapas não está ativa.")
    if settings.exigir_2fa_admin and usuario.papel == PapelUsuario.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Administradores precisam manter a verificação em duas etapas.",
        )
    _conferir_senha(usuario, payload.senha)
    _conferir_codigo(db, usuario, payload.codigo)
    dois_fatores.desativar(db, usuario)
    auditoria.registrar(db, "auth.2fa_desativado", "usuario", usuario.id, usuario=usuario)
    db.commit()
