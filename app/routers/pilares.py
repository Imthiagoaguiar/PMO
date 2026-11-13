"""
Pilares do projeto: frentes de trabalho que cruzam as 5 fases (TI, Suprimentos, CAPEX, Pessoas...).

- Todo projeto nasce com os pilares padrão; o editor renomeia, reordena, remove ou cria outros.
- Cada tarefa pode pertencer a um pilar: o andamento do pilar é calculado pelas tarefas dele.
- Farol (verde/amarelo/vermelho) + texto de status: a avaliação do responsável pela frente.
- Orçado x realizado (R$): execução financeira da frente (ex.: CAPEX).
Visualizadores leem; editores alteram. Projeto encerrado fica somente leitura.
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import auditoria, services
from app.database import get_db
from app.deps import (
    conferir_versao,
    exigir_projeto_aberto,
    get_usuario_atual,
    obter_projeto_editavel,
    obter_projeto_visivel,
    salvar,
)
from app.models import MAX_PILARES, Pilar, Projeto, Tarefa, Usuario, chave_do_pilar
from app.schemas import PilarCreate, PilarRead, PilarUpdate
from app.security import agora_utc

router = APIRouter(prefix="/projetos/{projeto_id}/pilares", tags=["Pilares"])

NOME_REPETIDO = "Já existe um pilar com esse nome neste projeto."


def _pilar_do_projeto(db: Session, projeto: Projeto, pilar_id: int) -> Pilar:
    pilar = db.scalar(select(Pilar).where(Pilar.id == pilar_id, Pilar.projeto_id == projeto.id))
    if pilar is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Pilar não encontrado.")
    return pilar


def _exigir_nome_livre(db: Session, projeto: Projeto, nome: str, ignorar_id: Optional[int] = None) -> None:
    """Resposta clara no caso comum; a corrida (dois ao mesmo tempo) fica com a unicidade do banco."""
    repetido = db.scalar(select(Pilar.id).where(
        Pilar.projeto_id == projeto.id, Pilar.nome_chave == chave_do_pilar(nome), Pilar.id != (ignorar_id or 0)
    ))
    if repetido is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOME_REPETIDO)


def _leitura(db: Session, projeto_id: int, usuario: Usuario, pilar_id: Optional[int] = None):
    projeto = obter_projeto_visivel(db, projeto_id, usuario, carregar_tarefas=True)
    pilares = services.calcular_pilares(projeto, services.listar_pilares(db, projeto.id))
    if pilar_id is None:
        return pilares
    return next(p for p in pilares if p.id == pilar_id)


@router.get("", response_model=List[PilarRead])
def listar_pilares(
    projeto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[PilarRead]:
    """Na ordem definida pelo editor, com andamento, atrasos, farol e financeiro de cada pilar."""
    return _leitura(db, projeto_id, usuario)


@router.post("", response_model=PilarRead, status_code=status.HTTP_201_CREATED)
def criar_pilar(
    projeto_id: int,
    payload: PilarCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> PilarRead:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    if len(projeto.pilares) >= MAX_PILARES:
        raise HTTPException(status_code=422, detail=f"Limite de {MAX_PILARES} pilares por projeto.")
    _exigir_nome_livre(db, projeto, payload.nome)

    pilar = Pilar(
        projeto_id=projeto.id, nome=payload.nome, valor_orcado=payload.valor_orcado,
        ordem=max((p.ordem for p in projeto.pilares), default=-1) + 1,
    )
    db.add(pilar)
    try:
        db.flush()  # no Postgres, o INSERT concorrente espera o outro terminar e falha AQUI
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=NOME_REPETIDO)
    auditoria.registrar(db, "pilar.criado", "pilar", pilar.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"nome": pilar.nome})
    salvar(db, conflito=NOME_REPETIDO)  # dois editores criando o mesmo nome ao mesmo tempo
    db.expire_all()
    return _leitura(db, projeto_id, usuario, pilar.id)


@router.patch("/{pilar_id}", response_model=PilarRead)
def atualizar_pilar(
    projeto_id: int,
    pilar_id: int,
    payload: PilarUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> PilarRead:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    pilar = _pilar_do_projeto(db, projeto, pilar_id)
    alteracoes = payload.model_dump(exclude_unset=True)
    conferir_versao(pilar, alteracoes.pop("versao", None))
    if "nome" in alteracoes:
        _exigir_nome_livre(db, projeto, alteracoes["nome"], ignorar_id=pilar.id)

    diff = auditoria.aplicar_alteracoes(pilar, alteracoes)
    if diff:
        if {"farol", "status_texto"} & set(diff):
            # Quem avaliou e quando: um farol verde de três meses atrás não vale muito.
            pilar.status_atualizado_em = agora_utc()
            pilar.status_atualizado_por_id = usuario.id
        auditoria.registrar(db, "pilar.atualizado", "pilar", pilar.id, usuario=usuario, projeto_id=projeto.id,
                            detalhes={"pilar": pilar.nome, **diff})
        salvar(db, conflito=NOME_REPETIDO)
        db.expire_all()
    return _leitura(db, projeto_id, usuario, pilar_id)


@router.delete("/{pilar_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_pilar(
    projeto_id: int,
    pilar_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    """As tarefas do pilar continuam no cronograma, só ficam sem pilar."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    pilar = _pilar_do_projeto(db, projeto, pilar_id)
    # Explícito (e não só o ON DELETE SET NULL): soma 1 na versão, então quem estava com a tarefa
    # aberta não a devolve para o pilar excluído.
    db.execute(
        update(Tarefa).where(Tarefa.pilar_id == pilar.id)
        .values(pilar_id=None, versao=Tarefa.versao + 1)
        .execution_options(synchronize_session=False)
    )
    auditoria.registrar(db, "pilar.excluido", "pilar", pilar.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"nome": pilar.nome})
    db.delete(pilar)
    salvar(db)
