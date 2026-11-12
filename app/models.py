import enum
from datetime import date, datetime
from decimal import Decimal
from typing import List, Optional, Type

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base

# Fases padrão do Waterfall, na ordem em que são criadas (ordem_sequencia = posição + 1).
FASES_WATERFALL = ("Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento")


def _enum(classe: Type[enum.Enum], tamanho: int = 20) -> Enum:
    # native_enum=False: grava como VARCHAR + CHECK, sem criar um tipo ENUM no Postgres
    # (evita dor de cabeça em migrações ao adicionar novos valores).
    return Enum(
        classe,
        native_enum=False,
        length=tamanho,
        values_callable=lambda e: [m.value for m in e],
    )


class StatusProjeto(str, enum.Enum):
    PLANEJADO = "Planejado"
    EM_ANDAMENTO = "Em andamento"
    CONCLUIDO = "Concluído"
    CANCELADO = "Cancelado"


class PapelUsuario(str, enum.Enum):
    ADMIN = "admin"        # gerencia usuários e enxerga todos os projetos
    GERENTE = "gerente"    # cria projetos (vira dono) e gerencia os membros deles
    MEMBRO = "membro"      # enxerga apenas os projetos dos quais é membro


class ProvedorAuth(str, enum.Enum):
    LOCAL = "local"        # e-mail + senha
    ENTRA_ID = "entra_id"  # Microsoft Entra ID (Azure AD) — futuro
    GOOGLE = "google"      # Google Workspace — futuro
    CLERK = "clerk"        # Clerk (claim "sub" = id do usuário no Clerk)


# ---------- Usuários e sessões ----------


class Usuario(Base):
    __tablename__ = "usuarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True, nullable=False)
    nome: Mapped[str] = mapped_column(String(150), nullable=False)
    # Nulo para usuários de SSO, que não têm senha no nosso banco.
    senha_hash: Mapped[Optional[str]] = mapped_column(String(255))
    papel: Mapped[PapelUsuario] = mapped_column(
        _enum(PapelUsuario), default=PapelUsuario.MEMBRO, nullable=False
    )
    ativo: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Preparação para SSO: identificador do usuário no provedor externo (claim "sub"/"oid").
    provedor_auth: Mapped[ProvedorAuth] = mapped_column(
        _enum(ProvedorAuth), default=ProvedorAuth.LOCAL, nullable=False
    )
    provedor_sub: Mapped[Optional[str]] = mapped_column(String(255))

    # Proteção contra força bruta.
    tentativas_falhas: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    bloqueado_ate: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("provedor_auth", "provedor_sub", name="uq_usuario_provedor_sub"),
        CheckConstraint(
            "provedor_auth <> 'local' OR senha_hash IS NOT NULL",
            name="ck_usuario_local_tem_senha",
        ),
    )


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revogado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------- Projetos ----------


class Projeto(Base):
    __tablename__ = "projetos"

    id: Mapped[int] = mapped_column(primary_key=True)
    nome_projeto: Mapped[str] = mapped_column(String(200), nullable=False)
    orcamento_aprovado: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    data_inicio: Mapped[Optional[date]] = mapped_column(Date)
    status: Mapped[StatusProjeto] = mapped_column(
        _enum(StatusProjeto), default=StatusProjeto.PLANEJADO, nullable=False
    )
    dono_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    dono: Mapped["Usuario"] = relationship()
    fases: Mapped[List["Fase"]] = relationship(
        back_populates="projeto",
        cascade="all, delete-orphan",
        order_by="Fase.ordem_sequencia",
    )
    membros: Mapped[List["ProjetoMembro"]] = relationship(
        back_populates="projeto", cascade="all, delete-orphan"
    )

    __table_args__ = (
        CheckConstraint("orcamento_aprovado >= 0", name="ck_projeto_orcamento_positivo"),
    )


class ProjetoMembro(Base):
    """Quem pode enxergar o projeto. O dono também é registrado aqui."""

    __tablename__ = "projeto_membros"

    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("projetos.id", ondelete="CASCADE"), primary_key=True
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    adicionado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    projeto: Mapped["Projeto"] = relationship(back_populates="membros")
    usuario: Mapped["Usuario"] = relationship()


class Fase(Base):
    __tablename__ = "fases"

    id: Mapped[int] = mapped_column(primary_key=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("projetos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    titulo: Mapped[str] = mapped_column(String(50), nullable=False)
    ordem_sequencia: Mapped[int] = mapped_column(Integer, nullable=False)

    projeto: Mapped["Projeto"] = relationship(back_populates="fases")
    tarefas: Mapped[List["Tarefa"]] = relationship(
        back_populates="fase",
        cascade="all, delete-orphan",
        order_by="Tarefa.id",
    )

    __table_args__ = (
        # Um projeto não pode ter duas fases na mesma posição.
        UniqueConstraint("projeto_id", "ordem_sequencia", name="uq_fase_projeto_ordem"),
    )


class Tarefa(Base):
    __tablename__ = "tarefas"

    id: Mapped[int] = mapped_column(primary_key=True)
    fase_id: Mapped[int] = mapped_column(
        ForeignKey("fases.id", ondelete="CASCADE"), nullable=False, index=True
    )
    descricao: Mapped[str] = mapped_column(String(500), nullable=False)
    data_inicio: Mapped[Optional[date]] = mapped_column(Date)
    data_fim: Mapped[Optional[date]] = mapped_column(Date)
    # Nome livre (vem da planilha ou é alguém sem conta no sistema).
    responsavel: Mapped[Optional[str]] = mapped_column(String(150))
    # Usuário do sistema responsável (precisa ser membro do projeto).
    responsavel_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), index=True
    )
    percentual_conclusao: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    fase: Mapped["Fase"] = relationship(back_populates="tarefas")
    responsavel_usuario: Mapped[Optional["Usuario"]] = relationship()

    __table_args__ = (
        CheckConstraint(
            "percentual_conclusao BETWEEN 0 AND 100", name="ck_tarefa_percentual"
        ),
        CheckConstraint("data_fim >= data_inicio", name="ck_tarefa_datas"),
    )


# ---------- Auditoria ----------


class Auditoria(Base):
    """
    Registro imutável de quem fez o quê. Sem FK para projeto/entidade de propósito:
    o histórico continua existindo mesmo depois que o projeto é excluído.
    """

    __tablename__ = "auditoria"

    id: Mapped[int] = mapped_column(primary_key=True)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    usuario_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("usuarios.id", ondelete="SET NULL"), index=True
    )
    acao: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    entidade: Mapped[str] = mapped_column(String(30), nullable=False)
    entidade_id: Mapped[Optional[int]] = mapped_column(Integer)
    projeto_id: Mapped[Optional[int]] = mapped_column(Integer, index=True)
    detalhes: Mapped[Optional[dict]] = mapped_column(JSON)
    ip: Mapped[Optional[str]] = mapped_column(String(45))  # cabe IPv6

    usuario: Mapped[Optional["Usuario"]] = relationship()
