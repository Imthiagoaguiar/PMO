"""Registro de auditoria: quem fez o quê, quando e de onde."""

import enum
from contextvars import ContextVar
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Dict, Optional

from sqlalchemy.orm import Session

from app.models import Auditoria, Usuario

# IP da requisição atual, preenchido por um middleware em app/main.py.
# Assim os routers não precisam repassar o Request só para auditar.
ip_requisicao: ContextVar[Optional[str]] = ContextVar("ip_requisicao", default=None)


def valor_json(valor: Any) -> Any:
    """Converte tipos do banco para algo que cabe numa coluna JSON."""
    if isinstance(valor, enum.Enum):
        return valor.value
    if isinstance(valor, (date, datetime)):
        return valor.isoformat()
    if isinstance(valor, Decimal):
        return str(valor)
    return valor


def registrar(
    db: Session,
    acao: str,
    entidade: str,
    entidade_id: Optional[int] = None,
    *,
    usuario: Optional[Usuario] = None,
    projeto_id: Optional[int] = None,
    detalhes: Optional[Dict[str, Any]] = None,
) -> None:
    """
    Adiciona o registro à sessão. Não faz commit: o registro é gravado junto com a
    operação auditada (se a operação falhar, a auditoria também não fica gravada).
    """
    db.add(
        Auditoria(
            usuario_id=usuario.id if usuario else None,
            acao=acao,
            entidade=entidade,
            entidade_id=entidade_id,
            projeto_id=projeto_id,
            detalhes={k: valor_json(v) for k, v in (detalhes or {}).items()} or None,
            ip=ip_requisicao.get(),
        )
    )


def aplicar_alteracoes(objeto: Any, alteracoes: Dict[str, Any]) -> Dict[str, Any]:
    """Aplica o PATCH no objeto e devolve só o que mudou: {campo: [antes, depois]}."""
    diff: Dict[str, Any] = {}
    for campo, novo in alteracoes.items():
        antigo = getattr(objeto, campo)
        if antigo != novo:
            diff[campo] = [valor_json(antigo), valor_json(novo)]
            setattr(objeto, campo, novo)
    return diff
