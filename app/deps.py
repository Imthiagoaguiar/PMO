"""Dependências e helpers compartilhados pelos routers: autenticação, autorização, paginação."""

import logging
from typing import Any, Callable, Dict

import jwt
from fastapi import Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import Select, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app import services
from app.database import get_db
from app.integracoes import clerk
from app.models import (
    Fase,
    PapelProjeto,
    PapelUsuario,
    Projeto,
    ProjetoMembro,
    StatusProjeto,
    Tarefa,
    Usuario,
)
from app.security import decodificar_access_token

logger = logging.getLogger(__name__)

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

STATUS_ENCERRADOS = {StatusProjeto.CONCLUIDO, StatusProjeto.CANCELADO}


# ---------- Persistência ----------


def salvar(db: Session) -> None:
    """Commit com rollback e erro genérico (o detalhe vai só para o log)."""
    try:
        db.commit()
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Falha ao gravar no banco")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao salvar. Tente novamente.",
        )


# ---------- Autenticação ----------


def get_usuario_sessao(
    token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)
) -> Usuario:
    """Usuário do token, SEM exigir a troca de senha pendente (só para /auth/me e /auth/alterar-senha)."""
    nao_autenticado = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Não autenticado.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    # Aceita dois emissores: o login próprio (HS256, /auth/login) e o Clerk (RS256), cada um
    # com o seu validador e algoritmo fixo. O token do Clerk só é aceito se CLERK_EMISSOR existir.
    if clerk.emitido_pelo_clerk(token):
        try:
            identidade = clerk.validar_token(token)
        except clerk.TokenClerkInvalido as exc:
            logger.info("Token do Clerk rejeitado: %s", exc)
            raise nao_autenticado
        try:
            usuario = services.usuario_do_clerk(db, identidade)
        except services.AcessoNegado as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    else:
        try:
            acesso = decodificar_access_token(token)
        except jwt.InvalidTokenError:
            raise nao_autenticado
        usuario = db.get(Usuario, acesso.usuario_id)
        # Troca de senha / "encerrar sessões": tokens emitidos antes disso deixam de valer na hora,
        # sem esperar os 15 minutos de validade.
        if usuario is not None and acesso.versao_sessao != usuario.versao_sessao:
            raise nao_autenticado

    # Busca no banco a cada requisição: desativar um usuário corta o acesso na hora,
    # e o papel vem sempre do banco (nunca confiamos no que está dentro do token).
    if usuario is None or not usuario.ativo:
        raise nao_autenticado
    return usuario


def get_usuario_atual(usuario: Usuario = Depends(get_usuario_sessao)) -> Usuario:
    if usuario.trocar_senha:
        # Senha definida pelo admin: nada funciona até a pessoa escolher a própria senha.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Troque sua senha para continuar.",
            headers={"X-Troca-De-Senha": "obrigatoria"},
        )
    return usuario


def exigir_papel(*papeis: PapelUsuario) -> Callable[..., Usuario]:
    def _dependencia(usuario: Usuario = Depends(get_usuario_atual)) -> Usuario:
        if usuario.papel not in papeis:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Você não tem permissão para esta ação.",
            )
        return usuario

    return _dependencia


# ---------- Regras de acesso a projetos ----------


def filtrar_projetos_visiveis(query: Select, usuario: Usuario) -> Select:
    """Admin vê tudo; os demais só veem projetos dos quais são membros."""
    if usuario.papel == PapelUsuario.ADMIN:
        return query
    return query.where(Projeto.membros.any(ProjetoMembro.usuario_id == usuario.id))


def obter_projeto_visivel(
    db: Session, projeto_id: int, usuario: Usuario, carregar_tarefas: bool = False
) -> Projeto:
    query = select(Projeto).where(Projeto.id == projeto_id)
    if carregar_tarefas:
        query = query.options(
            selectinload(Projeto.fases).selectinload(Fase.tarefas).selectinload(Tarefa.responsavel_usuario)
        )
    projeto = db.scalar(filtrar_projetos_visiveis(query, usuario))
    if projeto is None:
        # 404 (e não 403) para não revelar que o projeto existe.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projeto não encontrado.")
    return projeto


# Quem pode o quê dentro de um projeto:
#   visualizador -> ver, exportar, comentar
#   editor       -> + alterar dados do projeto (inclusive status) e o cronograma
#   dono / admin -> + gerenciar membros e excluir o projeto


def pode_gerenciar(projeto: Projeto, usuario: Usuario) -> bool:
    """Dono do projeto ou admin."""
    return usuario.papel == PapelUsuario.ADMIN or projeto.dono_id == usuario.id


def pode_editar(projeto: Projeto, usuario: Usuario) -> bool:
    if pode_gerenciar(projeto, usuario):
        return True
    return any(m.usuario_id == usuario.id and m.papel == PapelProjeto.EDITOR for m in projeto.membros)


def obter_projeto_editavel(
    db: Session, projeto_id: int, usuario: Usuario, carregar_tarefas: bool = False
) -> Projeto:
    projeto = obter_projeto_visivel(db, projeto_id, usuario, carregar_tarefas)
    if not pode_editar(projeto, usuario):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Você tem acesso apenas de visualização a este projeto.",
        )
    return projeto


def obter_projeto_gerenciavel(db: Session, projeto_id: int, usuario: Usuario) -> Projeto:
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    if not pode_gerenciar(projeto, usuario):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Apenas o dono do projeto ou um administrador pode fazer isso.",
        )
    return projeto


def exigir_projeto_aberto(projeto: Projeto) -> None:
    """Projeto Concluído/Cancelado fica somente leitura até ser reaberto (mudando o status)."""
    if projeto.status in STATUS_ENCERRADOS:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Projeto {projeto.status.value.lower()}: reabra o projeto (altere o status) para editá-lo.",
        )


# ---------- Consulta ----------


def escapar_like(texto: str) -> str:
    """Impede que % e _ digitados pelo usuário virem curingas do LIKE."""
    return texto.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------- Paginação ----------


class Paginacao:
    def __init__(
        self,
        limite: int = Query(20, ge=1, le=100, description="Itens por página"),
        offset: int = Query(0, ge=0, description="Quantos itens pular"),
    ) -> None:
        self.limite = limite
        self.offset = offset


def paginar(db: Session, query: Select, pagina: Paginacao, linhas: bool = False) -> Dict[str, Any]:
    """
    Retorna {"itens", "total", "limite", "offset"}.
    `linhas=True` para consultas com várias entidades por linha (ex.: select(Tarefa, Fase)).
    """
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    paginada = query.limit(pagina.limite).offset(pagina.offset)
    itens = db.execute(paginada).all() if linhas else list(db.scalars(paginada))
    return {"itens": itens, "total": total or 0, "limite": pagina.limite, "offset": pagina.offset}
