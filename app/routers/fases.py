"""
Fases do projeto: o Waterfall (Iniciação... Encerramento) é só o modelo inicial.

- O editor cria, renomeia, reordena e exclui fases (até MAX_FASES por projeto, sem nomes repetidos).
- Excluir uma fase com tarefas exige dizer o destino: `mover_para` (outra fase) ou `excluir_tarefas=true`.
- "Seguir a ordem das fases" fica no próprio projeto (PATCH /projetos/{id}, campo seguir_ordem_fases).
Visualizadores leem (no detalhe do projeto); projeto encerrado fica somente leitura.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auditoria
from app.database import get_db
from app.deps import IdPath, exigir_projeto_aberto, get_usuario_atual, obter_projeto_editavel, salvar
from app.models import MAX_FASES, Fase, Projeto, Tarefa, Usuario, chave_da_fase
from app.schemas import FaseCreate, FaseRead, FaseUpdate, OrdemFases
from app.security import ID_MAXIMO

router = APIRouter(prefix="/projetos/{projeto_id}/fases", tags=["Fases"])

NOME_REPETIDO = "Já existe uma fase com esse nome neste projeto."
CONFLITO = "Outra pessoa alterou as fases deste projeto ao mesmo tempo. Recarregue a página e tente de novo."


def _fase_do_projeto(projeto: Projeto, fase_id: int) -> Fase:
    fase = next((f for f in projeto.fases if f.id == fase_id), None)
    if fase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fase não encontrada neste projeto.")
    return fase


def _exigir_nome_livre(projeto: Projeto, titulo: str, ignorar: Optional[Fase] = None) -> None:
    chave = chave_da_fase(titulo)
    if not chave:
        raise HTTPException(status_code=422, detail="Dê um nome à fase.")
    if any(f.titulo_chave == chave and f is not ignorar for f in projeto.fases):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOME_REPETIDO)


def _renumerar(db: Session, fases: List[Fase]) -> None:
    """
    Grava a ordem 1..N. A posição é única no banco: primeiro todas vão para posições negativas
    (que ninguém usa), depois para a definitiva; assim nenhum passo intermediário colide.
    """
    for fase in fases:
        fase.ordem_sequencia = -fase.id
    db.flush()
    for posicao, fase in enumerate(fases, start=1):
        fase.ordem_sequencia = posicao
    db.flush()


def _leitura(db: Session, fase: Fase) -> FaseRead:
    total = db.scalar(select(func.count()).select_from(Tarefa).where(Tarefa.fase_id == fase.id)) or 0
    return FaseRead(id=fase.id, titulo=fase.titulo, ordem_sequencia=fase.ordem_sequencia, total_tarefas=total)


@router.post("", response_model=FaseRead, status_code=status.HTTP_201_CREATED)
def criar_fase(
    projeto_id: IdPath,
    payload: FaseCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> FaseRead:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    if len(projeto.fases) >= MAX_FASES:
        raise HTTPException(status_code=422, detail=f"Limite de {MAX_FASES} fases por projeto.")
    _exigir_nome_livre(projeto, payload.titulo)

    ordem = list(projeto.fases)
    fase = Fase(projeto_id=projeto.id, titulo=payload.titulo, ordem_sequencia=-(len(ordem) + 1) * 1_000_000)
    db.add(fase)
    try:
        db.flush()
    except IntegrityError:  # mesmo nome criado por outra pessoa no mesmo instante
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOME_REPETIDO)
    posicao = min(payload.posicao or len(ordem) + 1, len(ordem) + 1)
    ordem.insert(posicao - 1, fase)
    try:
        _renumerar(db, ordem)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CONFLITO)
    auditoria.registrar(db, "fase.criada", "fase", fase.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"titulo": fase.titulo, "posicao": posicao})
    salvar(db, conflito=CONFLITO)
    return _leitura(db, fase)


@router.patch("/{fase_id}", response_model=FaseRead)
def renomear_fase(
    projeto_id: IdPath,
    fase_id: IdPath,
    payload: FaseUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> FaseRead:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    fase = _fase_do_projeto(projeto, fase_id)
    _exigir_nome_livre(projeto, payload.titulo, ignorar=fase)
    antes = fase.titulo
    if antes != payload.titulo:
        fase.titulo = payload.titulo
        auditoria.registrar(db, "fase.renomeada", "fase", fase.id, usuario=usuario, projeto_id=projeto.id,
                            detalhes={"titulo": [antes, fase.titulo]})
        salvar(db, conflito=NOME_REPETIDO)
    return _leitura(db, fase)


@router.put("/ordem", response_model=List[FaseRead])
def reordenar_fases(
    projeto_id: IdPath,
    payload: OrdemFases,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[FaseRead]:
    """Recebe TODAS as fases do projeto na nova ordem (ninguém fica de fora nem repetido)."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    por_id = {f.id: f for f in projeto.fases}
    if sorted(payload.ids) != sorted(por_id):
        raise HTTPException(
            status_code=422,
            detail="Envie todas as fases do projeto, cada uma uma vez. Se alguém criou ou excluiu uma fase agora, recarregue.",
        )
    antes = [f.titulo for f in projeto.fases]
    nova = [por_id[i] for i in payload.ids]
    if [f.id for f in projeto.fases] != payload.ids:
        try:
            _renumerar(db, nova)
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=CONFLITO)
        auditoria.registrar(db, "fase.reordenadas", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id,
                            detalhes={"ordem": [antes, [f.titulo for f in nova]]})
        salvar(db, conflito=CONFLITO)
    return [_leitura(db, f) for f in nova]


@router.delete("/{fase_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_fase(
    projeto_id: IdPath,
    fase_id: IdPath,
    mover_para: Optional[int] = Query(None, ge=1, le=ID_MAXIMO, description="Fase que recebe as tarefas desta"),
    excluir_tarefas: bool = Query(False, description="true: exclui a fase JUNTO com as tarefas dela"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    """
    Fase vazia: exclui direto. Com tarefas: é preciso escolher `mover_para` OU `excluir_tarefas=true`
    (sem escolha, 409 dizendo quantas tarefas a fase tem: nada some sem a pessoa decidir).
    """
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    fase = _fase_do_projeto(projeto, fase_id)
    if mover_para is not None and excluir_tarefas:
        raise HTTPException(status_code=422, detail="Escolha mover as tarefas OU excluí-las, não os dois.")
    destino = _fase_do_projeto(projeto, mover_para) if mover_para is not None else None
    if destino is fase:
        raise HTTPException(status_code=422, detail="Escolha outra fase para receber as tarefas.")

    total = db.scalar(select(func.count()).select_from(Tarefa).where(Tarefa.fase_id == fase.id)) or 0
    if total and destino is None and not excluir_tarefas:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A fase “{fase.titulo}” tem {total} tarefa(s). Escolha para qual fase movê-las ou confirme a exclusão junto.",
        )
    if destino is not None and total:
        # Soma 1 na versão: quem estava com a tarefa aberta não a devolve para a fase excluída.
        db.execute(
            update(Tarefa).where(Tarefa.fase_id == fase.id)
            .values(fase_id=destino.id, versao=Tarefa.versao + 1)
            .execution_options(synchronize_session=False)
        )
        db.expire(fase, ["tarefas"])
    elif excluir_tarefas and total:
        # Em lote (uma fase pode ter milhares de tarefas); comentários e dependências vão junto (ON DELETE CASCADE).
        db.execute(delete(Tarefa).where(Tarefa.fase_id == fase.id).execution_options(synchronize_session=False))
        db.expire(fase, ["tarefas"])
    auditoria.registrar(
        db, "fase.excluida", "fase", fase.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"titulo": fase.titulo, "tarefas": total,
                  "destino": destino.titulo if destino else ("excluídas" if total else None)},
    )
    restantes = [f for f in projeto.fases if f is not fase]
    db.delete(fase)
    db.flush()
    if fase in projeto.fases:
        projeto.fases.remove(fase)
    _renumerar(db, restantes)
    salvar(db, conflito=CONFLITO)
