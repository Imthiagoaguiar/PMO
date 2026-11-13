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
    Text,
    UniqueConstraint,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship, validates

from app.database import Base
from app.texto import normalizar

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


class FarolPilar(str, enum.Enum):
    VERDE = "verde"        # no prazo / sem riscos
    AMARELO = "amarelo"    # atenção
    VERMELHO = "vermelho"  # problema: precisa de ação


# Frentes de trabalho com que todo projeto nasce (o editor renomeia, remove ou cria outras).
PILARES_PADRAO = ("TI", "Suprimentos", "CAPEX", "Pessoas")
MAX_PILARES = 20


def chave_do_pilar(nome: str) -> str:
    """Nome comparável: sem acento, sem caixa, sem espaços ("Jurídico " == "juridico")."""
    return "".join(normalizar(nome).split())[:60]


class PapelUsuario(str, enum.Enum):
    ADMIN = "admin"        # gerencia usuários e enxerga todos os projetos
    GERENTE = "gerente"    # cria projetos (vira dono) e gerencia os membros deles
    MEMBRO = "membro"      # enxerga apenas os projetos dos quais é membro


class PapelProjeto(str, enum.Enum):
    EDITOR = "editor"              # edita os dados do projeto e o cronograma
    VISUALIZADOR = "visualizador"  # vê, exporta e comenta; não altera nada


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
    # Senha definida pelo admin (cadastro ou redefinição): a pessoa precisa trocar antes de usar o sistema.
    trocar_senha: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    # Vai no token de acesso (claim "ver"). Trocar a senha ou "encerrar sessões" incrementa o valor
    # e todos os tokens já emitidos deixam de valer na hora (comparação exata, sem depender do relógio).
    versao_sessao: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)

    # Verificação em duas etapas (TOTP). O segredo fica cifrado (app/cripto.py): um vazamento do
    # banco não permite gerar códigos. Enquanto totp_ativo é False, o segredo é só uma tentativa
    # de configuração em andamento.
    totp_ativo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false(), nullable=False)
    totp_segredo_cifrado: Mapped[Optional[str]] = mapped_column(Text)
    # Último passo de 30 s aceito: o mesmo código não vale duas vezes (nem dentro da validade).
    totp_ultimo_passo: Mapped[Optional[int]] = mapped_column(Integer)

    codigos_recuperacao: Mapped[List["CodigoRecuperacao"]] = relationship(
        back_populates="usuario", cascade="all, delete-orphan"
    )

    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    @property
    def dois_fatores_ativo(self) -> bool:
        return self.totp_ativo

    @property
    def exige_configurar_2fa(self) -> bool:
        """Admin com login local sem 2FA: só consegue configurar o 2FA até ativá-lo."""
        from app.config import settings

        return (
            settings.exigir_2fa_admin
            and self.papel == PapelUsuario.ADMIN
            and self.provedor_auth == ProvedorAuth.LOCAL  # Clerk: o 2FA é configurado no Clerk
            and not self.totp_ativo
        )

    @property
    def aguardando_primeiro_login(self) -> bool:
        """Conta externa cadastrada pelo admin que ainda não foi ligada à identidade do provedor."""
        return self.provedor_auth != ProvedorAuth.LOCAL and self.provedor_sub is None

    __table_args__ = (
        UniqueConstraint("provedor_auth", "provedor_sub", name="uq_usuario_provedor_sub"),
        CheckConstraint(
            "provedor_auth <> 'local' OR senha_hash IS NOT NULL",
            name="ck_usuario_local_tem_senha",
        ),
    )


class CodigoRecuperacao(Base):
    """Códigos de uso único para entrar sem o celular. Guardados só como hash (SHA-256)."""

    __tablename__ = "codigos_recuperacao"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    codigo_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    usado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    usuario: Mapped["Usuario"] = relationship(back_populates="codigos_recuperacao")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    expira_em: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revogado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Preenchido só na rotação (troca normal por um token novo), não no logout/revogação.
    substituido_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
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
    # Bloqueio otimista: todo UPDATE vira "... WHERE versao = <a que eu li>" e soma 1.
    # Duas pessoas salvando a partir da mesma versão: uma vence, a outra recebe 409.
    versao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")

    __mapper_args__ = {"version_id_col": versao}

    dono: Mapped["Usuario"] = relationship()
    fases: Mapped[List["Fase"]] = relationship(
        back_populates="projeto",
        cascade="all, delete-orphan",
        order_by="Fase.ordem_sequencia",
    )
    membros: Mapped[List["ProjetoMembro"]] = relationship(
        back_populates="projeto", cascade="all, delete-orphan"
    )
    comentarios: Mapped[List["Comentario"]] = relationship(
        back_populates="projeto", cascade="all, delete-orphan"
    )
    pilares: Mapped[List["Pilar"]] = relationship(
        back_populates="projeto", cascade="all, delete-orphan", order_by="(Pilar.ordem, Pilar.id)"
    )

    __table_args__ = (
        CheckConstraint("orcamento_aprovado >= 0", name="ck_projeto_orcamento_positivo"),
    )


class ProjetoMembro(Base):
    """
    Quem enxerga o projeto e com qual papel. O dono também é registrado aqui (como editor).
    Dono e admin, além de editar, gerenciam membros e podem excluir o projeto.
    """

    __tablename__ = "projeto_membros"

    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("projetos.id", ondelete="CASCADE"), primary_key=True
    )
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), primary_key=True, index=True
    )
    papel: Mapped[PapelProjeto] = mapped_column(
        _enum(PapelProjeto),
        default=PapelProjeto.VISUALIZADOR,  # menor privilégio
        server_default=PapelProjeto.VISUALIZADOR.value,
        nullable=False,
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
    # Frente de trabalho (TI, Suprimentos...): cruza as fases. Excluir o pilar não exclui a tarefa.
    pilar_id: Mapped[Optional[int]] = mapped_column(ForeignKey("pilares.id", ondelete="SET NULL"), index=True)
    versao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")  # ver Projeto.versao

    __mapper_args__ = {"version_id_col": versao}

    fase: Mapped["Fase"] = relationship(back_populates="tarefas")
    responsavel_usuario: Mapped[Optional["Usuario"]] = relationship()
    pilar: Mapped[Optional["Pilar"]] = relationship()
    comentarios: Mapped[List["Comentario"]] = relationship(
        back_populates="tarefa", cascade="all, delete"
    )

    __table_args__ = (
        CheckConstraint(
            "percentual_conclusao BETWEEN 0 AND 100", name="ck_tarefa_percentual"
        ),
        CheckConstraint("data_fim >= data_inicio", name="ck_tarefa_datas"),
    )


class Pilar(Base):
    """
    Frente de trabalho dentro do projeto (TI, Suprimentos, CAPEX, Pessoas...).
    O andamento vem das tarefas ligadas a ele; o farol e o texto de status são a avaliação do
    responsável; orçado x realizado mostra a execução financeira da frente.
    """

    __tablename__ = "pilares"

    id: Mapped[int] = mapped_column(primary_key=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("projetos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    nome: Mapped[str] = mapped_column(String(60), nullable=False)
    # "TI", "ti" e " T I " são o mesmo pilar. A unicidade fica no BANCO (sobre esta chave), então
    # vale até para duas pessoas criando "TI" e "ti" no mesmo instante.
    nome_chave: Mapped[str] = mapped_column(String(60), nullable=False)
    ordem: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    farol: Mapped[Optional[FarolPilar]] = mapped_column(_enum(FarolPilar))
    status_texto: Mapped[Optional[str]] = mapped_column(Text)
    status_atualizado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status_atualizado_por_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    valor_orcado: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    valor_realizado: Mapped[Optional[Decimal]] = mapped_column(Numeric(14, 2))
    versao: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")  # ver Projeto.versao
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __mapper_args__ = {"version_id_col": versao}

    projeto: Mapped["Projeto"] = relationship(back_populates="pilares")
    status_atualizado_por: Mapped[Optional["Usuario"]] = relationship()

    @validates("nome")
    def _definir_chave(self, _campo: str, nome: str) -> str:
        self.nome_chave = chave_do_pilar(nome)
        return nome

    __table_args__ = (
        UniqueConstraint("projeto_id", "nome_chave", name="uq_pilar_projeto_nome_chave"),
        CheckConstraint("valor_orcado >= 0", name="ck_pilar_orcado_positivo"),
        CheckConstraint("valor_realizado >= 0", name="ck_pilar_realizado_positivo"),
    )


# ---------- Colaboração ----------


class Comentario(Base):
    """Comentário no projeto (tarefa_id nulo) ou numa tarefa. Qualquer membro pode comentar."""

    __tablename__ = "comentarios"

    id: Mapped[int] = mapped_column(primary_key=True)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("projetos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tarefa_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("tarefas.id", ondelete="CASCADE"), index=True
    )
    # SET NULL: o comentário continua no histórico se o usuário for excluído.
    autor_id: Mapped[Optional[int]] = mapped_column(ForeignKey("usuarios.id", ondelete="SET NULL"))
    texto: Mapped[str] = mapped_column(Text, nullable=False)
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    editado_em: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    projeto: Mapped["Projeto"] = relationship(back_populates="comentarios")
    tarefa: Mapped[Optional["Tarefa"]] = relationship(back_populates="comentarios")
    autor: Mapped[Optional["Usuario"]] = relationship()


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


class ChaveIdempotencia(Base):
    """
    Header Idempotency-Key das criações (projeto manual/importado): a mesma chave enviada de novo
    (duplo clique, retentativa após queda de rede, duas abas) devolve o projeto já criado em vez de
    criar outro. A unicidade (usuario_id, chave) no banco resolve até envios simultâneos.
    """

    __tablename__ = "chaves_idempotencia"

    id: Mapped[int] = mapped_column(primary_key=True)
    usuario_id: Mapped[int] = mapped_column(
        ForeignKey("usuarios.id", ondelete="CASCADE"), nullable=False
    )
    chave: Mapped[str] = mapped_column(String(100), nullable=False)
    rota: Mapped[str] = mapped_column(String(50), nullable=False)
    hash_corpo: Mapped[str] = mapped_column(String(64), nullable=False)
    projeto_id: Mapped[int] = mapped_column(
        ForeignKey("projetos.id", ondelete="CASCADE"), nullable=False, index=True
    )
    criado_em: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (UniqueConstraint("usuario_id", "chave", name="uq_idempotencia_usuario_chave"),)
