"""
Orçamento do projeto: itens (o orçado de cada linha) e lançamentos (o realizado, com data).

- GET /orcamento: resumo com totais, saldo, execução, previsão de fechamento, gráficos e alertas.
- Itens: CRUD. Excluir item com lançamentos exige `excluir_lancamentos=true` (nada some sem decidir).
- Itens podem ter o orçado MÊS A MÊS (`meses`); editar o item também aceita o novo valor GASTO
  (`valor_realizado`): o sistema lança um ajuste com a diferença, que fica no histórico.
- Lançamentos: CRUD, com filtros (item, categoria, pilar, período, projetado) e paginação. Projetados são gastos
  futuros previstos; `POST /lancamentos/{id}/confirmar` registra que aconteceram (viram realizados).
- Gasto que deixa o item acima do orçado exige `justificativa` (por que está estourando).
- GET /orcamento/exportar: planilha .xlsx com resumo (CAPEX/OPEX), itens, plano mensal e lançamentos.
Visualizadores leem; editores alteram. Projeto encerrado fica somente leitura.
"""

import io
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.attributes import flag_modified

from app import auditoria, orcamento, tempo
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
from app.models import (
    MAX_ITENS_ORCAMENTO,
    CategoriaOrcamento,
    ItemOrcamento,
    LancamentoOrcamento,
    OrcamentoMensal,
    Projeto,
    Usuario,
)
from app.schemas import (
    ConfirmarProjetado,
    ItemOrcamentoCreate,
    ItemOrcamentoRead,
    ItemOrcamentoUpdate,
    LancamentoCreate,
    LancamentoRead,
    LancamentoUpdate,
    MesOrcamento,
    Pagina,
    ResumoOrcamento,
)
from app.routers.projetos import content_disposition  # mesmo nome de arquivo seguro do cronograma
from app.security import ID_MAXIMO

router = APIRouter(prefix="/projetos/{projeto_id}/orcamento", tags=["Orçamento"])
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _item(db: Session, projeto: Projeto, item_id: int) -> ItemOrcamento:
    item = db.scalar(select(ItemOrcamento).where(ItemOrcamento.id == item_id, ItemOrcamento.projeto_id == projeto.id))
    if item is None:  # também barra o item de OUTRO projeto (IDOR)
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Item do orçamento não encontrado.")
    return item


def _lancamento(db: Session, projeto: Projeto, lancamento_id: int) -> LancamentoOrcamento:
    lanc = db.scalar(select(LancamentoOrcamento).where(
        LancamentoOrcamento.id == lancamento_id, LancamentoOrcamento.projeto_id == projeto.id))
    if lanc is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lançamento não encontrado.")
    return lanc


def _validar_pilar(projeto: Projeto, pilar_id: Optional[int]) -> None:
    if pilar_id is not None and not any(p.id == pilar_id for p in projeto.pilares):
        raise HTTPException(status_code=422, detail="O pilar precisa ser deste projeto.")


def _ler_item(db: Session, projeto: Projeto, item_id: int) -> ItemOrcamentoRead:
    return orcamento.ler_itens(db, projeto, [item_id])[0]


def _exigir_justificativa(item: ItemOrcamento, gasto_antes: Decimal, gasto_depois: Decimal,
                          justificativa: Optional[str]) -> None:
    """Gasto que AUMENTA e deixa o item acima do orçado precisa dizer por quê."""
    if gasto_depois > gasto_antes and gasto_depois > item.valor_orcado and not justificativa:
        raise HTTPException(status_code=422, detail=(
            f"Este gasto deixa “{item.descricao}” acima do orçado ({orcamento.reais(gasto_depois)} de "
            f"{orcamento.reais(Decimal(item.valor_orcado))}). Explique por que está estourando."))


def _definir_meses(item: ItemOrcamento, meses: List[MesOrcamento]) -> None:
    """
    Troca a distribuição mensal do item; com meses, o orçado passa a ser a soma deles.
    Atualiza no lugar o mês que continua (apagar e recriar o mesmo mês no mesmo flush violaria a unicidade
    item + mês: o SQLAlchemy insere antes de apagar).
    """
    existentes = {m.mes: m for m in item.meses}
    novos = []
    for m in sorted(meses, key=lambda m: m.mes):
        dia = date(int(m.mes[:4]), int(m.mes[5:]), 1)
        linha = existentes.get(dia) or OrcamentoMensal(mes=dia)
        linha.valor = m.valor
        novos.append(linha)
    item.meses = novos
    if meses:
        item.valor_orcado = sum((m.valor for m in meses), Decimal("0"))


def _resumo_meses(item: ItemOrcamento) -> List[str]:
    return [f"{orcamento.mes_iso(m.mes)}: {orcamento.reais(Decimal(m.valor))}" for m in item.meses]


# ---------- Resumo ----------


@router.get("", response_model=ResumoOrcamento)
def resumo_orcamento(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ResumoOrcamento:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    return orcamento.resumo(db, projeto)


# ---------- Itens ----------


@router.post("/itens", response_model=ItemOrcamentoRead, status_code=status.HTTP_201_CREATED)
def criar_item(
    projeto_id: IdPath,
    payload: ItemOrcamentoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ItemOrcamentoRead:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    _validar_pilar(projeto, payload.pilar_id)
    total = db.scalar(select(func.count()).select_from(ItemOrcamento).where(ItemOrcamento.projeto_id == projeto.id)) or 0
    if total >= MAX_ITENS_ORCAMENTO:
        raise HTTPException(status_code=422, detail=f"Limite de {MAX_ITENS_ORCAMENTO} itens de orçamento por projeto.")
    ordem = db.scalar(select(func.max(ItemOrcamento.ordem)).where(ItemOrcamento.projeto_id == projeto.id))
    item = ItemOrcamento(projeto_id=projeto.id, ordem=(ordem or 0) + 1, **payload.model_dump(exclude={"meses"}))
    _definir_meses(item, payload.meses or [])
    db.add(item)
    db.flush()
    detalhes: Dict[str, Any] = {"descricao": item.descricao, "valor_orcado": str(item.valor_orcado)}
    if item.meses:
        detalhes["meses"] = _resumo_meses(item)
    auditoria.registrar(db, "orcamento.item_criado", "item_orcamento", item.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes=detalhes)
    salvar(db)
    return _ler_item(db, projeto, item.id)


@router.patch("/itens/{item_id}", response_model=ItemOrcamentoRead)
def atualizar_item(
    projeto_id: IdPath,
    item_id: IdPath,
    payload: ItemOrcamentoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ItemOrcamentoRead:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    item = _item(db, projeto, item_id)
    alteracoes = payload.model_dump(exclude_unset=True)
    conferir_versao(item, alteracoes.pop("versao", None))
    meses = payload.meses if "meses" in alteracoes else None
    alteracoes.pop("meses", None)
    novo_gasto = alteracoes.pop("valor_realizado", None)
    justificativa = alteracoes.pop("justificativa", None)
    if "pilar_id" in alteracoes:
        _validar_pilar(projeto, alteracoes["pilar_id"])
    if item.meses and meses is None:
        alteracoes.pop("valor_orcado", None)  # com meses, o orçado é a soma deles
    diff = auditoria.aplicar_alteracoes(item, alteracoes)
    if meses is not None:
        antes = _resumo_meses(item)
        orcado_antes = Decimal(item.valor_orcado)
        _definir_meses(item, meses)
        if _resumo_meses(item) != antes:
            diff["meses"] = [antes, _resumo_meses(item)]
            if Decimal(item.valor_orcado) != orcado_antes:
                diff["valor_orcado"] = [str(orcado_antes), str(item.valor_orcado)]
            flag_modified(item, "descricao")  # a linha do item muda de versão (bloqueio otimista)
    if diff:
        auditoria.registrar(db, "orcamento.item_atualizado", "item_orcamento", item.id, usuario=usuario,
                            projeto_id=projeto.id, detalhes={"item": item.descricao, **diff})

    ajustou = False
    if novo_gasto is not None:
        atual = orcamento.realizado_do_item(db, item.id)
        diferenca = novo_gasto - atual
        if diferenca:
            _exigir_justificativa(item, atual, novo_gasto, justificativa)
            ajuste = LancamentoOrcamento(
                projeto_id=projeto.id, item_id=item.id, data=tempo.hoje(), criado_por_id=usuario.id,
                descricao=f"Ajuste do valor gasto: de {orcamento.reais(atual)} para {orcamento.reais(novo_gasto)}",
                valor=diferenca, justificativa=justificativa,
            )
            db.add(ajuste)
            db.flush()
            auditoria.registrar(db, "orcamento.gasto_ajustado", "item_orcamento", item.id, usuario=usuario,
                                projeto_id=projeto.id, detalhes={
                                    "item": item.descricao, "gasto": [str(atual), str(novo_gasto)],
                                    **({"justificativa": justificativa} if justificativa else {})})
            ajustou = True
    if diff or ajustou:
        salvar(db)
    return _ler_item(db, projeto, item.id)


@router.delete("/itens/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_item(
    projeto_id: IdPath,
    item_id: IdPath,
    excluir_lancamentos: bool = Query(False, description="true: exclui o item JUNTO com os lançamentos dele"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    item = _item(db, projeto, item_id)
    quantidade = db.scalar(select(func.count()).select_from(LancamentoOrcamento)
                           .where(LancamentoOrcamento.item_id == item.id)) or 0
    if quantidade and not excluir_lancamentos:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"“{item.descricao}” tem {quantidade} lançamento(s). Confirme a exclusão junto com eles.",
        )
    auditoria.registrar(db, "orcamento.item_excluido", "item_orcamento", item.id, usuario=usuario,
                        projeto_id=projeto.id, detalhes={"descricao": item.descricao, "lancamentos": quantidade})
    db.delete(item)
    salvar(db)


# ---------- Lançamentos ----------


@router.get("/lancamentos", response_model=Pagina[LancamentoRead])
def listar_lancamentos(
    projeto_id: IdPath,
    item_id: Optional[int] = Query(None, ge=1, le=ID_MAXIMO),
    categoria: Optional[CategoriaOrcamento] = None,
    pilar_id: Optional[int] = Query(None, ge=1, le=ID_MAXIMO),
    de: Optional[date] = Query(None, description="A partir de (AAAA-MM-DD)"),
    ate: Optional[date] = Query(None, description="Até (AAAA-MM-DD)"),
    projetado: bool = Query(False, description="true: os gastos futuros previstos; false: os realizados"),
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000_000),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Realizados: os mais recentes primeiro. Projetados: os mais próximos primeiro."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    ordem = ((LancamentoOrcamento.data, LancamentoOrcamento.id) if projetado
             else (LancamentoOrcamento.data.desc(), LancamentoOrcamento.id.desc()))
    consulta = (
        select(LancamentoOrcamento).join(ItemOrcamento, ItemOrcamento.id == LancamentoOrcamento.item_id)
        .where(LancamentoOrcamento.projeto_id == projeto.id, LancamentoOrcamento.projetado.is_(projetado))
        .options(selectinload(LancamentoOrcamento.criado_por))
        .order_by(*ordem)
    )
    if item_id is not None:
        consulta = consulta.where(LancamentoOrcamento.item_id == item_id)
    if categoria is not None:
        consulta = consulta.where(ItemOrcamento.categoria == categoria)
    if pilar_id is not None:
        consulta = consulta.where(ItemOrcamento.pilar_id == pilar_id)
    if de is not None:
        consulta = consulta.where(LancamentoOrcamento.data >= de)
    if ate is not None:
        consulta = consulta.where(LancamentoOrcamento.data <= ate)
    total = db.scalar(select(func.count()).select_from(consulta.order_by(None).subquery())) or 0
    itens = list(db.scalars(consulta.limit(limite).offset(offset)))
    return {"itens": itens, "total": total, "limite": limite, "offset": offset}


@router.post("/lancamentos", response_model=LancamentoRead, status_code=status.HTTP_201_CREATED)
def criar_lancamento(
    projeto_id: IdPath,
    payload: LancamentoCreate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> LancamentoOrcamento:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    item = _item(db, projeto, payload.item_id)
    if not payload.projetado:
        atual = orcamento.realizado_do_item(db, item.id)
        _exigir_justificativa(item, atual, atual + payload.valor, payload.justificativa)
    lanc = LancamentoOrcamento(projeto_id=projeto.id, criado_por_id=usuario.id, **payload.model_dump())
    db.add(lanc)
    db.flush()
    detalhes = {"item": item.descricao, "descricao": lanc.descricao, "valor": str(lanc.valor), "data": lanc.data.isoformat()}
    if lanc.projetado:
        detalhes["projetado"] = True
    if lanc.justificativa:
        detalhes["justificativa"] = lanc.justificativa
    auditoria.registrar(db, "orcamento.lancamento_criado", "lancamento", lanc.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes=detalhes)
    salvar(db)
    return lanc


@router.patch("/lancamentos/{lancamento_id}", response_model=LancamentoRead)
def atualizar_lancamento(
    projeto_id: IdPath,
    lancamento_id: IdPath,
    payload: LancamentoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> LancamentoOrcamento:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    lanc = _lancamento(db, projeto, lancamento_id)
    alteracoes = payload.model_dump(exclude_unset=True)
    destino = _item(db, projeto, alteracoes.get("item_id", lanc.item_id))  # só move para item do mesmo projeto
    if not lanc.projetado:
        atual = orcamento.realizado_do_item(db, destino.id)
        antes = atual - (lanc.valor if lanc.item_id == destino.id else 0)
        _exigir_justificativa(destino, atual, antes + alteracoes.get("valor", lanc.valor),
                              alteracoes.get("justificativa", lanc.justificativa))
    diff = auditoria.aplicar_alteracoes(lanc, alteracoes)
    if diff:
        auditoria.registrar(db, "orcamento.lancamento_atualizado", "lancamento", lanc.id, usuario=usuario,
                            projeto_id=projeto.id, detalhes={"lancamento": lanc.descricao, **diff})
        salvar(db)
    return lanc


@router.post("/lancamentos/{lancamento_id}/confirmar", response_model=LancamentoRead)
def confirmar_projetado(
    projeto_id: IdPath,
    lancamento_id: IdPath,
    payload: ConfirmarProjetado,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> LancamentoOrcamento:
    """O gasto projetado aconteceu: vira realizado, na data (padrão: hoje) e no valor (padrão: o projetado) reais."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    lanc = _lancamento(db, projeto, lancamento_id)
    if not lanc.projetado:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Este lançamento já é um gasto realizado.")
    item = _item(db, projeto, lanc.item_id)
    valor = payload.valor if payload.valor is not None else lanc.valor
    justificativa = payload.justificativa or lanc.justificativa
    atual = orcamento.realizado_do_item(db, item.id)
    _exigir_justificativa(item, atual, atual + valor, justificativa)
    projetado = {"data": lanc.data.isoformat(), "valor": str(lanc.valor)}
    lanc.projetado, lanc.data, lanc.valor, lanc.justificativa = False, payload.data or tempo.hoje(), valor, justificativa
    auditoria.registrar(db, "orcamento.projetado_confirmado", "lancamento", lanc.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"item": item.descricao, "descricao": lanc.descricao, "projetado": projetado,
                                  "realizado": {"data": lanc.data.isoformat(), "valor": str(lanc.valor)}})
    salvar(db)
    return lanc


@router.delete("/lancamentos/{lancamento_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_lancamento(
    projeto_id: IdPath,
    lancamento_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    exigir_projeto_aberto(projeto)
    lanc = _lancamento(db, projeto, lancamento_id)
    auditoria.registrar(db, "orcamento.lancamento_excluido", "lancamento", lanc.id, usuario=usuario,
                        projeto_id=projeto.id, detalhes={"descricao": lanc.descricao, "valor": str(lanc.valor)})
    db.delete(lanc)
    salvar(db)


# ---------- Exportação ----------


@router.get(
    "/exportar",
    response_class=Response,
    responses={200: {"content": {MIME_XLSX: {}}, "description": "Planilha .xlsx com itens e lançamentos"}},
)
def exportar_orcamento(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Response:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    livro = openpyxl.Workbook(write_only=True)
    orcamento.escrever_planilha(livro, db, projeto)
    buffer = io.BytesIO()
    livro.save(buffer)
    auditoria.registrar(db, "orcamento.exportado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id)
    salvar(db)
    return Response(
        content=buffer.getvalue(), media_type=MIME_XLSX,
        headers={"Content-Disposition": content_disposition(f"{projeto.nome_projeto} - orçamento.xlsx")},
    )
