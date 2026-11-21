"""
Anexos das tarefas e o histórico de arquivos do projeto.

- Editores enviam e excluem; todos os membros veem e baixam (o download fica na auditoria).
- Até ANEXOS_MAX_MB por arquivo e ANEXOS_MAX_PROJETO_MB por projeto; programas e scripts são recusados.
- Excluir a tarefa não apaga os anexos: eles ficam no histórico do projeto com o nome da tarefa.
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import anexos, auditoria
from app.config import settings
from app.database import get_db
from app.deps import (
    IdPath,
    exigir_projeto_aberto,
    get_usuario_atual,
    obter_projeto_editavel,
    obter_projeto_visivel,
    salvar,
)
from app.models import AnexoTarefa, Fase, Projeto, Tarefa, Usuario
from app.routers.projetos import content_disposition
from app.schemas import AnexoRead, Pagina
from app.security import ID_MAXIMO

router = APIRouter(prefix="/projetos/{projeto_id}", tags=["Anexos"])
MB = 1024 * 1024


def _anexo(db: Session, projeto: Projeto, anexo_id: int) -> AnexoTarefa:
    anexo = db.scalar(select(AnexoTarefa).where(AnexoTarefa.id == anexo_id, AnexoTarefa.projeto_id == projeto.id))
    if anexo is None:  # também barra anexo de OUTRO projeto (IDOR)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Arquivo não encontrado.")
    return anexo


@router.get("/anexos", response_model=Pagina[AnexoRead])
def listar_anexos(
    projeto_id: IdPath,
    tarefa_id: Optional[int] = Query(None, ge=1, le=ID_MAXIMO, description="Só os anexos desta tarefa"),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000_000),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Histórico de arquivos do projeto (ou de uma tarefa), os mais recentes primeiro."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    consulta = select(AnexoTarefa).where(AnexoTarefa.projeto_id == projeto.id)
    if tarefa_id is not None:
        consulta = consulta.where(AnexoTarefa.tarefa_id == tarefa_id)
    total = db.scalar(select(func.count()).select_from(consulta.subquery())) or 0
    itens = list(db.scalars(
        consulta.options(selectinload(AnexoTarefa.enviado_por))
        .order_by(AnexoTarefa.enviado_em.desc(), AnexoTarefa.id.desc()).limit(limite).offset(offset)
    ))
    return {"itens": itens, "total": total, "limite": limite, "offset": offset}


@router.post("/tarefas/{tarefa_id}/anexos", response_model=AnexoRead, status_code=status.HTTP_201_CREATED)
def enviar_anexo(
    projeto_id: IdPath,
    tarefa_id: IdPath,
    arquivo: UploadFile = File(..., description="O arquivo (até ANEXOS_MAX_MB)"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> AnexoTarefa:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    tarefa = db.scalar(select(Tarefa).join(Fase).where(Tarefa.id == tarefa_id, Fase.projeto_id == projeto.id))
    if tarefa is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarefa não encontrada.")
    nome = anexos.nome_seguro(arquivo.filename or "")
    usado = db.scalar(select(func.coalesce(func.sum(AnexoTarefa.tamanho), 0)).where(AnexoTarefa.projeto_id == projeto.id)) or 0
    cota = settings.anexos_max_projeto_mb * MB
    try:
        anexos.validar_nome(nome)
        if usado >= cota:
            raise anexos.AnexoInvalido(f"O projeto chegou ao limite de {settings.anexos_max_projeto_mb} MB em arquivos.", 413)
        chave, tamanho, sha256 = anexos.gravar(projeto.id, arquivo.file, min(settings.anexos_max_mb * MB, cota - usado))
    except anexos.AnexoInvalido as exc:
        raise HTTPException(status_code=exc.status, detail=str(exc))

    anexo = AnexoTarefa(
        projeto_id=projeto.id, tarefa_id=tarefa.id, tarefa_descricao=tarefa.descricao[:500], nome_original=nome,
        chave=chave, tamanho=tamanho, tipo_mime=anexos.tipo_mime(nome), sha256=sha256, enviado_por_id=usuario.id,
    )
    db.add(anexo)
    try:
        db.flush()
        auditoria.registrar(db, "anexo.enviado", "anexo", anexo.id, usuario=usuario, projeto_id=projeto.id,
                            detalhes={"arquivo": nome, "tarefa": tarefa.descricao, "bytes": tamanho})
        salvar(db)
    except BaseException:
        anexos.apagar(projeto.id, [chave])  # não fica arquivo sem registro
        raise
    db.refresh(anexo, ["enviado_por"])
    return anexo


@router.get("/anexos/{anexo_id}/download", response_class=FileResponse)
def baixar_anexo(
    projeto_id: IdPath,
    anexo_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> FileResponse:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    anexo = _anexo(db, projeto, anexo_id)
    arquivo = anexos.caminho(projeto.id, anexo.chave)
    if not arquivo.is_file():
        raise HTTPException(status_code=status.HTTP_410_GONE, detail="O arquivo não está mais no servidor.")
    auditoria.registrar(db, "anexo.baixado", "anexo", anexo.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"arquivo": anexo.nome_original})
    salvar(db)
    return FileResponse(
        arquivo, media_type=anexo.tipo_mime,
        headers={
            "Content-Disposition": content_disposition(anexo.nome_original),
            "X-Content-Type-Options": "nosniff",
            # Mesmo aberto direto na aba, nada roda na origem do PMO (HTML/SVG anexado viram inofensivos).
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )


@router.delete("/anexos/{anexo_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_anexo(
    projeto_id: IdPath,
    anexo_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    anexo = _anexo(db, projeto, anexo_id)
    chave = anexo.chave
    auditoria.registrar(db, "anexo.excluido", "anexo", anexo.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"arquivo": anexo.nome_original, "tarefa": anexo.tarefa_descricao})
    db.delete(anexo)
    salvar(db)
    anexos.apagar(projeto.id, [chave])
