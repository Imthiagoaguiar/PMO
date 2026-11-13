"""
Idempotency-Key nas criações: o front gera uma chave (UUID) quando o formulário abre e a envia
em todas as tentativas daquele envio. A mesma chave = a mesma criação.

- 2ª requisição depois da 1ª: devolve o projeto já criado (e o header Idempotent-Replayed: true).
- Requisições SIMULTÂNEAS: a unicidade (usuario_id, chave) no banco deixa só uma gravar; as
  outras pegam o IntegrityError e devolvem o projeto da vencedora.
- Mesma chave com outro conteúdo: 422 (a chave foi reaproveitada por engano).
Sem o header, a rota funciona como antes (clientes antigos, scripts).
"""

import hashlib
import re
from typing import Optional

from fastapi import Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ChaveIdempotencia

_FORMATO = re.compile(r"[A-Za-z0-9_\-:.]{8,100}")


def chave_do_header(
    idempotency_key: Optional[str] = Header(
        None, alias="Idempotency-Key",
        description="UUID gerado pelo cliente por envio; repetir o envio com a mesma chave não duplica o projeto",
    ),
) -> Optional[str]:
    if idempotency_key is None:
        return None
    chave = idempotency_key.strip()
    if not _FORMATO.fullmatch(chave):
        raise HTTPException(
            status_code=422,
            detail="Idempotency-Key inválida: use de 8 a 100 caracteres (letras, números, - _ : .).",
        )
    return chave


def resumo(*partes: bytes) -> str:
    """Impressão digital do corpo, para reconhecer a mesma chave usada com outro conteúdo."""
    h = hashlib.sha256()
    for parte in partes:
        h.update(len(parte).to_bytes(8, "big"))
        h.update(parte)
    return h.hexdigest()


def projeto_ja_criado(db: Session, usuario_id: int, chave: Optional[str], rota: str, hash_corpo: str) -> Optional[int]:
    if chave is None:
        return None
    registro = db.scalar(
        select(ChaveIdempotencia).where(ChaveIdempotencia.usuario_id == usuario_id, ChaveIdempotencia.chave == chave)
    )
    if registro is None:
        return None
    if registro.rota != rota or registro.hash_corpo != hash_corpo:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Esta Idempotency-Key já foi usada em outra requisição. Gere uma chave nova para cada criação.",
        )
    return registro.projeto_id


def registrar(db: Session, usuario_id: int, chave: str, rota: str, hash_corpo: str, projeto_id: int) -> None:
    db.add(ChaveIdempotencia(usuario_id=usuario_id, chave=chave, rota=rota, hash_corpo=hash_corpo, projeto_id=projeto_id))
