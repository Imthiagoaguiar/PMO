"""
Curva S do projeto: planejado x realizado ao longo do tempo, física (% do cronograma) e financeira (R$).

Física
- Planejado na data d: cada tarefa com início e término "deveria" estar em (d - início + 1) / duração
  (de 0 a 100%); o projeto é a média ponderada pela DURAÇÃO (tarefa de 30 dias pesa 30x uma de 1 dia).
- Realizado na data d: o % que cada tarefa TINHA naquele dia, reconstruído pelo histórico (auditoria:
  cada mudança de percentual fica registrada com data). Antes de a tarefa existir, 0%.
- IDP (índice de desempenho de prazo) hoje = realizado / planejado: abaixo de 1 = atrasado.

Financeira (só com itens no orçamento)
- Planejado: o total orçado distribuído pela MESMA curva física planejada (gasta-se conforme o trabalho
  andaria) — por isso o formato em S, e não uma reta.
- Realizado: soma dos lançamentos até a data.

Contas vetorizadas (numpy): projetos de 100 mil tarefas cabem em poucos décimos de segundo.
"""

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Auditoria, Fase, ItemOrcamento, LancamentoOrcamento, Projeto, Tarefa
from app.schemas import CurvaS, PontoCurvaS

MAX_PONTOS = 160
CENTAVO = Decimal("0.01")


def _passo(inicio: date, fim: date) -> int:
    """Semanal; em projetos longos, o passo cresce para caber em MAX_PONTOS."""
    dias = max((fim - inicio).days, 1)
    return max(7, -(-dias // MAX_PONTOS))


def _datas(inicio: date, fim: date, hoje: date) -> List[date]:
    passo = _passo(inicio, fim)
    datas, d = [], inicio
    while d < fim:
        datas.append(d)
        d += timedelta(days=passo)
    datas.append(fim)
    if inicio <= hoje <= fim and hoje not in datas:
        datas.append(hoje)
    return sorted(set(datas))


def _historico(db: Session, projeto: Projeto, fuso: ZoneInfo) -> Tuple[Dict[int, date], Dict[int, List[Tuple[date, int, int]]], Optional[date]]:
    """(criação de cada tarefa, mudanças de % [(dia, antes, depois)], 1º registro do projeto)."""
    criada: Dict[int, date] = {}
    mudancas: Dict[int, List[Tuple[date, int, int]]] = defaultdict(list)
    for tarefa_id, quando, detalhes, acao in db.execute(
        select(Auditoria.entidade_id, Auditoria.criado_em, Auditoria.detalhes, Auditoria.acao)
        .where(Auditoria.projeto_id == projeto.id, Auditoria.entidade == "tarefa",
               Auditoria.acao.in_(("tarefa.criada", "tarefa.atualizada")))
        .order_by(Auditoria.criado_em, Auditoria.id)
    ):
        dia = (quando if quando.tzinfo else quando.replace(tzinfo=ZoneInfo("UTC"))).astimezone(fuso).date()
        if acao == "tarefa.criada":
            criada.setdefault(tarefa_id, dia)
            continue
        valor = (detalhes or {}).get("percentual_conclusao") if isinstance(detalhes, dict) else None
        if isinstance(valor, list) and len(valor) == 2 and all(isinstance(v, int) for v in valor):
            mudancas[tarefa_id].append((dia, valor[0], valor[1]))
    primeiro = db.scalar(select(func.min(Auditoria.criado_em)).where(Auditoria.projeto_id == projeto.id))
    if primeiro is not None:
        primeiro = (primeiro if primeiro.tzinfo else primeiro.replace(tzinfo=ZoneInfo("UTC"))).astimezone(fuso).date()
    return criada, mudancas, primeiro


def calcular(db: Session, projeto: Projeto, hoje: date) -> CurvaS:
    fuso = ZoneInfo(settings.fuso_horario)
    linhas = db.execute(
        select(Tarefa.id, Tarefa.data_inicio, Tarefa.data_fim, Tarefa.percentual_conclusao)
        .join(Fase, Fase.id == Tarefa.fase_id)
        .where(Fase.projeto_id == projeto.id, Tarefa.data_inicio.is_not(None), Tarefa.data_fim.is_not(None))
        .order_by(Tarefa.id)
    ).all()
    sem_datas = db.scalar(
        select(func.count()).select_from(Tarefa).join(Fase, Fase.id == Tarefa.fase_id)
        .where(Fase.projeto_id == projeto.id, (Tarefa.data_inicio.is_(None)) | (Tarefa.data_fim.is_(None)))
    ) or 0
    total_orcado = Decimal(db.scalar(
        select(func.coalesce(func.sum(ItemOrcamento.valor_orcado), 0)).where(ItemOrcamento.projeto_id == projeto.id)
    ) or 0)
    if not linhas:
        return CurvaS(pontos=[], tarefas_sem_datas=sem_datas, com_orcamento=total_orcado > 0)

    ids = [t for t, _, _, _ in linhas]
    base = min(t[1] for t in linhas)
    ini = np.array([(t[1] - base).days for t in linhas], dtype=np.int64)
    fim = np.array([(t[2] - base).days for t in linhas], dtype=np.int64)
    peso = (fim - ini + 1).astype(np.float64)
    total_peso = peso.sum()

    criada, mudancas, primeiro = _historico(db, projeto, fuso)
    padrao = primeiro or base
    # Sem mudanças registradas: o % atual vale desde que a tarefa existe (criação ou 1º registro do projeto).
    atual = np.array([t[3] for t in linhas], dtype=np.float64)
    desde = np.array([(criada.get(t, padrao) - base).days for t in ids], dtype=np.int64)
    inicial = atual.copy()
    com_historico = []
    for i, tid in enumerate(ids):
        if tid in mudancas:
            inicial[i] = mudancas[tid][0][1]
            com_historico.append(i)

    inicio = min(base, projeto.data_inicio or base)
    final = max(max(t[2] for t in linhas), min(hoje, max(t[2] for t in linhas) + timedelta(days=365)))
    datas = _datas(inicio, final, hoje)

    lancamentos = sorted(db.execute(
        select(LancamentoOrcamento.data, func.sum(LancamentoOrcamento.valor))
        .where(LancamentoOrcamento.projeto_id == projeto.id, LancamentoOrcamento.projetado.is_(False))
        .group_by(LancamentoOrcamento.data)
    ).all())

    pontos: List[PontoCurvaS] = []
    for d in datas:
        x = (d - base).days
        planejado = float(np.clip((x - ini + 1) / peso, 0, 1).dot(peso) / total_peso * 100)
        realizado = None
        if d <= hoje:
            pct = np.where(x >= desde, inicial, 0.0)
            for i in com_historico:  # poucas: só as tarefas cujo % mudou pela tela
                valor = inicial[i] if x >= desde[i] else 0.0
                for dia, _, depois in mudancas[ids[i]]:
                    if dia <= d:
                        valor = depois
                pct[i] = valor
            realizado = round(float(pct.dot(peso) / total_peso), 1)
        planejado_rs = realizado_rs = None
        if total_orcado > 0:
            planejado_rs = (total_orcado * Decimal(repr(planejado)) / 100).quantize(CENTAVO)  # arredonda só os reais
            if d <= hoje:
                realizado_rs = sum((Decimal(v) for dia, v in lancamentos if dia <= d), Decimal("0"))
        pontos.append(PontoCurvaS(data=d, planejado_pct=round(planejado, 1), realizado_pct=realizado,
                                  planejado_rs=planejado_rs, realizado_rs=realizado_rs))

    agora = next((p for p in reversed(pontos) if p.realizado_pct is not None), None)
    idp = round(agora.realizado_pct / agora.planejado_pct, 2) if agora and agora.planejado_pct else None
    return CurvaS(
        pontos=pontos, tarefas_sem_datas=sem_datas, com_orcamento=total_orcado > 0,
        planejado_hoje=agora.planejado_pct if agora else None, realizado_hoje=agora.realizado_pct if agora else None,
        desvio_pp=round(agora.realizado_pct - agora.planejado_pct, 1) if agora else None, idp=idp,
        planejado_rs_hoje=agora.planejado_rs if agora else None, realizado_rs_hoje=agora.realizado_rs if agora else None,
    )
