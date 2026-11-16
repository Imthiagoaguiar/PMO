"""
Marcos da linha do tempo: pins, bandeiras, formas geométricas e faixas (períodos) que marcam eventos
importantes do projeto (pré-entrega, go-live, auditoria, congelamento...).

Aparecem na aba "Linha do tempo" (o editor cria, arrasta para mudar a data e edita) e no relatório PDF.
"""

from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import auditoria
from app.database import get_db
from app.deps import (
    IdPath,
    conferir_versao,
    exigir_projeto_aberto,
    get_usuario_atual,
    obter_projeto_editavel,
    obter_projeto_visivel,
    salvar,
)
from app.models import MAX_MARCOS, FormaMarco, MarcoLinhaTempo, Projeto, Usuario
from app.schemas import MarcoCreate, MarcoRead, MarcoUpdate, conferir_periodo_do_marco

router = APIRouter(prefix="/projetos/{projeto_id}/marcos", tags=["Marcos da linha do tempo"])


def _marco(db: Session, projeto: Projeto, marco_id: int) -> MarcoLinhaTempo:
    marco = db.scalar(select(MarcoLinhaTempo).where(MarcoLinhaTempo.id == marco_id, MarcoLinhaTempo.projeto_id == projeto.id))
    if marco is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marco não encontrado.")
    return marco


@router.get("", response_model=List[MarcoRead])
def listar_marcos(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MarcoLinhaTempo]:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    return list(db.scalars(
        select(MarcoLinhaTempo).where(MarcoLinhaTempo.projeto_id == projeto.id)
        .order_by(MarcoLinhaTempo.data, MarcoLinhaTempo.id)
    ))


@router.post("", response_model=MarcoRead, status_code=status.HTTP_201_CREATED)
def criar_marco(
    projeto_id: IdPath,
    payload: MarcoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> MarcoLinhaTempo:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    total = db.scalar(select(func.count()).select_from(MarcoLinhaTempo)
                      .where(MarcoLinhaTempo.projeto_id == projeto.id)) or 0
    if total >= MAX_MARCOS:
        raise HTTPException(status_code=422, detail=f"Limite de {MAX_MARCOS} marcos por projeto.")
    marco = MarcoLinhaTempo(projeto_id=projeto.id, criado_por_id=usuario.id, **payload.model_dump())
    db.add(marco)
    db.flush()
    auditoria.registrar(db, "marco.criado", "marco", marco.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"titulo": marco.titulo, "data": marco.data.isoformat(), "forma": marco.forma.value})
    salvar(db)
    return marco


@router.patch("/{marco_id}", response_model=MarcoRead)
def atualizar_marco(
    projeto_id: IdPath,
    marco_id: IdPath,
    payload: MarcoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> MarcoLinhaTempo:
    """Também é o "arrastar" na linha do tempo: muda `data` (e `data_fim`, na faixa) e manda a versão lida."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    marco = _marco(db, projeto, marco_id)
    alteracoes = payload.model_dump(exclude_unset=True)
    conferir_versao(marco, alteracoes.pop("versao", None))
    # Virar marco de um dia só apaga o fim do período (em vez de recusar a mudança de forma).
    if alteracoes.get("forma") not in (None, FormaMarco.FAIXA):
        alteracoes["data_fim"] = None
    forma = alteracoes.get("forma", marco.forma)
    data = alteracoes.get("data", marco.data)
    data_fim = alteracoes.get("data_fim", marco.data_fim)
    try:
        conferir_periodo_do_marco(forma, data, data_fim)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc).capitalize() + ".")
    diff = auditoria.aplicar_alteracoes(marco, alteracoes)
    if diff:
        auditoria.registrar(db, "marco.atualizado", "marco", marco.id, usuario=usuario, projeto_id=projeto.id,
                            detalhes={"marco": marco.titulo, **diff})
        salvar(db)
    return marco


@router.delete("/{marco_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_marco(
    projeto_id: IdPath,
    marco_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    marco = _marco(db, projeto, marco_id)
    auditoria.registrar(db, "marco.excluido", "marco", marco.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"titulo": marco.titulo})
    db.delete(marco)
    salvar(db)
