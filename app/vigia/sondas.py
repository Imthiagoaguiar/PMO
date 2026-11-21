"""
Sondas do sistema rodando. Todas SÓ LEEM: o vigia nunca altera dados.
- saúde da API (HTTP), migração do banco em dia, integridade dos dados,
- segurança na janela (auditoria), higiene (tokens vencidos, admins sem 2FA), disco e log.
"""

import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from app.config import settings
from app.models import MAX_FASES, MAX_PILARES, Auditoria, Importacao, PapelUsuario, ProvedorAuth, RefreshToken, StatusImportacao, Usuario

RAIZ = Path(__file__).resolve().parents[2]


def saude_api(url: str) -> Dict[str, Any]:
    inicio = time.perf_counter()
    try:
        resposta = httpx.get(url.rstrip("/") + "/saude", timeout=10)
        corpo = resposta.json() if resposta.headers.get("content-type", "").startswith("application/json") else {}
        return {"ok": resposta.status_code == 200, "status": resposta.status_code, "corpo": corpo,
                "ms": round((time.perf_counter() - inicio) * 1000, 1)}
    except (httpx.HTTPError, ValueError) as exc:
        return {"ok": False, "status": None, "erro": type(exc).__name__, "ms": round((time.perf_counter() - inicio) * 1000, 1)}


def migracao(db: Session) -> Dict[str, Any]:
    """Revisão aplicada no banco x a mais nova do código: diferença = colunas faltando = erro 500 em produção."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    try:
        cabeca = ScriptDirectory.from_config(Config(str(RAIZ / "alembic.ini"))).get_current_head()
        atual = db.execute(text("SELECT version_num FROM alembic_version")).scalar()
    except Exception as exc:  # tabela inexistente, alembic.ini fora do lugar...
        db.rollback()  # no Postgres, um SELECT que falha aborta a transação inteira
        return {"em_dia": None, "erro": type(exc).__name__}
    return {"em_dia": atual == cabeca, "banco": atual, "codigo": cabeca}


# (código, descrição, SQL que devolve os ids problemáticos). Cada uma é uma regra que a API
# deveria garantir: achar algo aqui = bug ou alteração manual no banco.
VERIFICACOES_DE_INTEGRIDADE = [
    # As fases são editáveis (20/11/2026): um projeto pode ter de 0 a MAX_FASES, numeradas 1..N sem buracos.
    ("fases_demais_ou_fora_de_ordem", f"Projetos com mais de {MAX_FASES} fases ou com a ordem das fases quebrada",
     "SELECT projeto_id FROM fases GROUP BY projeto_id "
     "HAVING COUNT(*) > :max_fases OR MIN(ordem_sequencia) <> 1 OR MAX(ordem_sequencia) <> COUNT(*)"),
    ("dependencia_entre_projetos", "Dependências entre tarefas de projetos diferentes",
     "SELECT d.id FROM dependencias_tarefas d JOIN tarefas a ON a.id = d.predecessora_id JOIN fases fa ON fa.id = a.fase_id "
     "JOIN tarefas b ON b.id = d.sucessora_id JOIN fases fb ON fb.id = b.fase_id "
     "WHERE fa.projeto_id <> d.projeto_id OR fb.projeto_id <> d.projeto_id"),
    ("anexo_de_tarefa_de_outro_projeto", "Anexos ligados a uma tarefa de OUTRO projeto",
     "SELECT x.id FROM anexos_tarefas x JOIN tarefas t ON t.id = x.tarefa_id JOIN fases f ON f.id = t.fase_id "
     "WHERE f.projeto_id <> x.projeto_id"),
    ("lancamento_de_item_de_outro_projeto", "Lançamentos do orçamento ligados a um item de OUTRO projeto",
     "SELECT l.id FROM lancamentos_orcamento l JOIN itens_orcamento i ON i.id = l.item_id WHERE i.projeto_id <> l.projeto_id"),
    ("tarefa_com_pilar_de_outro_projeto", "Tarefas ligadas a um pilar de OUTRO projeto",
     "SELECT t.id FROM tarefas t JOIN fases f ON f.id = t.fase_id JOIN pilares pi ON pi.id = t.pilar_id "
     "WHERE pi.projeto_id <> f.projeto_id"),
    ("responsavel_fora_do_projeto", "Tarefas cujo responsável não é membro do projeto",
     "SELECT t.id FROM tarefas t JOIN fases f ON f.id = t.fase_id WHERE t.responsavel_id IS NOT NULL AND NOT EXISTS "
     "(SELECT 1 FROM projeto_membros m WHERE m.projeto_id = f.projeto_id AND m.usuario_id = t.responsavel_id)"),
    ("dono_fora_dos_membros", "Projetos cujo dono não está na lista de membros",
     "SELECT p.id FROM projetos p WHERE NOT EXISTS "
     "(SELECT 1 FROM projeto_membros m WHERE m.projeto_id = p.id AND m.usuario_id = p.dono_id)"),
    ("pilares_demais", f"Projetos com mais de {MAX_PILARES} pilares",
     "SELECT projeto_id FROM pilares GROUP BY projeto_id HAVING COUNT(*) > :maximo"),
    ("datas_invertidas", "Tarefas com término antes do início",
     "SELECT id FROM tarefas WHERE data_inicio IS NOT NULL AND data_fim IS NOT NULL AND data_fim < data_inicio"),
    ("percentual_fora_da_faixa", "Tarefas com % fora de 0 a 100",
     "SELECT id FROM tarefas WHERE percentual_conclusao < 0 OR percentual_conclusao > 100"),
]


def integridade(db: Session) -> List[Dict[str, Any]]:
    problemas = []
    for codigo, descricao, sql in VERIFICACOES_DE_INTEGRIDADE:
        try:
            ids = [linha[0] for linha in db.execute(text(sql + " LIMIT 51"), {"maximo": MAX_PILARES, "max_fases": MAX_FASES})]
        except Exception as exc:
            db.rollback()
            problemas.append({"codigo": codigo, "descricao": descricao, "erro": type(exc).__name__})
            continue
        if ids:
            problemas.append({"codigo": codigo, "descricao": descricao, "quantidade": len(ids) if len(ids) <= 50 else "50+",
                              "exemplos": ids[:5]})
    return problemas


ACOES_DE_SEGURANCA = ("auth.login_falhou", "auth.conta_bloqueada", "auth.login_barrado", "auth.reuso_refresh_token",
                      "auth.2fa_falhou", "usuario.2fa_redefinido", "usuario.senha_redefinida")


def seguranca(db: Session, desde: datetime) -> Dict[str, Any]:
    contagens = dict(db.execute(
        select(Auditoria.acao, func.count()).where(Auditoria.criado_em >= desde, Auditoria.acao.in_(ACOES_DE_SEGURANCA))
        .group_by(Auditoria.acao)
    ).all())
    ips_com_falha = db.scalar(
        select(func.count(func.distinct(Auditoria.ip))).where(Auditoria.criado_em >= desde, Auditoria.acao == "auth.login_falhou")
    ) or 0
    return {"eventos": contagens, "ips_distintos_com_falha": ips_com_falha}


def higiene(db: Session, agora: datetime) -> Dict[str, Any]:
    tokens_velhos = db.scalar(select(func.count()).select_from(RefreshToken).where(
        (RefreshToken.expira_em < agora - timedelta(days=1)) | (RefreshToken.revogado_em < agora - timedelta(days=1))
    )) or 0
    admins_sem_2fa = 0
    if settings.exigir_2fa_admin:
        admins_sem_2fa = db.scalar(select(func.count()).select_from(Usuario).where(
            Usuario.papel == PapelUsuario.ADMIN, Usuario.ativo.is_(True),
            Usuario.provedor_auth == ProvedorAuth.LOCAL, Usuario.totp_ativo.is_(False),
        )) or 0
    return {"refresh_tokens_para_limpar": tokens_velhos, "admins_sem_2fa": admins_sem_2fa}


def recursos(arquivo_log: Optional[str]) -> Dict[str, Any]:
    pasta = Path(arquivo_log).parent if arquivo_log else RAIZ
    pasta = pasta if pasta.exists() else RAIZ
    uso = shutil.disk_usage(pasta)
    tamanho_log = Path(arquivo_log).stat().st_size if arquivo_log and Path(arquivo_log).exists() else None
    return {"disco_livre_percentual": round(uso.free / uso.total * 100, 1), "disco_livre_gb": round(uso.free / 1e9, 1),
            "log_bytes": tamanho_log}


PARADA_NA_FILA = timedelta(minutes=10)
TRAVADA_PROCESSANDO = timedelta(minutes=35)  # o worker mata o job em 30 min


def fila_de_importacao(db: Session, agora: datetime, desde: datetime) -> Dict[str, Any]:
    """Redis no ar? Algum worker? Importação esperando demais ou travada no meio?"""
    from rq import Worker

    from app import fila
    from app.importacao import FALHA_INTERNA

    estado: Dict[str, Any] = {"redis_ok": False, "na_fila_no_redis": None, "workers": None}
    try:
        conexao = fila.conexao()
        conexao.ping()
        estado.update(redis_ok=True, na_fila_no_redis=fila.fila().count, workers=len(Worker.all(connection=conexao)))
    except Exception as exc:  # Redis fora do ar / URL errada
        estado["erro"] = type(exc).__name__

    def _contar(*filtros: Any) -> int:
        return db.scalar(select(func.count()).select_from(Importacao).where(*filtros)) or 0

    estado.update(
        paradas_na_fila=_contar(Importacao.status == StatusImportacao.NA_FILA, Importacao.criada_em < agora - PARADA_NA_FILA),
        travadas_processando=_contar(Importacao.status == StatusImportacao.PROCESSANDO,
                                     Importacao.iniciada_em < agora - TRAVADA_PROCESSANDO),
        concluidas_na_janela=_contar(Importacao.status == StatusImportacao.CONCLUIDA, Importacao.concluida_em >= desde),
        falhas_internas_na_janela=_contar(Importacao.status == StatusImportacao.FALHOU, Importacao.concluida_em >= desde,
                                          Importacao.mensagem == FALHA_INTERNA),
    )
    return estado


def backup() -> Dict[str, Any]:
    """Idade do último backup (o vigia cobra um por dia)."""
    from app import backup as modulo_backup

    if not settings.backup_dir:
        return {"configurado": False}
    pasta = Path(settings.backup_dir)
    backups = modulo_backup.listar(pasta)
    idade = modulo_backup.idade_do_ultimo(pasta)
    return {
        "configurado": True, "quantidade": len(backups),
        "ultimo": backups[-1].name if backups else None,
        "horas_desde_o_ultimo": round(idade.total_seconds() / 3600, 1) if idade else None,
        "copia_externa": bool(settings.backup_copia_dir),
    }
