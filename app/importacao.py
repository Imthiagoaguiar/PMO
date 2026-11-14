"""
Job da fila: processa uma planilha enviada para POST /projetos/importar.

- Tudo-ou-nada: o projeto, as tarefas, a auditoria e o status "concluida" vão no MESMO commit.
  Se qualquer coisa falhar, nada do projeto fica gravado e a importação vira "falhou".
- O progresso vai para o Redis (job.meta) e não para o banco: assim ele aparece ao vivo sem
  precisar commitar a transação da importação no meio do caminho.
- Idempotente: se o mesmo job for entregue duas vezes, a segunda vê que já não está "na fila" e sai.
"""

import logging
from typing import Optional

from rq import get_current_job
from sqlalchemy import update

from app import auditoria, services
from app.auditoria import ip_requisicao
from app.database import SessionLocal
from app.integracoes import notificacoes
from app.models import Importacao, StatusImportacao, Usuario
from app.security import agora_utc

logger = logging.getLogger("pmo.importacao")
MAX_ERROS_GUARDADOS = 200
FALHA_INTERNA = "Erro interno ao importar a planilha. Tente de novo; se continuar, avise o administrador."


def _progresso(percentual: int, etapa: str) -> None:
    job = get_current_job()
    if job is not None:
        job.meta.update({"progresso": percentual, "etapa": etapa})
        job.save_meta()


def processar(importacao_id: int) -> Optional[int]:
    """Devolve o id do projeto criado (ou None se falhou / já tinha sido processada)."""
    with SessionLocal() as db:
        # Pega a importação para si de forma atômica: dois workers com o mesmo job não processam em dobro.
        pegou = db.execute(
            update(Importacao)
            .where(Importacao.id == importacao_id, Importacao.status == StatusImportacao.NA_FILA)
            .values(status=StatusImportacao.PROCESSANDO, iniciada_em=agora_utc(), etapa="Lendo a planilha", progresso=5)
            .execution_options(synchronize_session=False)
        ).rowcount
        db.commit()
        if pegou != 1:
            logger.info("Importação %s já processada ou inexistente: ignorando", importacao_id)
            return None

        imp = db.get(Importacao, importacao_id)
        usuario = db.get(Usuario, imp.usuario_id)
        token_ip = ip_requisicao.set(imp.ip)  # a auditoria feita aqui registra o IP de quem enviou
        try:
            _progresso(5, "Lendo a planilha")
            df = services.ler_planilha(imp.conteudo or b"", imp.arquivo_nome)
            _progresso(25, f"Validando {len(df):,} linhas".replace(",", "."))
            tarefas = services.validar_tarefas(df)

            _progresso(40, "Gravando o cronograma")
            projeto = services.criar_projeto_com_fases(db, usuario, imp.nome_projeto, imp.orcamento_aprovado, imp.data_inicio)
            db.flush()
            services.gravar_tarefas_em_lote(
                db, projeto, tarefas,
                ao_progredir=lambda feitas, total: _progresso(40 + int(55 * feitas / total), f"Gravando tarefas ({feitas:,} de {total:,})".replace(",", ".")),
            )
            auditoria.registrar(
                db, "projeto.importado", "projeto", projeto.id, usuario=usuario, projeto_id=projeto.id,
                detalhes={"arquivo": imp.arquivo_nome[:200], "tarefas": len(tarefas)},
            )
            imp.status, imp.progresso, imp.etapa = StatusImportacao.CONCLUIDA, 100, "Concluída"
            imp.projeto_id, imp.total_linhas, imp.conteudo, imp.concluida_em = projeto.id, len(tarefas), None, agora_utc()
            db.commit()  # projeto + tarefas + auditoria + status: tudo junto
            _progresso(100, "Concluída")
        except services.PlanilhaInvalida as exc:
            db.rollback()
            _falhar(db, importacao_id, exc.mensagem, exc.erros)
            return None
        except Exception:
            db.rollback()
            logger.exception("Falha ao processar a importação %s", importacao_id)
            _falhar(db, importacao_id, FALHA_INTERNA, [])
            return None
        finally:
            ip_requisicao.reset(token_ip)

        notificacoes.enviar_projeto_iniciado(usuario.email, usuario.nome, imp.nome_projeto)
        logger.info("Importação %s concluída: projeto %s com %s tarefas", importacao_id, projeto.id, len(tarefas))
        return projeto.id


def _falhar(db, importacao_id: int, mensagem: str, erros: list) -> None:
    imp = db.get(Importacao, importacao_id)
    imp.status, imp.etapa, imp.concluida_em = StatusImportacao.FALHOU, "Falhou", agora_utc()
    imp.mensagem = mensagem[:500]
    imp.erros = erros[:MAX_ERROS_GUARDADOS] + ([f"... e mais {len(erros) - MAX_ERROS_GUARDADOS} erro(s)."]
                                                if len(erros) > MAX_ERROS_GUARDADOS else [])
    imp.conteudo = None  # o arquivo não fica guardado depois de processado
    db.commit()
    _progresso(100, "Falhou")
