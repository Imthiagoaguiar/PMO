"""
Modelos de apresentação da empresa (.pptx/.potx com cores, fontes e logotipo).

Todos os usuários listam e usam no montador de slides; só administradores enviam e excluem.
O arquivo é validado e normalizado (app/modelos_pptx.py) e guardado em ANEXOS_DIR/modelos (entra no backup).
"""

import secrets
from typing import List

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import auditoria, modelos_pptx
from app.database import get_db
from app.deps import IdPath, get_usuario_atual, salvar
from app.models import ModeloApresentacao, PapelUsuario, Usuario
from app.schemas import ModeloApresentacaoRead

router = APIRouter(prefix="/modelos-apresentacao", tags=["Modelos de apresentação"])
MAX_MB = 20
MAX_MODELOS = 20


def _exigir_admin(usuario: Usuario) -> None:
    if usuario.papel != PapelUsuario.ADMIN:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Só administradores gerenciam os modelos da empresa.")


@router.get("", response_model=List[ModeloApresentacaoRead])
def listar_modelos(db: Session = Depends(get_db), usuario: Usuario = Depends(get_usuario_atual)) -> List[ModeloApresentacao]:
    return list(db.scalars(select(ModeloApresentacao).options(selectinload(ModeloApresentacao.enviado_por))
                           .order_by(ModeloApresentacao.nome, ModeloApresentacao.id)))


@router.post("", response_model=ModeloApresentacaoRead, status_code=status.HTTP_201_CREATED)
def enviar_modelo(
    arquivo: UploadFile = File(..., description=".pptx ou .potx da empresa (até 20 MB)"),
    nome: str = Form(..., min_length=1, max_length=100),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ModeloApresentacao:
    _exigir_admin(usuario)
    if (db.scalar(select(func.count()).select_from(ModeloApresentacao)) or 0) >= MAX_MODELOS:
        raise HTTPException(status_code=422, detail=f"Limite de {MAX_MODELOS} modelos. Exclua um antes de enviar outro.")
    conteudo = arquivo.file.read(MAX_MB * 1024 * 1024 + 1)
    if len(conteudo) > MAX_MB * 1024 * 1024:
        raise HTTPException(status_code=413, detail=f"O modelo passa de {MAX_MB} MB.")
    try:
        limpo, _, _ = modelos_pptx.normalizar(conteudo)
    except modelos_pptx.ModeloInvalido as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    chave = secrets.token_hex(16)
    destino = modelos_pptx.caminho(chave)
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_bytes(limpo)
    modelo = ModeloApresentacao(nome=" ".join(nome.split())[:100], chave=chave, tamanho=len(limpo), enviado_por_id=usuario.id)
    db.add(modelo)
    try:
        db.flush()
        auditoria.registrar(db, "modelo_apresentacao.enviado", "modelo_apresentacao", modelo.id, usuario=usuario,
                            detalhes={"nome": modelo.nome, "bytes": len(limpo)})
        salvar(db)
    except BaseException:
        destino.unlink(missing_ok=True)
        raise
    db.refresh(modelo, ["enviado_por"])
    return modelo


@router.delete("/{modelo_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_modelo(modelo_id: IdPath, db: Session = Depends(get_db), usuario: Usuario = Depends(get_usuario_atual)) -> None:
    _exigir_admin(usuario)
    modelo = db.get(ModeloApresentacao, modelo_id)
    if modelo is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Modelo não encontrado.")
    chave = modelo.chave
    auditoria.registrar(db, "modelo_apresentacao.excluido", "modelo_apresentacao", modelo.id, usuario=usuario,
                        detalhes={"nome": modelo.nome})
    db.delete(modelo)
    salvar(db)
    modelos_pptx.caminho(chave).unlink(missing_ok=True)
