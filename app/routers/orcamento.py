"""
Orçamento do projeto: itens (o orçado de cada linha) e lançamentos (o realizado, com data).

- GET /orcamento: resumo com totais, saldo, execução, previsão de fechamento, gráficos e alertas.
- Itens: CRUD. Excluir item com lançamentos exige `excluir_lancamentos=true` (nada some sem decidir).
- Lançamentos: CRUD, com filtros (item, categoria, pilar, período) e paginação.
- GET /orcamento/exportar: planilha .xlsx com os itens e os lançamentos.
Visualizadores leem; editores alteram. Projeto encerrado fica somente leitura.
"""

import io
from datetime import date
from typing import Any, Dict, Optional

import openpyxl
from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from openpyxl.cell import WriteOnlyCell
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app import auditoria, orcamento, services
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
from app.models import MAX_ITENS_ORCAMENTO, CategoriaOrcamento, ItemOrcamento, LancamentoOrcamento, Projeto, Usuario
from app.schemas import (
    ItemOrcamentoCreate,
    ItemOrcamentoRead,
    ItemOrcamentoUpdate,
    LancamentoCreate,
    LancamentoRead,
    LancamentoUpdate,
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
    item = ItemOrcamento(projeto_id=projeto.id, ordem=(ordem or 0) + 1, **payload.model_dump())
    db.add(item)
    db.flush()
    auditoria.registrar(db, "orcamento.item_criado", "item_orcamento", item.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"descricao": item.descricao, "valor_orcado": str(item.valor_orcado)})
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
    if "pilar_id" in alteracoes:
        _validar_pilar(projeto, alteracoes["pilar_id"])
    diff = auditoria.aplicar_alteracoes(item, alteracoes)
    if diff:
        auditoria.registrar(db, "orcamento.item_atualizado", "item_orcamento", item.id, usuario=usuario,
                            projeto_id=projeto.id, detalhes={"item": item.descricao, **diff})
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
    limite: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0, le=1_000_000_000),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Os mais recentes primeiro."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    consulta = (
        select(LancamentoOrcamento).join(ItemOrcamento, ItemOrcamento.id == LancamentoOrcamento.item_id)
        .where(LancamentoOrcamento.projeto_id == projeto.id)
        .options(selectinload(LancamentoOrcamento.criado_por))
        .order_by(LancamentoOrcamento.data.desc(), LancamentoOrcamento.id.desc())
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
    lanc = LancamentoOrcamento(projeto_id=projeto.id, criado_por_id=usuario.id, **payload.model_dump())
    db.add(lanc)
    db.flush()
    auditoria.registrar(db, "orcamento.lancamento_criado", "lancamento", lanc.id, usuario=usuario, projeto_id=projeto.id,
                        detalhes={"item": item.descricao, "descricao": lanc.descricao, "valor": str(lanc.valor),
                                  "data": lanc.data.isoformat()})
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
    if "item_id" in alteracoes:
        _item(db, projeto, alteracoes["item_id"])  # só move para item do mesmo projeto
    diff = auditoria.aplicar_alteracoes(lanc, alteracoes)
    if diff:
        auditoria.registrar(db, "orcamento.lancamento_atualizado", "lancamento", lanc.id, usuario=usuario,
                            projeto_id=projeto.id, detalhes={"lancamento": lanc.descricao, **diff})
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
    itens = orcamento.ler_itens(db, projeto)
    nomes = {i.id: i.descricao for i in itens}

    livro = openpyxl.Workbook(write_only=True)
    aba = livro.create_sheet("Itens")
    for letra, largura in zip("ABCDEFG", (40, 10, 18, 16, 16, 16, 12)):
        aba.column_dimensions[letra].width = largura
    aba.append(["Item", "Categoria", "Pilar", "Orçado", "Realizado", "Saldo", "Execução %"])
    for i in itens:
        aba.append([
            services.celula_texto_seguro(aba, i.descricao), orcamento.NOME_CATEGORIA[i.categoria],
            services.celula_texto_seguro(aba, i.pilar_nome), _moeda(aba, i.valor_orcado), _moeda(aba, i.valor_realizado),
            _moeda(aba, i.saldo), i.execucao,
        ])

    aba = livro.create_sheet("Lançamentos")
    for letra, largura in zip("ABCDE", (12, 40, 40, 18, 16)):
        aba.column_dimensions[letra].width = largura
    aba.append(["Data", "Item", "Descrição", "Documento", "Valor"])
    for lanc in db.scalars(
        select(LancamentoOrcamento).where(LancamentoOrcamento.projeto_id == projeto.id)
        .order_by(LancamentoOrcamento.data, LancamentoOrcamento.id)
        .execution_options(yield_per=services.LOTE_EXPORTACAO)
    ):
        aba.append([
            services.celula_data(aba, lanc.data), services.celula_texto_seguro(aba, nomes.get(lanc.item_id)),
            services.celula_texto_seguro(aba, lanc.descricao), services.celula_texto_seguro(aba, lanc.documento),
            _moeda(aba, lanc.valor),
        ])

    buffer = io.BytesIO()
    livro.save(buffer)
    auditoria.registrar(db, "orcamento.exportado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id)
    salvar(db)
    return Response(
        content=buffer.getvalue(), media_type=MIME_XLSX,
        headers={"Content-Disposition": content_disposition(f"{projeto.nome_projeto} - orçamento.xlsx")},
    )


def _moeda(aba: Any, valor: Any) -> Any:
    celula = WriteOnlyCell(aba, value=valor)
    celula.number_format = '"R$" #,##0.00;-"R$" #,##0.00'
    return celula
