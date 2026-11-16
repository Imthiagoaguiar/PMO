"""
Dependências entre tarefas do mesmo projeto ("só consigo fazer B depois de terminar A").

As regras ficam em app/dependencias.py; aqui só as rotas. O bloqueio vale também no PATCH da tarefa
(app/routers/tarefas.py): avançar B antes de A terminar é recusado com a explicação.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auditoria, dependencias
from app.database import get_db
from app.deps import (
    IdPath,
    exigir_projeto_aberto,
    get_usuario_atual,
    obter_projeto_editavel,
    obter_projeto_visivel,
    salvar,
)
from app.models import DependenciaTarefa, Fase, Projeto, Tarefa, Usuario
from app.schemas import DependenciaCreate, DependenciaCriada, DependenciaRead, Reprogramacao, TarefaRead

router = APIRouter(prefix="/projetos/{projeto_id}/dependencias", tags=["Dependências"])

JA_EXISTE = "Essa dependência já existe."


def _tarefa(db: Session, projeto: Projeto, tarefa_id: int) -> Tarefa:
    tarefa = db.scalar(select(Tarefa).join(Fase).where(Tarefa.id == tarefa_id, Fase.projeto_id == projeto.id))
    if tarefa is None:  # também barra tarefa de OUTRO projeto (IDOR)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarefa não encontrada neste projeto.")
    return tarefa


@router.get("", response_model=List[DependenciaRead])
def listar_dependencias(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[DependenciaTarefa]:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    return list(db.scalars(
        select(DependenciaTarefa).where(DependenciaTarefa.projeto_id == projeto.id).order_by(DependenciaTarefa.id)
    ))


@router.post("", response_model=DependenciaCriada, status_code=status.HTTP_201_CREATED)
def criar_dependencia(
    projeto_id: IdPath,
    payload: DependenciaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> DependenciaCriada:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    if payload.predecessora_id == payload.sucessora_id:
        raise HTTPException(status_code=422, detail="Uma tarefa não pode depender dela mesma.")
    dependencias.travar_projeto(db, projeto)
    pred = _tarefa(db, projeto, payload.predecessora_id)
    suc = _tarefa(db, projeto, payload.sucessora_id)

    existente = db.scalar(select(DependenciaTarefa.id).where(
        DependenciaTarefa.predecessora_id == pred.id, DependenciaTarefa.sucessora_id == suc.id))
    if existente is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=JA_EXISTE)
    total = db.scalar(select(func.count()).select_from(DependenciaTarefa)
                      .where(DependenciaTarefa.projeto_id == projeto.id)) or 0
    if total >= dependencias.MAX_DEPENDENCIAS:
        raise HTTPException(status_code=422, detail=f"Limite de {dependencias.MAX_DEPENDENCIAS} dependências por projeto.")
    if dependencias.criaria_ciclo(db, projeto.id, pred.id, suc.id):
        raise HTTPException(
            status_code=422,
            detail=f"Isso criaria um ciclo: “{suc.descricao}” já vem antes de “{pred.descricao}” (direta ou indiretamente).",
        )
    motivo = dependencias.violacao(payload.tipo, pred, suc)
    if motivo:
        raise HTTPException(status_code=422, detail=f"A dependência já começaria quebrada: {motivo}")

    dep = DependenciaTarefa(projeto_id=projeto.id, predecessora_id=pred.id, sucessora_id=suc.id, tipo=payload.tipo)
    db.add(dep)
    db.flush()
    mudadas = dependencias.reprogramar(db, projeto.id, [pred.id]) if payload.ajustar_datas else []
    auditoria.registrar(
        db, "dependencia.criada", "dependencia", dep.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"predecessora": pred.descricao, "sucessora": suc.descricao, "tipo": dep.tipo.value,
                  "reprogramadas": len(mudadas)},
    )
    salvar(db, conflito=JA_EXISTE)
    return DependenciaCriada(
        dependencia=DependenciaRead.model_validate(dep),
        reprogramadas=[TarefaRead.model_validate(t) for t in mudadas],
    )


@router.delete("/{dependencia_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_dependencia(
    projeto_id: IdPath,
    dependencia_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    dep = db.scalar(select(DependenciaTarefa).where(
        DependenciaTarefa.id == dependencia_id, DependenciaTarefa.projeto_id == projeto.id))
    if dep is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Dependência não encontrada.")
    auditoria.registrar(
        db, "dependencia.excluida", "dependencia", dep.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"predecessora": dep.predecessora.descricao, "sucessora": dep.sucessora.descricao},
    )
    db.delete(dep)
    salvar(db)


@router.post("/reprogramar", response_model=Reprogramacao)
def reprogramar_cronograma(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Reprogramacao:
    """Depois de mexer nas datas: empurra todas as sucessoras que ficaram antes das predecessoras."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    dependencias.travar_projeto(db, projeto)
    mudadas = dependencias.reprogramar(db, projeto.id)
    if mudadas:
        auditoria.registrar(db, "cronograma.reprogramado", "projeto", projeto.id, usuario=usuario,
                            projeto_id=projeto.id, detalhes={"tarefas": len(mudadas)})
        salvar(db)
    return Reprogramacao(reprogramadas=[TarefaRead.model_validate(t) for t in mudadas])
