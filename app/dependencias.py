"""
Regras das dependências entre tarefas ("B depende de A").

- inicio_apos_fim: B não passa de 0% enquanto A não chegar a 100%.
- fim_apos_fim:    B não chega a 100% enquanto A não chegar a 100%.
- Reabrir A (sair de 100%) é recusado se a sucessora já avançou além do que a regra permite.
- Sem ciclos: A -> B -> C -> A tornaria as três impossíveis de começar.
- Reprogramar: empurra as datas das sucessoras para depois de A (em dias úteis), em cascata.
"""

from collections import defaultdict, deque
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

from sqlalchemy import or_, select
from sqlalchemy.orm import Session, aliased

from app.integracoes.feriados import MAX_DIAS_UTEIS, calcular_data_fim, eh_dia_util, proximo_dia_util
from app.models import DependenciaTarefa, Projeto, Tarefa, TipoDependencia

MAX_DEPENDENCIAS = 5_000  # por projeto
MAX_PASSOS_REPROGRAMACAO = 50_000


class RegraDependencia(Exception):
    """Mudança que quebraria uma dependência. A mensagem vai para a pessoa como está."""


def _nome(tarefa: Tarefa) -> str:
    texto = " ".join(tarefa.descricao.split())
    return f"“{texto if len(texto) <= 60 else texto[:59] + '…'}”"


def travar_projeto(db: Session, projeto: Projeto) -> None:
    """
    No Postgres, enfileira as mudanças de dependência do MESMO projeto (SELECT ... FOR UPDATE na linha
    do projeto): duas pessoas criando A->B e B->A ao mesmo tempo não fecham um ciclo. No SQLite, nada.
    """
    if db.get_bind().dialect.name == "postgresql":
        db.execute(select(Projeto.id).where(Projeto.id == projeto.id).with_for_update())


def violacao(dep_tipo: TipoDependencia, predecessora: Tarefa, sucessora: Tarefa,
             percentual_sucessora: Optional[int] = None) -> Optional[str]:
    """Por que o par fere a regra (ou None). `percentual_sucessora`: o valor que se quer gravar."""
    pct = sucessora.percentual_conclusao if percentual_sucessora is None else percentual_sucessora
    if predecessora.percentual_conclusao >= 100:
        return None
    situacao = f"está em {predecessora.percentual_conclusao}%"
    if dep_tipo == TipoDependencia.INICIO_APOS_FIM and pct > 0:
        return f"{_nome(sucessora)} só pode começar depois que {_nome(predecessora)} terminar ({situacao})."
    if dep_tipo == TipoDependencia.FIM_APOS_FIM and pct >= 100:
        return f"{_nome(sucessora)} só pode ser concluída depois que {_nome(predecessora)} terminar ({situacao})."
    return None


def conferir_avanco(db: Session, tarefa: Tarefa, novo: int) -> None:
    """Levanta RegraDependencia se mudar o percentual de `tarefa` para `novo` quebrar alguma dependência."""
    antigo = tarefa.percentual_conclusao
    if novo == antigo:
        return
    Pred = aliased(Tarefa)
    for dep, pred in db.execute(
        select(DependenciaTarefa, Pred).join(Pred, Pred.id == DependenciaTarefa.predecessora_id)
        .where(DependenciaTarefa.sucessora_id == tarefa.id).order_by(DependenciaTarefa.id)
    ).all():
        motivo = violacao(dep.tipo, pred, tarefa, novo)
        if motivo:
            raise RegraDependencia(motivo)

    if antigo >= 100 > novo:  # reabrindo: quem dependia do término dela já pode ter avançado
        Suc = aliased(Tarefa)
        for dep, suc in db.execute(
            select(DependenciaTarefa, Suc).join(Suc, Suc.id == DependenciaTarefa.sucessora_id)
            .where(DependenciaTarefa.predecessora_id == tarefa.id).order_by(DependenciaTarefa.id)
        ).all():
            if dep.tipo == TipoDependencia.INICIO_APOS_FIM and suc.percentual_conclusao > 0:
                raise RegraDependencia(
                    f"{_nome(tarefa)} não pode ser reaberta: {_nome(suc)} já começou e depende do término dela. "
                    f"Volte {_nome(suc)} para 0% ou remova a dependência."
                )
            if dep.tipo == TipoDependencia.FIM_APOS_FIM and suc.percentual_conclusao >= 100:
                raise RegraDependencia(
                    f"{_nome(tarefa)} não pode ser reaberta: {_nome(suc)} já foi concluída e depende do término dela. "
                    f"Reabra {_nome(suc)} antes ou remova a dependência."
                )


def tem_dependencias(db: Session, tarefa_id: int) -> bool:
    return db.scalar(
        select(DependenciaTarefa.id).where(
            or_(DependenciaTarefa.predecessora_id == tarefa_id, DependenciaTarefa.sucessora_id == tarefa_id)
        ).limit(1)
    ) is not None


def _grafo(db: Session, projeto_id: int) -> Dict[int, List[Tuple[int, TipoDependencia]]]:
    """predecessora -> [(sucessora, tipo)]: uma consulta só para o projeto inteiro."""
    saidas: Dict[int, List[Tuple[int, TipoDependencia]]] = defaultdict(list)
    for pred, suc, tipo in db.execute(
        select(DependenciaTarefa.predecessora_id, DependenciaTarefa.sucessora_id, DependenciaTarefa.tipo)
        .where(DependenciaTarefa.projeto_id == projeto_id)
    ):
        saidas[pred].append((suc, tipo))
    return saidas


def criaria_ciclo(db: Session, projeto_id: int, predecessora_id: int, sucessora_id: int) -> bool:
    """A -> B fecha um ciclo se já existe um caminho de B até A."""
    saidas = _grafo(db, projeto_id)
    vistos, fila = {sucessora_id}, deque([sucessora_id])
    while fila:
        atual = fila.popleft()
        if atual == predecessora_id:
            return True
        for proxima, _ in saidas.get(atual, ()):
            if proxima not in vistos:
                vistos.add(proxima)
                fila.append(proxima)
    return False


def dias_uteis(inicio: date, fim: date) -> int:
    """Dias úteis de inicio a fim (os dois inclusive); no mínimo 1."""
    total, dia = 0, inicio
    while dia <= fim and total < MAX_DIAS_UTEIS:
        total += eh_dia_util(dia)
        dia += timedelta(days=1)
    return max(total, 1)


def _empurrar(tipo: TipoDependencia, pred: Tarefa, suc: Tarefa) -> bool:
    """Ajusta as datas de `suc` para respeitar `pred`. True se mudou alguma coisa (só empurra para frente)."""
    if pred.data_fim is None:
        return False
    if tipo == TipoDependencia.INICIO_APOS_FIM:
        if suc.data_inicio is None:
            return False
        minimo = proximo_dia_util(pred.data_fim + timedelta(days=1))
        if suc.data_inicio >= minimo:
            return False
        duracao = dias_uteis(suc.data_inicio, suc.data_fim) if suc.data_fim else None
        suc.data_inicio = minimo
        if duracao is not None:
            suc.data_fim = calcular_data_fim(minimo, duracao)
        return True
    # fim_apos_fim
    if suc.data_fim is None or suc.data_fim >= pred.data_fim:
        return False
    suc.data_fim = pred.data_fim
    return True


def reprogramar(db: Session, projeto_id: int, a_partir_de: Optional[List[int]] = None) -> List[Tarefa]:
    """
    Empurra as datas das sucessoras (e das sucessoras delas) para depois das predecessoras.
    `a_partir_de`: ids das predecessoras por onde começar (None = o projeto inteiro).
    Devolve as tarefas que mudaram (a versão delas sobe no commit).
    """
    saidas = _grafo(db, projeto_id)
    fila = deque(a_partir_de if a_partir_de is not None else list(saidas))
    mudadas: Dict[int, Tarefa] = {}
    passos = 0
    while fila and passos < MAX_PASSOS_REPROGRAMACAO:
        pred_id = fila.popleft()
        pred = db.get(Tarefa, pred_id)
        for suc_id, tipo in saidas.get(pred_id, ()):
            passos += 1
            suc = db.get(Tarefa, suc_id)
            if pred is not None and suc is not None and _empurrar(tipo, pred, suc):
                mudadas[suc.id] = suc
                fila.append(suc.id)  # quem depende dela pode precisar andar também
    return list(mudadas.values())
