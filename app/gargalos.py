"""
Gargalos do projeto: qual fase, pilar (área) e pessoa acumulam mais atraso.

Atraso de uma tarefa (dias corridos):
- entregue (100%) depois do prazo: data de conclusão - prazo;
- ainda aberta e com o prazo vencido: hoje - prazo (o atraso continua crescendo).
Concluídas sem data de conclusão conhecida (ex.: importadas já prontas) ficam fora das contas.
Uma consulta só de colunas: serve para projetos de 100 mil tarefas.
"""

from collections import defaultdict
from datetime import date
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Fase, Pilar, Projeto, Tarefa, Usuario
from app.schemas import Gargalos, GrupoGargalo, TarefaGargalo

SEM_PILAR, SEM_RESPONSAVEL = "Sem pilar", "Sem responsável"


class _Conta:
    def __init__(self) -> None:
        self.com_prazo = self.no_prazo = self.entregues_atrasadas = self.abertas_atrasadas = 0
        self.dias_entregues = self.dias_abertas = self.pior = 0

    def somar(self, situacao: str, dias: int) -> None:
        self.com_prazo += 1
        if situacao == "no_prazo":
            self.no_prazo += 1
        elif situacao == "entregue_atrasada":
            self.entregues_atrasadas += 1
            self.dias_entregues += dias
        elif situacao == "aberta_atrasada":
            self.abertas_atrasadas += 1
            self.dias_abertas += dias
        self.pior = max(self.pior, dias)

    def ler(self, nome: str) -> GrupoGargalo:
        atrasadas = self.entregues_atrasadas + self.abertas_atrasadas
        entregues = self.no_prazo + self.entregues_atrasadas
        return GrupoGargalo(
            nome=nome, tarefas_com_prazo=self.com_prazo, entregues_no_prazo=self.no_prazo,
            entregues_atrasadas=self.entregues_atrasadas, abertas_atrasadas=self.abertas_atrasadas,
            dias_atraso_entregues=self.dias_entregues, dias_atraso_abertas=self.dias_abertas,
            atraso_medio=round((self.dias_entregues + self.dias_abertas) / atrasadas, 1) if atrasadas else None,
            pior_atraso=self.pior,
            pontualidade=round(self.no_prazo / entregues * 100, 1) if entregues else None,
        )


def _ordenar(contas: Dict[str, _Conta]) -> List[GrupoGargalo]:
    grupos = [c.ler(nome) for nome, c in contas.items()]
    return sorted(grupos, key=lambda g: (-(g.dias_atraso_entregues + g.dias_atraso_abertas), -g.abertas_atrasadas, g.nome))


def calcular(db: Session, projeto: Projeto, hoje: date) -> Gargalos:
    fuso = ZoneInfo(settings.fuso_horario)
    linhas = db.execute(
        select(Tarefa.id, Tarefa.descricao, Tarefa.data_fim, Tarefa.percentual_conclusao, Tarefa.concluida_em,
               Fase.titulo, Pilar.nome, func.coalesce(Usuario.nome, Tarefa.responsavel))
        .join(Fase, Fase.id == Tarefa.fase_id)
        .outerjoin(Pilar, Pilar.id == Tarefa.pilar_id)
        .outerjoin(Usuario, Usuario.id == Tarefa.responsavel_id)
        .where(Fase.projeto_id == projeto.id, Tarefa.data_fim.is_not(None))
    ).all()

    por: Dict[str, Dict[str, _Conta]] = {"fase": defaultdict(_Conta), "pilar": defaultdict(_Conta), "pessoa": defaultdict(_Conta)}
    total = _Conta()
    sem_data = 0
    piores: List[Tuple[int, TarefaGargalo]] = []
    for tid, descricao, prazo, pct, concluida_em, fase, pilar, responsavel in linhas:
        situacao: Optional[str]
        if pct >= 100:
            if concluida_em is None:
                sem_data += 1
                continue
            entregue = concluida_em.astimezone(fuso).date() if concluida_em.tzinfo else concluida_em.date()
            dias = max((entregue - prazo).days, 0)
            situacao = "entregue_atrasada" if dias > 0 else "no_prazo"
        elif prazo < hoje:
            dias, situacao = (hoje - prazo).days, "aberta_atrasada"
        else:
            dias, situacao = 0, "em_dia"
        for chave, nome in (("fase", fase), ("pilar", pilar or SEM_PILAR), ("pessoa", responsavel or SEM_RESPONSAVEL)):
            por[chave][nome].somar(situacao, dias)
        total.somar(situacao, dias)
        if dias > 0:
            piores.append((dias, TarefaGargalo(
                tarefa_id=tid, descricao=descricao, fase=fase, pilar=pilar, responsavel=responsavel, prazo=prazo,
                dias_atraso=dias, situacao=situacao)))

    resumo = total.ler("Projeto")
    return Gargalos(
        dias_atraso_total=resumo.dias_atraso_entregues + resumo.dias_atraso_abertas,
        pontualidade=resumo.pontualidade, entregues_sem_data=sem_data,
        por_fase=_ordenar(por["fase"]), por_pilar=_ordenar(por["pilar"]), por_responsavel=_ordenar(por["pessoa"]),
        piores=[t for _, t in sorted(piores, key=lambda x: (-x[0], x[1].tarefa_id))[:10]],
    )
