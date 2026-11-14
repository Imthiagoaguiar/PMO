"""
Comentários no projeto ou numa tarefa.

- Qualquer membro (inclusive visualizador) lê e comenta, mesmo com o projeto encerrado
  (ex.: lições aprendidas no Encerramento).
- Só o autor edita o próprio texto. Autor, dono e admin podem excluir (moderação).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app import auditoria
from app.database import get_db
from app.deps import IdPath, Paginacao, get_usuario_atual, obter_projeto_visivel, paginar, pode_gerenciar, salvar
from app.models import Comentario, Fase, Projeto, Tarefa, Usuario
from app.schemas import ComentarioCreate, ComentarioRead, ComentarioUpdate, Pagina
from app.security import ID_MAXIMO, agora_utc

router = APIRouter(prefix="/projetos/{projeto_id}/comentarios", tags=["Comentários"])


def _garantir_tarefa_do_projeto(db: Session, projeto: Projeto, tarefa_id: int) -> None:
    existe = db.scalar(
        select(Tarefa.id).join(Fase).where(Tarefa.id == tarefa_id, Fase.projeto_id == projeto.id)
    )
    if existe is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarefa não encontrada.")


def _comentario_do_projeto(db: Session, projeto: Projeto, comentario_id: int) -> Comentario:
    comentario = db.scalar(
        select(Comentario).where(Comentario.id == comentario_id, Comentario.projeto_id == projeto.id)
    )
    if comentario is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Comentário não encontrado.")
    return comentario


@router.get("", response_model=Pagina[ComentarioRead])
def listar_comentarios(
    projeto_id: IdPath,
    tarefa_id: Optional[int] = Query(None, ge=1, le=ID_MAXIMO, description="Só os comentários desta tarefa"),
    somente_projeto: bool = Query(False, description="Só os comentários gerais (sem tarefa)"),
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Mais recentes primeiro."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    query = (
        select(Comentario)
        .where(Comentario.projeto_id == projeto.id)
        .options(selectinload(Comentario.autor))
        .order_by(Comentario.criado_em.desc(), Comentario.id.desc())
    )
    if tarefa_id is not None:
        query = query.where(Comentario.tarefa_id == tarefa_id)
    elif somente_projeto:
        query = query.where(Comentario.tarefa_id.is_(None))
    return paginar(db, query, pagina)


@router.post("", response_model=ComentarioRead, status_code=status.HTTP_201_CREATED)
def comentar(
    projeto_id: IdPath,
    payload: ComentarioCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Comentario:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    if payload.tarefa_id is not None:
        _garantir_tarefa_do_projeto(db, projeto, payload.tarefa_id)

    comentario = Comentario(
        projeto_id=projeto.id, tarefa_id=payload.tarefa_id, autor=usuario, texto=payload.texto
    )
    db.add(comentario)
    db.flush()
    auditoria.registrar(
        db, "comentario.criado", "comentario", comentario.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"tarefa_id": payload.tarefa_id},
    )
    salvar(db)
    return comentario


@router.patch("/{comentario_id}", response_model=ComentarioRead)
def editar_comentario(
    projeto_id: IdPath,
    comentario_id: IdPath,
    payload: ComentarioUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Comentario:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    comentario = _comentario_do_projeto(db, projeto, comentario_id)
    if comentario.autor_id != usuario.id:
        # Nem o admin reescreve o texto de outra pessoa: o comentário é dela.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Só o autor pode editar o comentário.")

    if payload.texto != comentario.texto:
        comentario.texto = payload.texto
        comentario.editado_em = agora_utc()
        auditoria.registrar(
            db, "comentario.editado", "comentario", comentario.id, usuario=usuario, projeto_id=projeto.id
        )
        salvar(db)
    return comentario


@router.delete("/{comentario_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_comentario(
    projeto_id: IdPath,
    comentario_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    comentario = _comentario_do_projeto(db, projeto, comentario_id)
    if comentario.autor_id != usuario.id and not pode_gerenciar(projeto, usuario):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Só o autor, o dono do projeto ou um administrador pode excluir o comentário.",
        )

    auditoria.registrar(
        db, "comentario.excluido", "comentario", comentario.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"autor_id": comentario.autor_id},
    )
    db.delete(comentario)
    salvar(db)
