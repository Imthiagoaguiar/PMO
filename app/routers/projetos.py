import logging
import re
import unicodedata
from decimal import Decimal
from typing import Any, Dict, List, Optional

from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.exceptions import RequestValidationError
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, selectinload

from app import auditoria, fila, idempotencia, relatorio, services, tempo
from app.config import settings
from app.routers.importacoes import ler_importacao
from app.database import get_db
from app.integracoes import ia, notificacoes
from app.deps import (
    IdPath,
    STATUS_ENCERRADOS,
    conferir_versao,
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
    Comentario,
    Fase,
    Importacao,
    StatusImportacao,
    PapelUsuario,
    Projeto,
    ProjetoMembro,
    StatusProjeto,
    Tarefa,
    Usuario,
)
from app.rate_limit import limite_ia, limite_importacao, limite_relatorio
from app.schemas import (
    MembroAlterar,
    MembroRead,
    PermissoesProjeto,
    ProjetoIAEntrada,
    SugestaoProjetoIA,
    AuditoriaRead,
    ImportacaoRead,
    MembroAdicionar,
    Pagina,
    ProjetoIndicadores,
    ProjetoManualCreate,
    FaseRead,
    PilarResumo,
    ProjetoRead,
    ProjetoResumo,
    ProjetoUpdate,
    TarefaRead,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/projetos", tags=["Projetos"])

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

pode_criar_projeto = exigir_papel(PapelUsuario.ADMIN, PapelUsuario.GERENTE)


# ---------- Criação ----------


def _projeto_read(db: Session, projeto: Projeto, usuario: Usuario) -> ProjetoRead:
    """
    Detalhe do projeto com as primeiras 200 tarefas de cada fase (+ o total). Projeto de 100 mil
    tarefas não vira um JSON de 23 MB: o resto é paginado em GET /projetos/{id}/tarefas.
    """
    por_fase = services.tarefas_por_fase(db, projeto)
    return ProjetoRead(
        **ProjetoResumo.model_validate(projeto).model_dump(),
        fases=[
            FaseRead(id=f.id, titulo=f.titulo, ordem_sequencia=f.ordem_sequencia,
                     tarefas=[TarefaRead.model_validate(t) for t in por_fase[f.id][1]], total_tarefas=por_fase[f.id][0])
            for f in projeto.fases
        ],
        pilares=[PilarResumo.model_validate(pi) for pi in projeto.pilares],
        permissoes=PermissoesProjeto(editar=pode_editar(projeto, usuario), gerenciar=pode_gerenciar(projeto, usuario)),
    )


def _membros(projeto: Projeto) -> List[MembroRead]:
    return [
        MembroRead(
            id=m.usuario.id, nome=m.usuario.nome, email=m.usuario.email,
            papel=m.papel, dono=m.usuario_id == projeto.dono_id,
        )
        for m in sorted(projeto.membros, key=lambda m: (m.usuario_id != projeto.dono_id, m.usuario.nome))
    ]


def _repeticao(
    db: Session, usuario: Usuario, chave: Optional[str], rota: str, hash_corpo: str, response: Response
) -> Optional[Projeto]:
    """Mesma Idempotency-Key já usada: o projeto que ela criou (senão None)."""
    projeto_id = idempotencia.projeto_ja_criado(db, usuario.id, chave, rota, hash_corpo)
    if projeto_id is None:
        return None
    response.headers["Idempotent-Replayed"] = "true"
    return obter_projeto_visivel(db, projeto_id, usuario)


def _gravar_criacao(
    db: Session, usuario: Usuario, projeto: Projeto, chave: Optional[str], rota: str, hash_corpo: str,
    response: Response,
) -> Projeto:
    """
    Commit da criação. Se outra requisição com a MESMA chave gravou primeiro (envio simultâneo),
    a unicidade da chave barra esta e devolvemos o projeto da outra: um projeto só.
    """
    if chave is not None:
        idempotencia.registrar(db, usuario.id, chave, rota, hash_corpo, projeto.id)
    try:
        db.commit()
        return projeto
    except IntegrityError:
        db.rollback()
        anterior = _repeticao(db, usuario, chave, rota, hash_corpo, response)
        if anterior is not None:
            return anterior
        logger.exception("Falha ao gravar a criação do projeto")
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Falha ao gravar a criação do projeto")
    raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao salvar. Tente novamente.")


def _avisar_por_email(tarefas_fundo: BackgroundTasks, usuario: Usuario, projeto: Projeto) -> None:
    """Agenda o e-mail para depois da resposta (e só depois do commit: não avisa projeto que não existe)."""
    tarefas_fundo.add_task(
        notificacoes.enviar_projeto_iniciado, usuario.email, usuario.nome, projeto.nome_projeto
    )


@router.post("/manual", response_model=ProjetoRead, status_code=status.HTTP_201_CREATED)
def criar_projeto_manual(
    payload: ProjetoManualCreate,
    tarefas_fundo: BackgroundTasks,
    response: Response,
    chave: Optional[str] = Depends(idempotencia.chave_do_header),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(pode_criar_projeto),
) -> ProjetoRead:
    """
    Modo 1: cria o projeto do zero, já com as 5 fases do Waterfall.
    Opcionalmente recebe as tarefas iniciais em `fases` (ex.: a sugestão revisada do /projetos/ia).
    Tarefas com `duracao_dias_uteis` ganham datas encadeadas a partir de `data_inicio`.
    Com o header Idempotency-Key, repetir o envio devolve o mesmo projeto (não cria outro).
    """
    rota, hash_corpo = "projetos.manual", idempotencia.resumo(payload.model_dump_json().encode())
    repetido = _repeticao(db, usuario, chave, rota, hash_corpo, response)
    if repetido is not None:
        return _projeto_read(db, repetido, usuario)

    projeto = services.criar_projeto_com_fases(db, usuario, **payload.model_dump(exclude={"fases"}))
    total_tarefas = services.adicionar_cronograma(projeto, payload.fases, payload.data_inicio)
    db.flush()  # gera o id para a auditoria
    auditoria.registrar(
        db, "projeto.criado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"modo": "manual", "nome_projeto": projeto.nome_projeto, "tarefas": total_tarefas},
    )
    gravado = _gravar_criacao(db, usuario, projeto, chave, rota, hash_corpo, response)
    if gravado is projeto:  # repetição não manda o e-mail de novo
        _avisar_por_email(tarefas_fundo, usuario, projeto)
    return _projeto_read(db, gravado, usuario)


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
    response_model=ImportacaoRead,
    status_code=status.HTTP_202_ACCEPTED,
    responses={503: {"description": "Fila de processamento indisponível"}},
)
def importar_projeto(
    request: Request,
    response: Response,
    chave: Optional[str] = Depends(idempotencia.chave_do_header),
    arquivo: UploadFile = File(..., description="Planilha .xlsx ou .csv"),
    nome_projeto: str = Form(..., max_length=300),
    orcamento_aprovado: Optional[Decimal] = Form(None),
    data_inicio: Optional[str] = Form(None, description="AAAA-MM-DD"),
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(pode_criar_projeto),
) -> ImportacaoRead:
    """
    Modo 2: envia a planilha e responde NA HORA (202) com a importação "na fila". O worker
    (`python -m app.trabalhador`) lê, valida e grava em segundo plano; acompanhe em GET /importacoes/{id}.
    Colunas obrigatórias: Fase, Descricao. Opcionais: Data Inicio, Data Fim, Responsavel, Percentual Conclusao, Pilar.
    Tudo-ou-nada: com qualquer linha inválida, nada é gravado e a importação termina "falhou" com os erros.
    """
    limite_importacao.por_usuario(usuario.id)
    # Validações baratas aqui (resposta imediata); o trabalho pesado (ler e validar as linhas) fica com o worker.
    try:
        dados = ProjetoManualCreate(
            nome_projeto=nome_projeto, orcamento_aprovado=orcamento_aprovado, data_inicio=data_inicio or None
        )
    except ValidationError as exc:
        raise RequestValidationError(exc.errors(include_url=False, include_context=False))

    limite = settings.importacao_max_mb * 1024 * 1024
    conteudo = arquivo.file.read(limite + 1)
    if len(conteudo) > limite:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Arquivo maior que {settings.importacao_max_mb} MB.",
        )
    nome_arquivo = (arquivo.filename or "").replace("\x00", "")[:255]
    if not nome_arquivo.lower().endswith((".csv", ".xlsx")):
        raise HTTPException(status_code=422, detail={"mensagem": "Formato não suportado. Envie um arquivo .csv ou .xlsx.", "erros": []})
    if not conteudo:
        raise HTTPException(status_code=422, detail={"mensagem": "O arquivo está vazio.", "erros": []})

    hash_corpo = idempotencia.resumo(conteudo, nome_arquivo.encode(), dados.model_dump_json(exclude={"fases"}).encode())
    repetida = _importacao_repetida(db, usuario, chave, hash_corpo, response)
    if repetida is not None:
        return repetida

    importacao = Importacao(
        usuario_id=usuario.id, nome_projeto=dados.nome_projeto, orcamento_aprovado=dados.orcamento_aprovado,
        data_inicio=dados.data_inicio, arquivo_nome=nome_arquivo, arquivo_bytes=len(conteudo), conteudo=conteudo,
        ip=request.client.host if request.client else None, chave_idempotencia=chave, hash_corpo=hash_corpo,
        etapa="Na fila",
    )
    db.add(importacao)
    try:
        db.commit()  # gravada ANTES de enfileirar: o worker precisa encontrá-la no banco
    except IntegrityError:  # mesma Idempotency-Key enviada ao mesmo tempo: a outra requisição venceu
        db.rollback()
        repetida = _importacao_repetida(db, usuario, chave, hash_corpo, response)
        if repetida is not None:
            return repetida
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Erro ao salvar. Tente novamente.")

    try:
        fila.enfileirar_importacao(importacao.id)
    except fila.RedisError:
        logger.exception("Fila indisponível ao enfileirar a importação %s", importacao.id)
        importacao.status, importacao.etapa, importacao.conteudo = StatusImportacao.FALHOU, "Falhou", None
        importacao.mensagem = "A fila de processamento está fora do ar. Tente de novo em alguns minutos."
        db.commit()
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=importacao.mensagem)

    db.refresh(importacao)  # com a fila síncrona (testes), o job já rodou
    response.headers["Location"] = f"/importacoes/{importacao.id}"
    return ler_importacao(importacao)


def _importacao_repetida(
    db: Session, usuario: Usuario, chave: Optional[str], hash_corpo: str, response: Response
) -> Optional[ImportacaoRead]:
    if chave is None:
        return None
    existente = db.scalar(select(Importacao).where(Importacao.usuario_id == usuario.id, Importacao.chave_idempotencia == chave))
    if existente is None:
        return None
    if existente.hash_corpo != hash_corpo:
        raise HTTPException(
            status_code=422,
            detail="Esta Idempotency-Key já foi usada em outra requisição. Gere uma chave nova para cada criação.",
        )
    response.headers["Idempotent-Replayed"] = "true"
    response.headers["Location"] = f"/importacoes/{existente.id}"
    return ler_importacao(existente)



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
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ProjetoRead:
    return _projeto_read(db, obter_projeto_visivel(db, projeto_id, usuario), usuario)


@router.get("/{projeto_id}/indicadores", response_model=ProjetoIndicadores)
def indicadores_projeto(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> ProjetoIndicadores:
    """Progresso geral e por fase, atrasos e fase atual — base para o dashboard."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    return services.calcular_indicadores(db, projeto)


@router.get(
    "/{projeto_id}/exportar",
    response_class=Response,
    responses={200: {"content": {MIME_XLSX: {}}, "description": "Planilha .xlsx do cronograma"}},
)
def exportar_projeto(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Response:
    """Baixa o cronograma em .xlsx, no mesmo formato aceito por /projetos/importar."""
    projeto = obter_projeto_visivel(db, projeto_id, usuario)  # as tarefas vêm em lotes, na exportação
    conteudo = services.exportar_planilha(db, projeto)
    auditoria.registrar(db, "projeto.exportado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id)
    salvar(db)
    return Response(
        content=conteudo,
        media_type=MIME_XLSX,
        headers={"Content-Disposition": _content_disposition(projeto.nome_projeto + ".xlsx")},
    )


@router.get(
    "/{projeto_id}/relatorio",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "Relatório do projeto em PDF"}},
)
def relatorio_pdf(
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Response:
    """
    Resumo e indicadores, pilares (farol, andamento, orçado x realizado), linha do tempo,
    histórico e comentários recentes. Qualquer membro pode gerar (inclusive visualizador).
    """
    limite_relatorio.por_usuario(usuario.id)
    projeto = obter_projeto_visivel(db, projeto_id, usuario)
    historico = db.scalars(
        select(Auditoria).options(selectinload(Auditoria.usuario))
        .where(Auditoria.projeto_id == projeto.id)
        .order_by(Auditoria.criado_em.desc(), Auditoria.id.desc()).limit(30)
    ).all()
    comentarios = db.scalars(
        select(Comentario).options(selectinload(Comentario.autor), selectinload(Comentario.tarefa))
        .where(Comentario.projeto_id == projeto.id)
        .order_by(Comentario.criado_em.desc(), Comentario.id.desc()).limit(20)
    ).all()
    hoje = tempo.hoje()
    conteudo = relatorio.gerar_pdf(relatorio.DadosRelatorio(
        projeto=projeto,
        dono=projeto.dono.nome,
        indicadores=services.calcular_indicadores(db, projeto, hoje),
        pilares=services.calcular_pilares(db, projeto, services.listar_pilares(db, projeto.id), hoje),
        **relatorio.tarefas_da_linha_do_tempo(db, projeto),
        historico=historico,
        comentarios=comentarios,
        gerado_por=usuario.nome,
        agora_utc=tempo.agora_utc(),
        hoje=hoje,
    ))
    auditoria.registrar(db, "projeto.relatorio_pdf", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id)
    salvar(db)
    return Response(
        content=conteudo,
        media_type="application/pdf",
        headers={"Content-Disposition": _content_disposition(f"Relatório - {projeto.nome_projeto}.pdf")},
    )


@router.get("/{projeto_id}/historico", response_model=Pagina[AuditoriaRead])
def historico_projeto(
    projeto_id: IdPath,
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
    projeto_id: IdPath,
    payload: ProjetoUpdate,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> Projeto:
    """Dono, admin ou editor. Projeto encerrado só aceita mudança de status (para ser reaberto)."""
    projeto = obter_projeto_editavel(db, projeto_id, usuario)
    alteracoes = payload.model_dump(exclude_unset=True)
    conferir_versao(projeto, alteracoes.pop("versao", None))
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
    projeto_id: IdPath,
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
    projeto_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MembroRead]:
    """O dono vem primeiro; depois os demais por nome."""
    return _membros(obter_projeto_visivel(db, projeto_id, usuario, carregar_membros=True))


@router.post(
    "/{projeto_id}/membros",
    response_model=List[MembroRead],
    status_code=status.HTTP_201_CREATED,
)
def adicionar_membro(
    projeto_id: IdPath,
    payload: MembroAdicionar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MembroRead]:
    """Entra como visualizador, a menos que `papel` diga o contrário."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario, carregar_membros=True)

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
    salvar(db, conflito="Usuário já é membro do projeto.")  # duas requisições ao mesmo tempo
    return _membros(projeto)


@router.patch("/{projeto_id}/membros/{usuario_id}", response_model=List[MembroRead])
def alterar_papel_membro(
    projeto_id: IdPath,
    usuario_id: IdPath,
    payload: MembroAlterar,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> List[MembroRead]:
    """Promove a editor ou rebaixa a visualizador (dono ou admin)."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario, carregar_membros=True)
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
    projeto_id: IdPath,
    usuario_id: IdPath,
    db: Session = Depends(get_db),
    usuario: Usuario = Depends(get_usuario_atual),
) -> None:
    """Remove o membro e tira dele a responsabilidade pelas tarefas deste projeto."""
    projeto = obter_projeto_gerenciavel(db, projeto_id, usuario, carregar_membros=True)
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
        # Conta como edição: quem estava com a tarefa aberta não sobrescreve o responsável removido.
        .values(responsavel_id=None, versao=Tarefa.versao + 1)
    )
    projeto.membros.remove(membro)
    auditoria.registrar(
        db, "membro.removido", "usuario", usuario_id, usuario=usuario, projeto_id=projeto.id,
        detalhes={"email": membro.usuario.email},
    )
    salvar(db)


# ---------- Helpers ----------


def _content_disposition(nome: str) -> str:
    """
    RFC 6266: `filename` só em ASCII (navegadores antigos) e `filename*` com o nome real em UTF-8,
    para "Implantação São Paulo.xlsx" não virar "Implantacao_Sao_Paulo.xlsx".
    """
    base, _, extensao = nome.rpartition(".")
    real = re.sub(r'[\x00-\x1f\x7f"\\/]+', "_", base).strip()[:150] or "projeto"
    return (
        f'attachment; filename="{_nome_arquivo(base)}.{extensao}"; '
        f"filename*=UTF-8''{quote(real + '.' + extensao)}"
    )


def _nome_arquivo(nome: str) -> str:
    """Nome seguro para o header Content-Disposition (sem acentos, aspas ou quebras de linha)."""
    ascii_ = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Za-z0-9_-]+", "_", ascii_).strip("_")[:80] or "projeto"
