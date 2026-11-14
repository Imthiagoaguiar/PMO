"""
Andamento das importações de planilha (POST /projetos/importar responde 202 e devolve o id).
Cada pessoa só enxerga as próprias importações: as de outras pessoas respondem 404.
"""

from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import fila
from app.database import get_db
from app.deps import IdPath, Paginacao, get_usuario_atual, paginar
from app.models import Importacao, StatusImportacao, Usuario
from app.schemas import ImportacaoRead, Pagina

router = APIRouter(prefix="/importacoes", tags=["Importações"])


def ler_importacao(imp: Importacao) -> ImportacaoRead:
    leitura = ImportacaoRead(
        id=imp.id, status=imp.status, progresso=imp.progresso, etapa=imp.etapa, nome_projeto=imp.nome_projeto,
        arquivo_nome=imp.arquivo_nome, arquivo_bytes=imp.arquivo_bytes, total_linhas=imp.total_linhas,
        projeto_id=imp.projeto_id, mensagem=imp.mensagem, erros=imp.erros or [],
        criada_em=imp.criada_em, iniciada_em=imp.iniciada_em, concluida_em=imp.concluida_em,
    )
    if imp.status == StatusImportacao.PROCESSANDO:
        ao_vivo = fila.progresso_ao_vivo(imp.id)  # o banco só sabe que começou; o Redis sabe onde está
        if ao_vivo and ao_vivo.get("progresso") is not None:
            leitura.progresso, leitura.etapa = ao_vivo["progresso"], ao_vivo.get("etapa") or leitura.etapa
    return leitura


@router.get("", response_model=Pagina[ImportacaoRead])
def minhas_importacoes(
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """As suas importações, mais recentes primeiro."""
    query = select(Importacao).where(Importacao.usuario_id == usuario.id).order_by(Importacao.id.desc())
    resultado = paginar(db, query, pagina)
    resultado["itens"] = [ler_importacao(i) for i in resultado["itens"]]
    return resultado


@router.get("/{importacao_id}", response_model=ImportacaoRead)
def acompanhar_importacao(
    importacao_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ImportacaoRead:
    imp = db.get(Importacao, importacao_id)
    if imp is None or imp.usuario_id != usuario.id:  # a de outra pessoa é indistinguível de inexistente
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Importação não encontrada.")
    return ler_importacao(imp)
