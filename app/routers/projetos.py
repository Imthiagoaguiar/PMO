import re
import unicodedata
from datetime import date
from decimal import Decimal
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Response, UploadFile, status
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app import auditoria, services
from app.database import get_db
from app.integracoes import ia, notificacoes
from app.deps import (
    STATUS_ENCERRADOS,
    escapar_like,
    Paginacao,
    exigir_papel,
    exigir_projeto_aberto,
    filtrar_projetos_visiveis,
    get_usuario_atual,
    obter_projeto_editavel,
    obter_projeto_gerenciavel,
    obter_projeto_visivel,
    pode_editar,
    pode_gerenciar,
    paginar,
    salvar,
)
from app.models import (
    Auditoria,
    Fase,
    PapelUsuario,
    Projeto,
    ProjetoMembro,
    StatusProjeto,
    Tarefa,
    Usuario,
)
from app.rate_limit import limite_ia, limite_importacao
from app.schemas import (
    MembroAlterar,
    MembroRead,
    PermissoesProjeto,
    ProjetoIAEntrada,
    SugestaoProjetoIA,
    AuditoriaRead,
    ImportacaoResultado,
    MembroAdicionar,
    Pagina,
    ProjetoIndicadores,
    ProjetoManualCreate,
    ProjetoRead,
    ProjetoResumo,
    ProjetoUpdate,
)

router = APIRouter(prefix="/projetos", tags=["Projetos"])

MAX_TAMANHO_ARQUIVO = 5 * 1024 * 1024  # 5 MB
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

pode_criar_projeto = exigir_papel(PapelUsuario.ADMIN, PapelUsuario.GERENTE)


# ---------- Criação ----------


def _projeto_read(projeto: Projeto, usuario: Usuario) -> ProjetoRead:
    leitura = ProjetoRead.model_validate(projeto)
    leitura.permissoes = PermissoesProjeto(
        editar=pode_editar(projeto, usuario), gerenciar=pode_gerenciar(projeto, usuario)
    )
    return leitura


def _membros(projeto: Projeto) -> List[MembroRead]:
    return [
        MembroRead(
            id=m.usuario.id, nome=m.usuario.nome, email=m.usuario.email,
            papel=m.papel, dono=m.usuario_id == projeto.dono_id,
        )
        for m in sorted(projeto.membros, key=lambda m: (m.usuario_id != projeto.dono_id, m.usuario.nome))
    ]


def _avisar_por_email(tarefas_fundo: BackgroundTasks, usuario: Usuario, projeto: Projeto) -> None:
    """Agenda o e-mail para depois da resposta (e só depois do commit: não avisa projeto que não existe)."""
    tarefas_fundo.add_task(
        notificacoes.enviar_projeto_iniciado, usuario.email, usuario.nome, projeto.nome_projeto
    )


@router.post("/manual", response_model=ProjetoRead, status_code=status.HTTP_201_CREATED)
def criar_projeto_manual(
    payload: ProjetoManualCreate,
    tarefas_fundo: BackgroundTasks,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(pode_criar_projeto),
) -> ProjetoRead:
    """
    Modo 1: cria o projeto do zero, já com as 5 fases do Waterfall.
    Opcionalmente recebe as tarefas iniciais em `fases` (ex.: a sugestão revisada do /projetos/ia).
    Tarefas com `duracao_dias_uteis` ganham datas encadeadas a partir de `data_inicio`.
    """
    projeto = services.criar_projeto_com_fases(db, usuario, **payload.model_dump(exclude={"fases"}))
    total_tarefas = services.adicionar_cronograma(projeto, payload.fases, payload.data_inicio)
    db.flush()  # gera o id para a auditoria
    auditoria.registrar(
        db, "projeto.criado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"modo": "manual", "nome_projeto": projeto.nome_projeto, "tarefas": total_tarefas},
    )
    salvar(db)
    _avisar_por_email(tarefas_fundo, usuario, projeto)
    return _projeto_read(projeto, usuario)


@router.post("/ia", response_model=SugestaoProjetoIA)
def sugerir_projeto_com_ia(
    payload: ProjetoIAEntrada,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(pode_criar_projeto),
) -> Dict[str, Any]:
    """
    Modo 3: lê o Termo de Abertura e sugere nome + tarefas nas 5 fases (Groq).
    Não grava nada: o gerente revisa no front e envia `fases` para POST /projetos/manual,
    que cria tudo de uma vez e calcula as datas com os feriados.
    """
    limite_ia.por_usuario(usuario.id)  # por pessoa: colegas no mesmo IP não dividem a cota
    try:
        sugestao = ia.sugerir_escopo(payload.texto)
    except ia.FalhaIA as exc:
        raise HTTPException(status_code=exc.status_http, detail=exc.mensagem)

    # Registra o uso (custo), mas não o texto: o Termo de Abertura pode ser confidencial.
    auditoria.registrar(
        db, "projeto.sugestao_ia", "projeto", usuario=usuario,
        detalhes={"caracteres": len(payload.texto), "modelo": sugestao["modelo"],
                  "tarefas": sum(len(f["tarefas"]) for f in sugestao["fases"])},
    )
    salvar(db)
    return sugestao


@router.post(
    "/importar",
    response_model=ImportacaoResultado,
    status_code=status.HTTP_201_CREATED,
)
def importar_projeto(
    tarefas_fundo: BackgroundTasks,
    arquivo: UploadFile = File(..., description="Planilha .xlsx ou .csv"),
    nome_projeto: str = Form(..., min_length=3, max_length=200),
    orcamento_aprovado: Optional[Decimal] = Form(None, ge=0),
    data_inicio: Optional[date] = Form(None),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(pode_criar_projeto),
) -> ImportacaoResultado:
    """
    Modo 2: cria o projeto + 5 fases e importa as tarefas da planilha.
    Colunas obrigatórias: Fase, Descricao. Opcionais: Data Inicio, Data Fim, Responsavel, Percentual Conclusao.
    A importação é tudo-ou-nada: se qualquer linha for inválida, nada é gravado.
    """
    limite_importacao.por_usuario(usuario.id)
    conteudo = arquivo.file.read(MAX_TAMANHO_ARQUIVO + 1)
    if len(conteudo) > MAX_TAMANHO_ARQUIVO:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Arquivo maior que 5 MB.",
        )

    # Lê e valida tudo ANTES de tocar no banco.
    try:
        df = services.ler_planilha(conteudo, arquivo.filename or "")
        tarefas = services.validar_tarefas(df)
    except services.PlanilhaInvalida as exc:
        raise HTTPException(
            status_code=422,  # Unprocessable Content
            detail={"mensagem": exc.mensagem, "erros": exc.erros},
        )

    projeto = services.criar_projeto_com_fases(
        db, usuario, nome_projeto.strip(), orcamento_aprovado, data_inicio
    )
    services.adicionar_tarefas(projeto, tarefas)
    db.flush()
    auditoria.registrar(
        db, "projeto.importado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"arquivo": (arquivo.filename or "")[:200], "tarefas": len(tarefas)},
    )
    salvar(db)
    _avisar_por_email(tarefas_fundo, usuario, projeto)

    return ImportacaoResultado(
        projeto=_projeto_read(projeto, usuario),
        total_tarefas_importadas=len(tarefas),
    )


# ---------- Consulta ----------


@router.get("", response_model=Pagina[ProjetoResumo])
def listar_projetos(
    status_projeto: Optional[StatusProjeto] = Query(None, alias="status"),
    busca: Optional[str] = Query(None, max_length=100, description="Parte do nome do projeto"),
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    query = filtrar_projetos_visiveis(select(Projeto), usuario)
    if status_projeto:
        query = query.where(Projeto.status == status_projeto)
    if busca and busca.strip():
        query = query.where(Projeto.nome_projeto.ilike(f"%{escapar_like(busca.strip())}%", escape="\\"))
    return paginar(db, query.order_by(Projeto.id.desc()), pagina)


@router.get("/{projeto_id}", response_model=ProjetoRead)
def detalhar_projeto(
    projeto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ProjetoRead:
    return _projeto_read(obter_projeto_visivel(db, projeto_id, usuario, carregar_tarefas=True), usuario)


@router.get("/{projeto_id}/indicadores", response_model=ProjetoIndicadores)
def indicadores_projeto(
    projeto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ProjetoIndicadores:
    """Progresso geral e por fase, atrasos e fase atual — base para o dashboard."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario, carregar_tarefas=True)
    return services.calcular_indicadores(projeto)


@router.get(
    "/{projeto_id}/exportar",
    response_class=Response,
    responses={200: {"content": {MIME_XLSX: {}}, "description": "Planilha .xlsx do cronograma"}},
)
def exportar_projeto(
    projeto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Response:
    """Baixa o cronograma em .xlsx, no mesmo formato aceito por /projetos/importar."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario, carregar_tarefas=True)
    conteudo = services.exportar_planilha(projeto)
    auditoria.registrar(db, "projeto.exportado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id)
    salvar(db)
    return Response(
        content=conteudo,
        media_type=MIME_XLSX,
        headers={"Content-Disposition": f'attachment; filename="{_nome_arquivo(projeto.nome_projeto)}.xlsx"'},
    )


@router.get("/{projeto_id}/historico", response_model=Pagina[AuditoriaRead])
def historico_projeto(
    projeto_id: int,
    pagina: Paginacao = Depends(),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Dict[str, Any]:
    """Quem alterou o quê neste projeto, do mais recente para o mais antigo."""
    obter_projeto_visivel(db, projeto_id, usuario)
    query = (
        select(Auditoria)
        .options(selectinload(Auditoria.usuario))
        .where(Auditoria.projeto_id == projeto_id)
        .order_by(Auditoria.criado_em.desc(), Auditoria.id.desc())
    )
    return paginar(db, query, pagina)


# ---------- Edição ----------


@router.patch("/{projeto_id}", response_model=ProjetoResumo)
def atualizar_projeto(
    projeto_id: int,
    payload: ProjetoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Projeto:
    """Dono, admin ou editor. Projeto encerrado só aceita mudança de status (para ser reaberto)."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    alteracoes = payload.model_dump(exclude_unset=True)
    if projeto.status in STATUS_ENCERRADOS and set(alteracoes) - {"status"}:
        exigir_projeto_aberto(projeto)

    diff = auditoria.aplicar_alteracoes(projeto, alteracoes)
    if diff:
        auditoria.registrar(
            db, "projeto.atualizado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id, detalhes=diff
        )
        salvar(db)
    return projeto


@router.delete("/{projeto_id}", status_code=status.HTTP_204_NO_CONTENT)
def excluir_projeto(
    projeto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    """Exclui o projeto com fases, tarefas e membros. Não pode ser desfeito (o histórico fica)."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario)
    auditoria.registrar(
        db, "projeto.excluido", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"nome_projeto": projeto.nome_projeto},
    )
    db.delete(projeto)
    salvar(db)


# ---------- Membros ----------


@router.get("/{projeto_id}/membros", response_model=List[MembroRead])
def listar_membros(
    projeto_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MembroRead]:
    """O dono vem primeiro; depois os demais por nome."""
    return _membros(obter_projeto_visivel(db, projeto_id, usuario))


@router.post(
    "/{projeto_id}/membros",
    response_model=List[MembroRead],
    status_code=status.HTTP_201_CREATED,
)
def adicionar_membro(
    projeto_id: int,
    payload: MembroAdicionar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MembroRead]:
    """Entra como visualizador, a menos que `papel` diga o contrário."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario)

    novo = db.get(Usuario, payload.usuario_id)
    if novo is None or not novo.ativo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado.")
    if any(m.usuario_id == novo.id for m in projeto.membros):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Usuário já é membro do projeto.")

    projeto.membros.append(ProjetoMembro(usuario=novo, papel=payload.papel))
    auditoria.registrar(
        db, "membro.adicionado", "usuario", novo.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"email": novo.email, "papel": payload.papel},
    )
    salvar(db)
    return _membros(projeto)


@router.patch("/{projeto_id}/membros/{usuario_id}", response_model=List[MembroRead])
def alterar_papel_membro(
    projeto_id: int,
    usuario_id: int,
    payload: MembroAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MembroRead]:
    """Promove a editor ou rebaixa a visualizador (dono ou admin)."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario)
    if usuario_id == projeto.dono_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="O dono do projeto sempre pode editar."
        )
    membro = next((m for m in projeto.membros if m.usuario_id == usuario_id), None)
    if membro is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro não encontrado.")

    diff = auditoria.aplicar_alteracoes(membro, {"papel": payload.papel})
    if diff:
        auditoria.registrar(
            db, "membro.papel_alterado", "usuario", usuario_id, usuario=usuario, projeto_id=projeto.id,
            detalhes={"email": membro.usuario.email, **diff},
        )
        salvar(db)
    return _membros(projeto)


@router.delete("/{projeto_id}/membros/{usuario_id}", status_code=status.HTTP_204_NO_CONTENT)
def remover_membro(
    projeto_id: int,
    usuario_id: int,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    """Remove o membro e tira dele a responsabilidade pelas tarefas deste projeto."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario)
    if usuario_id == projeto.dono_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="O dono do projeto não pode ser removido."
        )

    membro = next((m for m in projeto.membros if m.usuario_id == usuario_id), None)
    if membro is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Membro não encontrado.")

    ids_fases = select(Fase.id).where(Fase.projeto_id == projeto.id)
    db.execute(
        update(Tarefa)
        .where(Tarefa.fase_id.in_(ids_fases), Tarefa.responsavel_id == usuario_id)
        .values(responsavel_id=None)
    )
    projeto.membros.remove(membro)
    auditoria.registrar(
        db, "membro.removido", "usuario", usuario_id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"email": membro.usuario.email},
    )
    salvar(db)


# ---------- Helpers ----------


def _nome_arquivo(nome: str) -> str:
    """Nome seguro para o header Content-Disposition (sem acentos, aspas ou quebras de linha)."""
    ascii_ = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_).strip("_")[:80] or "projeto"
