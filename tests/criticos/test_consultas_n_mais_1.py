"""
Consultas N+1 (SQLAlchemy): o número de queries de uma rota NÃO pode crescer com a quantidade de dados.

Conta os comandos SQL de verdade (evento `before_cursor_execute` do engine) e compara a mesma
rota com POUCOS e com MUITOS registros. Com N+1, 50 tarefas = 50 queries a mais.

Orçamento absoluto: além da conta de cada rota, há um teto por requisição. Ele inclui as
consultas fixas de qualquer rota autenticada (o usuário do token é lido do banco a cada
requisição, de propósito: desativar alguém corta o acesso na hora).
"""

from contextlib import contextmanager
from typing import Callable, Dict, List

import pytest
from sqlalchemy import event

from app.database import engine
from app.models import PapelUsuario

GERENTE = "gerente@empresa.com.br"
TETO_DE_QUERIES = 8


@contextmanager
def contar_queries():
    comandos: List[str] = []

    def _registrar(conn, cursor, sql, params, context, executemany):
        comandos.append(sql)

    event.listen(engine, "before_cursor_execute", _registrar)
    try:
        yield comandos
    finally:
        event.remove(engine, "before_cursor_execute", _registrar)


@pytest.fixture
def montar(client, criar_usuario, login, brasil_api) -> Callable[[int], Dict]:
    """Cria um projeto com `n` tarefas (cada uma com um responsável), `n` membros e `n` comentários."""
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    contador = {"projetos": 0}

    def _montar(n: int) -> Dict:
        contador["projetos"] += 1
        p = client.post("/projetos/manual", json={"nome_projeto": f"Projeto {contador['projetos']}"}, headers=h).json()
        pid = p["id"]
        fases = [f["id"] for f in p["fases"]]
        for i in range(n):
            pessoa = criar_usuario(f"p{contador['projetos']}-{i}@empresa.com.br")
            client.post(f"/projetos/{pid}/membros", json={"usuario_id": pessoa.id, "papel": "editor"}, headers=h)
            tarefa = client.post(f"/projetos/{pid}/fases/{fases[i % 5]}/tarefas", json={
                "descricao": f"Tarefa {i}", "responsavel_id": pessoa.id,
                "data_inicio": "2026-03-02", "data_fim": "2026-03-06", "percentual_conclusao": i % 101,
            }, headers=h).json()
            client.post(f"/projetos/{pid}/comentarios", json={"texto": f"c{i}", "tarefa_id": tarefa["id"]}, headers=h)
        return {"id": pid, "h": h}

    return _montar


ROTAS = [
    "/projetos/{id}",
    "/projetos/{id}/indicadores",
    "/projetos/{id}/exportar",
    "/projetos/{id}/membros",
    "/projetos/{id}/comentarios?limite=100",
    "/projetos/{id}/historico?limite=100",
    "/projetos?limite=100",
    "/tarefas/minhas?limite=100",
]


def _queries(client, projeto, rota) -> int:
    with contar_queries() as comandos:
        r = client.get(rota.format(id=projeto["id"]), headers=projeto["h"])
    assert r.status_code == 200, r.text
    return len(comandos)


@pytest.mark.parametrize("rota", ROTAS)
def test_queries_nao_crescem_com_a_quantidade_de_dados(client, montar, rota):
    pequeno, grande = montar(2), montar(50)
    poucas, muitas = _queries(client, pequeno, rota), _queries(client, grande, rota)
    assert muitas == poucas, f"{rota}: {poucas} queries com 2 itens, {muitas} com 50 (N+1)"
    assert muitas <= TETO_DE_QUERIES, f"{rota}: {muitas} queries (teto {TETO_DE_QUERIES})"


def test_detalhe_do_projeto_carrega_tudo_em_poucas_queries(client, montar):
    """GET /projetos/{id} com 50 tarefas: usuário + projeto + fases + tarefas + responsáveis + membros."""
    projeto = montar(50)
    with contar_queries() as comandos:
        r = client.get(f"/projetos/{projeto['id']}", headers=projeto["h"])
    assert sum(len(f["tarefas"]) for f in r.json()["fases"]) == 50
    assert len(comandos) <= 6, "\n\n".join(comandos)
