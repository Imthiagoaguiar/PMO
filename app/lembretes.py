"""
Lembrete por e-mail de tarefas atrasadas.

    python -m app.lembretes            # fica rodando: confere a cada LEMBRETES_VERIFICAR_MINUTOS (15)
    python -m app.lembretes --uma-vez  # uma conferência agora e sai

Regras (combinadas com o usuário em 20/11/2026):
- UM e-mail por pessoa, com TODAS as tarefas atrasadas dela (prazo vencido e abaixo de 100%), em
  projetos abertos dos quais ela ainda é membro;
- no máximo a cada LEMBRETES_INTERVALO_HORAS (4), só em dias úteis (sem feriados nacionais) entre
  LEMBRETES_HORA_INICIO e LEMBRETES_HORA_FIM (8h e 18h, no FUSO_HORARIO) — até 3 por dia;
- quem silenciou (menu do usuário) não recebe até a hora escolhida;
- vários processos rodando ao mesmo tempo não mandam em dobro: cada envio "reserva" a pessoa com um
  UPDATE condicional (só um consegue).
"""

import argparse
import logging
import signal
import sys
import threading
from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app import registro, tempo
from app.config import settings
from app.database import SessionLocal
from app.integracoes import notificacoes
from app.integracoes.feriados import eh_dia_util
from app.models import Fase, Projeto, ProjetoMembro, StatusProjeto, Tarefa, Usuario

logger = logging.getLogger("pmo.lembretes")
ENCERRADOS = (StatusProjeto.CONCLUIDO, StatusProjeto.CANCELADO)


def em_horario(agora_utc: datetime) -> bool:
    local = agora_utc.astimezone(ZoneInfo(settings.fuso_horario))
    return eh_dia_util(local.date()) and settings.lembretes_hora_inicio <= local.hour < settings.lembretes_hora_fim


def atrasadas_por_pessoa(db: Session, hoje: date) -> Dict[int, List[Tuple[Tarefa, Projeto]]]:
    """{usuario_id: [(tarefa, projeto)]} em uma consulta: só membros atuais, ativos, de projetos abertos."""
    linhas = db.execute(
        select(Tarefa, Projeto)
        .join(Fase, Fase.id == Tarefa.fase_id)
        .join(Projeto, Projeto.id == Fase.projeto_id)
        .join(ProjetoMembro, and_(ProjetoMembro.projeto_id == Projeto.id, ProjetoMembro.usuario_id == Tarefa.responsavel_id))
        .join(Usuario, Usuario.id == Tarefa.responsavel_id)
        .where(
            Tarefa.data_fim < hoje, Tarefa.percentual_conclusao < 100,
            Projeto.status.not_in(ENCERRADOS), Usuario.ativo.is_(True),
        )
        .order_by(Tarefa.data_fim, Tarefa.id)
    ).all()
    por_pessoa: Dict[int, List[Tuple[Tarefa, Projeto]]] = defaultdict(list)
    for tarefa, projeto in linhas:
        por_pessoa[tarefa.responsavel_id].append((tarefa, projeto))
    return por_pessoa


def _reservar(db: Session, usuario_id: int, agora: datetime) -> Tuple[bool, Optional[datetime]]:
    """
    Marca o envio para esta pessoa SE ela não recebeu nas últimas N horas e não está silenciada.
    (reservou?, valor anterior — para desfazer se o e-mail falhar). Duas rodadas juntas: só uma reserva.
    """
    anterior = db.scalar(select(Usuario.lembrete_enviado_em).where(Usuario.id == usuario_id))
    janela = agora - timedelta(hours=settings.lembretes_intervalo_horas)
    feito = db.execute(
        update(Usuario)
        .where(
            Usuario.id == usuario_id,
            or_(Usuario.lembrete_enviado_em.is_(None), Usuario.lembrete_enviado_em <= janela),
            or_(Usuario.lembretes_silenciados_ate.is_(None), Usuario.lembretes_silenciados_ate <= agora),
        )
        .values(lembrete_enviado_em=agora)
        .execution_options(synchronize_session=False)
    ).rowcount
    db.commit()
    return feito == 1, anterior


def executar(agora: Optional[datetime] = None, ignorar_horario: bool = False) -> int:
    """Uma conferência. Devolve quantos e-mails foram enviados."""
    agora = agora or tempo.agora_utc()
    if not ignorar_horario and not em_horario(agora):
        logger.info("Lembretes: fora do horário comercial; nada a enviar")
        return 0
    hoje = agora.astimezone(ZoneInfo(settings.fuso_horario)).date()
    enviados = 0
    with SessionLocal() as db:
        por_pessoa = atrasadas_por_pessoa(db, hoje)
        for usuario_id, itens in por_pessoa.items():
            reservou, anterior = _reservar(db, usuario_id, agora)
            if not reservou:
                continue
            usuario = db.get(Usuario, usuario_id)
            lista = [
                {"tarefa": t.descricao, "projeto": p.nome_projeto, "projeto_id": p.id, "prazo": t.data_fim,
                 "dias": (hoje - t.data_fim).days, "percentual": t.percentual_conclusao}
                for t, p in itens
            ]
            if notificacoes.enviar_lembrete_atrasadas(usuario.email, usuario.nome, lista):
                enviados += 1
            else:
                # Não saiu (Resend fora, sem chave...): desfaz a reserva para tentar na próxima conferência.
                db.execute(update(Usuario).where(Usuario.id == usuario_id, Usuario.lembrete_enviado_em == agora)
                           .values(lembrete_enviado_em=anterior)
                           .execution_options(synchronize_session=False))
                db.commit()
    logger.info("Lembretes: %s e-mail(s) enviado(s); %s pessoa(s) com tarefa atrasada", enviados, len(por_pessoa),
                extra={"tipo": "lembretes", "enviados": enviados, "pessoas": len(por_pessoa)})
    return enviados


def main(argv: Optional[list] = None) -> int:
    argumentos = argparse.ArgumentParser(description="Lembretes de tarefas atrasadas por e-mail")
    argumentos.add_argument("--uma-vez", action="store_true", help="confere agora e sai")
    argumentos.add_argument("--ignorar-horario", action="store_true", help="envia mesmo fora do horário (teste)")
    args = argumentos.parse_args(argv)
    registro.configurar(None)
    if args.uma_vez:
        executar(ignorar_horario=args.ignorar_horario)
        return 0
    parar = threading.Event()
    for sinal in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sinal, lambda *_: parar.set())
    logger.info("Lembretes ligados: conferindo a cada %s min", settings.lembretes_verificar_minutos)
    def _conferir(agora: datetime) -> None:
        try:
            executar(agora)
        except Exception:
            logger.exception("Lembretes: falha na conferência (tenta de novo na próxima)")

    tempo.repetir_pelo_relogio(parar, timedelta(minutes=settings.lembretes_verificar_minutos), _conferir)
    logger.info("Lembretes desligados")
    return 0


if __name__ == "__main__":
    sys.exit(main())
