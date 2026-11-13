"""Consulta do log de auditoria completo (somente Admin)."""

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.deps import Paginacao, exigir_papel, paginar
from app.models import Auditoria, PapelUsuario, Usuario
from app.schemas import AuditoriaRead, Pagina

router = APIRouter(prefix="/auditoria", tags=["Auditoria"])


@router.get("", response_model=Pagina[AuditoriaRead])
def listar_auditoria(
    usuario_id: Optional[int] = None,
    projeto_id: Optional[int] = None,
    acao: Optional[str] = Query(None, max_length=50, description="Ex.: auth.login_falhou, projeto.excluido"),
    desde: Optional[datetime] = None,
    ate: Optional[datetime] = None,
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    _: Usuario = Depends(exigir_papel(PapelUsuario.ADMIN)),
) -> Dict[str, Any]:
    query = select(Auditoria).options(selectinload(Auditoria.usuario))
    if usuario_id is not None:
        query = query.where(Auditoria.usuario_id == usuario_id)
    if projeto_id is not None:
        query = query.where(Auditoria.projeto_id == projeto_id)
    if acao:
        query = query.where(Auditoria.acao == acao)
    if desde:
        query = query.where(Auditoria.criado_em >= desde)
    if ate:
        query = query.where(Auditoria.criado_em <= ate)
    return paginar(db, query.order_by(Auditoria.criado_em.desc(), Auditoria.id.desc()), pagina)
