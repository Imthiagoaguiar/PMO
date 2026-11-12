"""
Gestão de usuários (somente Admin).

Contas locais entram com e-mail e senha. Contas do Clerk são pré-cadastradas aqui só com
e-mail e papel; no primeiro login pelo Clerk com esse e-mail, a conta é vinculada.
Com CLERK_CADASTRO_AUTOMATICO=false, esse pré-cadastro é a ÚNICA forma de entrar pelo Clerk.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auditoria, services
from app.integracoes import clerk
from app.database import get_db
from app.deps import Paginacao, escapar_like, exigir_papel, paginar, salvar
from app.models import PapelUsuario, ProvedorAuth, Usuario
from app.schemas import Pagina, UsuarioCreate, UsuarioRead, UsuarioUpdate
from app.security import gerar_hash_senha

router = APIRouter(prefix="/usuarios", tags=["Usuários"])

somente_admin = exigir_papel(PapelUsuario.ADMIN)


@router.post("", response_model=UsuarioRead, status_code=status.HTTP_201_CREATED)
def criar_usuario(
    payload: UsuarioCreate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(somente_admin),
) -> Usuario:
    if payload.provedor_auth == ProvedorAuth.CLERK and not clerk.habilitado():
        # Evita criar uma conta que ninguém consegue usar.
        raise HTTPException(status_code=422, detail="O login pelo Clerk não está configurado (CLERK_EMISSOR).")
    if db.scalar(select(Usuario.id).where(Usuario.email == payload.email)):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado.")

    usuario = Usuario(
        email=payload.email,
        nome=payload.nome,
        senha_hash=gerar_hash_senha(payload.senha) if payload.senha else None,
        papel=payload.papel,
        provedor_auth=payload.provedor_auth,
    )
    db.add(usuario)
    try:
        db.flush()
    except IntegrityError:  # corrida: outro admin criou o mesmo e-mail ao mesmo tempo
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="E-mail já cadastrado.")
    auditoria.registrar(
        db, "usuario.criado", "usuario", usuario.id, usuario=admin,
        detalhes={"email": usuario.email, "papel": usuario.papel, "provedor": usuario.provedor_auth},
    )
    salvar(db)
    return usuario


@router.get("", response_model=Pagina[UsuarioRead])
def listar_usuarios(
    busca: Optional[str] = Query(None, max_length=100, description="Parte do nome ou e-mail"),
    papel: Optional[PapelUsuario] = None,
    ativo: Optional[bool] = None,
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    _: Usuario = Depends(somente_admin),
) -> Dict[str, Any]:
    query = select(Usuario)
    if busca and busca.strip():
        termo = f"%{escapar_like(busca.strip())}%"
        query = query.where(
            or_(Usuario.nome.ilike(termo, escape="\\"), Usuario.email.ilike(termo, escape="\\"))
        )
    if papel:
        query = query.where(Usuario.papel == papel)
    if ativo is not None:
        query = query.where(Usuario.ativo == ativo)
    return paginar(db, query.order_by(Usuario.nome, Usuario.id), pagina)


@router.patch("/{usuario_id}", response_model=UsuarioRead)
def atualizar_usuario(
    usuario_id: int,
    payload: UsuarioUpdate,
    db: Session = Depends(get_db),
    admin: Usuario = Depends(somente_admin),
) -> Usuario:
    usuario = db.get(Usuario, usuario_id)
    if usuario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")

    alteracoes = payload.model_dump(exclude_unset=True)
    if usuario.id == admin.id and (
        alteracoes.get("ativo") is False or alteracoes.get("papel", PapelUsuario.ADMIN) != PapelUsuario.ADMIN
    ):
        # Evita que o sistema fique sem nenhum administrador por engano.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Você não pode desativar nem rebaixar a sua própria conta.",
        )

    diff = auditoria.aplicar_alteracoes(usuario, alteracoes)
    if diff:
        if alteracoes.get("ativo") is False:
            services.revogar_sessoes(db, usuario.id)
        auditoria.registrar(db, "usuario.atualizado", "usuario", usuario.id, usuario=admin, detalhes=diff)
        salvar(db)
    return usuario
