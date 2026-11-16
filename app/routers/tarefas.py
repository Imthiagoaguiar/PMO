"""
Cronograma do projeto (tarefas dentro das fases).

Regras:
- Dono, admin e editores: criam, editam qualquer campo (inclusive o percentual), movem entre fases e excluem.
- Visualizadores: só leem (e comentam, em /comentarios).
- Projeto Concluído/Cancelado: cronograma somente leitura até ser reaberto.
- responsavel_id precisa ser um membro ativo do projeto.
- duracao_dias_uteis: a data_fim é calculada pulando fins de semana e feriados (Brasil API).
"""

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import auditoria, dependencias, tempo
from app.database import get_db
from app.integracoes.feriados import calcular_data_fim, proximo_dia_util
from app.deps import (
    IdPath,
    Paginacao,
    conferir_versao,
    exigir_projeto_aberto,
    filtrar_projetos_visiveis,
    get_usuario_atual,
    obter_projeto_editavel,
    obter_projeto_visivel,
    paginar,
    salvar,
)
from app.models import Fase, Projeto, Tarefa, Usuario
from app.security import ID_MAXIMO
from app.schemas import MinhaTarefa, Pagina, TarefaCreate, TarefaRead, TarefaUpdate

router = APIRouter(prefix="/projetos/{projeto_id}", tags=["Tarefas"])
minhas_router = APIRouter(prefix="/tarefas", tags=["Tarefas"])

def _fase_do_projeto(projeto: Projeto, fase_id: int) -> Fase:
    fase = next((f for f in projeto.fases if f.id == fase_id), None)
    if fase is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fase não encontrada neste projeto.")
    return fase


def _tarefa_do_projeto(db: Session, projeto: Projeto, tarefa_id: int) -> Tarefa:
    # O join com Fase garante que a tarefa pertence a ESTE projeto (evita acessar tarefa
    # de outro projeto trocando só o id na URL).
    tarefa = db.scalar(
        select(Tarefa).join(Fase).where(Tarefa.id == tarefa_id, Fase.projeto_id == projeto.id)
    )
    if tarefa is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tarefa não encontrada.")
    return tarefa


def _validar_responsavel(projeto: Projeto, responsavel_id: Optional[int]) -> None:
    if responsavel_id is None:
        return
    membro = next((m for m in projeto.membros if m.usuario_id == responsavel_id), None)
    if membro is None or not membro.usuario.ativo:
        raise HTTPException(
            status_code=422, detail="O responsável precisa ser um membro ativo do projeto."
        )


def _validar_pilar(projeto: Projeto, pilar_id: Optional[int]) -> None:
    if pilar_id is not None and not any(p.id == pilar_id for p in projeto.pilares):
        # Também impede usar o id de um pilar de OUTRO projeto (IDOR).
        raise HTTPException(status_code=422, detail="O pilar precisa ser deste projeto.")


# ---------- Cronograma do projeto ----------


@router.post(
    "/fases/{fase_id}/tarefas", response_model=TarefaRead, status_code=status.HTTP_201_CREATED
)
def criar_tarefa(
    projeto_id: IdPath,
    fase_id: IdPath,
    payload: TarefaCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Tarefa:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    fase = _fase_do_projeto(projeto, fase_id)
    _validar_responsavel(projeto, payload.responsavel_id)
    _validar_pilar(projeto, payload.pilar_id)

    dados = payload.model_dump(exclude={"duracao_dias_uteis"})
    if payload.duracao_dias_uteis is not None:
        # Como no cronograma encadeado: começar num sábado/feriado é começar no próximo dia útil.
        dados["data_inicio"] = proximo_dia_util(payload.data_inicio)
        dados["data_fim"] = calcular_data_fim(dados["data_inicio"], payload.duracao_dias_uteis)

    tarefa = Tarefa(fase_id=fase.id, **dados)
    db.add(tarefa)
    db.flush()
    auditoria.registrar(
        db, "tarefa.criada", "tarefa", tarefa.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"descricao": tarefa.descricao, "fase": fase.titulo},
    )
    salvar(db)
    return tarefa


@router.get("/tarefas", response_model=Pagina[TarefaRead])
def listar_tarefas(
    projeto_id: IdPath,
    fase_id: Optional[int] = Query(None, ge=1, le=ID_MAXIMO),
    pilar_id: Optional[int] = Query(None, ge=1, le=ID_MAXIMO),
    sem_pilar: bool = Query(False, description="Só as tarefas sem pilar"),
    # Próprio (e não o Paginacao genérico, de até 100): o cronograma carrega de 200 em 200.
    limite: int = Query(200, ge=1, le=500, description="Tarefas por página"),
    offset: int = Query(0, ge=0, le=1_000_000_000),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Cronograma paginado (projetos grandes): o detalhe do projeto traz só as 200 primeiras de cada fase."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    query = select(Tarefa).join(Fase).where(Fase.projeto_id == projeto.id).order_by(Tarefa.id)
    if fase_id is not None:
        query = query.where(Tarefa.fase_id == fase_id)
    if sem_pilar:
        query = query.where(Tarefa.pilar_id.is_(None))
    elif pilar_id is not None:
        query = query.where(Tarefa.pilar_id == pilar_id)
    return paginar(db, query, Paginacao(limite=limite, offset=offset))


@router.patch("/tarefas/{tarefa_id}", response_model=TarefaRead)
def atualizar_tarefa(
    projeto_id: IdPath,
    tarefa_id: IdPath,
    payload: TarefaUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Tarefa:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    tarefa = _tarefa_do_projeto(db, projeto, tarefa_id)
    exigir_projeto_aberto(projeto)
    alteracoes = payload.model_dump(exclude_unset=True)
    conferir_versao(tarefa, alteracoes.pop("versao", None))
    duracao = alteracoes.pop("duracao_dias_uteis", None)
    if duracao is not None:
        inicio = alteracoes.get("data_inicio", tarefa.data_inicio)
        if inicio is None:
            raise HTTPException(status_code=422, detail="duracao_dias_uteis exige data_inicio.")
        alteracoes["data_inicio"] = proximo_dia_util(inicio)
        alteracoes["data_fim"] = calcular_data_fim(alteracoes["data_inicio"], duracao)
    if "fase_id" in alteracoes:
        _fase_do_projeto(projeto, alteracoes["fase_id"])  # só move dentro do mesmo projeto
    if "responsavel_id" in alteracoes:
        _validar_responsavel(projeto, alteracoes["responsavel_id"])
    if "pilar_id" in alteracoes:
        _validar_pilar(projeto, alteracoes["pilar_id"])
    novo_percentual = alteracoes.get("percentual_conclusao")
    if novo_percentual is not None and novo_percentual != tarefa.percentual_conclusao \
            and dependencias.tem_dependencias(db, tarefa.id):
        # Em fila com quem mexe nas dependências do projeto: "A reaberta" e "B avançada" ao mesmo
        # tempo não passam as duas.
        dependencias.travar_projeto(db, projeto)
        db.refresh(tarefa)
        conferir_versao(tarefa, payload.versao)
        try:
            dependencias.conferir_avanco(db, tarefa, novo_percentual)
        except dependencias.RegraDependencia as exc:
            raise HTTPException(status_code=422, detail=str(exc))

    diff = auditoria.aplicar_alteracoes(tarefa, alteracoes)

    # Valida as datas já combinadas (ex.: PATCH só da data_fim contra a data_inicio existente).
    if tarefa.data_inicio and tarefa.data_fim and tarefa.data_fim < tarefa.data_inicio:
        db.rollback()
        raise HTTPException(status_code=422, detail="data_fim não pode ser anterior à data_inicio.")

    if diff:
        if "pilar_id" in diff:
            # No histórico, o NOME do pilar (o id não diz nada a quem lê, e o pilar pode ser excluído depois).
            nomes = {p.id: p.nome for p in projeto.pilares}
            diff["pilar"] = [nomes.get(i) for i in diff.pop("pilar_id")]
        auditoria.registrar(
            db, "tarefa.atualizada", "tarefa", tarefa.id, usuario=usuario, projeto_id=projeto.id, detalhes=diff
        )
        salvar(db)
    return tarefa


@router.delete("/tarefas/{tarefa_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_tarefa(
    projeto_id: IdPath,
    tarefa_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    tarefa = _tarefa_do_projeto(db, projeto, tarefa_id)
    auditoria.registrar(
        db, "tarefa.excluida", "tarefa", tarefa.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"descricao": tarefa.descricao},
    )
    db.delete(tarefa)
    salvar(db)


# ---------- Minhas tarefas ----------


@minhas_router.get("/minhas", response_model=Pagina[MinhaTarefa])
def minhas_tarefas(
    pendentes: bool = Query(True, description="Só as que ainda não chegaram a 100%"),
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Tarefas atribuídas ao usuário logado, em todos os projetos que ele enxerga, por prazo."""
    query = (
        select(Tarefa, Fase, Projeto)
        .join(Fase, Tarefa.fase_id == Fase.id)
        .join(Projeto, Fase.projeto_id == Projeto.id)
        .where(Tarefa.responsavel_id == usuario.id)
    )
    if pendentes:
        query = query.where(Tarefa.percentual_conclusao < 100)
    query = filtrar_projetos_visiveis(query, usuario).order_by(
        Tarefa.data_fim.is_(None), Tarefa.data_fim, Tarefa.id
    )

    resultado = paginar(db, query, pagina, linhas=True)
    hoje = tempo.hoje()
    resultado["itens"] = [
        MinhaTarefa(
            **TarefaRead.model_validate(tarefa).model_dump(),
            projeto_id=projeto.id,
            nome_projeto=projeto.nome_projeto,
            fase_titulo=fase.titulo,
            atrasada=bool(tarefa.data_fim and tarefa.data_fim < hoje and tarefa.percentual_conclusao < 100),
        )
        for tarefa, fase, projeto in resultado["itens"]
    ]
    return resultado
