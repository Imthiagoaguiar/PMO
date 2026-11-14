"""
Relatórios do vigia (python -m app.vigia). Só para admins listados em VIGIA_EMAILS: para qualquer
outra pessoa (inclusive outros admins) as rotas respondem 404, como se não existissem.
"""

from datetime import timedelta
from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.deps import IdPath, Paginacao, get_usuario_atual, paginar
from app.models import Usuario, VigiaRodada
from app.schemas import Pagina, VigiaRodadaRead, VigiaRodadaResumo, VigiaSituacao
from app.security import agora_utc, como_utc

router = APIRouter(prefix="/vigia", tags=["Vigia"])


def exigir_vigia(usuario: Usuario = Depends(get_usuario_atual)) -> Usuario:
    if not usuario.pode_ver_vigia:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Não encontrado.")
    return usuario


def _resumo(r: VigiaRodada) -> Dict[str, Any]:
    return {**VigiaRodadaResumo.model_validate(r).model_dump(), "total_achados": len(r.achados or [])}


@router.get("/situacao", response_model=VigiaSituacao)
def situacao(db: Session = Depends(get_db), _: Usuario = Depends(exigir_vigia)) -> VigiaSituacao:
    ultima = db.scalar(select(VigiaRodada.iniciada_em).order_by(VigiaRodada.iniciada_em.desc()).limit(1))
    intervalo = timedelta(minutes=settings.vigia_intervalo_minutos)
    return VigiaSituacao(
        intervalo_minutos=settings.vigia_intervalo_minutos,
        ultima_rodada_em=ultima,
        atrasado=ultima is None or agora_utc() - como_utc(ultima) > 2 * intervalo,
    )


@router.get("/rodadas", response_model=Pagina[VigiaRodadaResumo])
def listar_rodadas(
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    _: Usuario = Depends(exigir_vigia),
) -> Dict[str, Any]:
    """Mais recentes primeiro."""
    resultado = paginar(db, select(VigiaRodada).order_by(VigiaRodada.iniciada_em.desc(), VigiaRodada.id.desc()), pagina)
    resultado["itens"] = [_resumo(r) for r in resultado["itens"]]
    return resultado


@router.get("/rodadas/{rodada_id}", response_model=VigiaRodadaRead)
def detalhar_rodada(rodada_id: IdPath, db: Session = Depends(get_db), _: Usuario = Depends(exigir_vigia)) -> Dict[str, Any]:
    r = db.get(VigiaRodada, rodada_id)
    if r is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Não encontrado.")
    return {**VigiaRodadaRead.model_validate(r).model_dump(), "total_achados": len(r.achados or [])}
