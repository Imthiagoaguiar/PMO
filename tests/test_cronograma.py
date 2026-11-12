from datetime import date, timedelta

import pytest

from app.models import PapelUsuario

GERENTE, OUTRO_GERENTE, MEMBRO = (
    "gerente@empresa.com.br",
    "outro@empresa.com.br",
    "membro@empresa.com.br",
)


@pytest.fixture
def projeto(client, criar_usuario, login):
    """Projeto do GERENTE, com MEMBRO adicionado. Retorna (dados_do_projeto, headers)."""
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    criar_usuario(OUTRO_GERENTE, PapelUsuario.GERENTE)
    membro = criar_usuario(MEMBRO)
    h = {"gerente": login(GERENTE), "outro": login(OUTRO_GERENTE), "membro": login(MEMBRO)}

    r = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h["gerente"])
    p = r.json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": membro.id}, headers=h["gerente"])
    return p, h


def _fase(p, titulo):
    return next(f["id"] for f in p["fases"] if f["titulo"] == titulo)


def _criar_tarefa(client, p, h, fase="Iniciação", **campos):
    body = {"descricao": "Tarefa", **campos}
    return client.post(f"/projetos/{p['id']}/fases/{_fase(p, fase)}/tarefas", json=body, headers=h)


# ---------- Tarefas ----------


def test_dono_cria_edita_move_e_exclui_tarefa(client, projeto):
    p, h = projeto
    r = _criar_tarefa(client, p, h["gerente"], descricao="  Termo de abertura ", responsavel="Ana")
    assert r.status_code == 201
    tarefa = r.json()
    assert tarefa["descricao"] == "Termo de abertura"  # espaços removidos
    url = f"/projetos/{p['id']}/tarefas/{tarefa['id']}"

    r = client.patch(url, json={"percentual_conclusao": 50, "fase_id": _fase(p, "Planejamento")}, headers=h["gerente"])
    assert r.status_code == 200
    assert r.json()["fase_id"] == _fase(p, "Planejamento")

    assert client.delete(url, headers=h["gerente"]).status_code == 204
    assert client.patch(url, json={"percentual_conclusao": 1}, headers=h["gerente"]).status_code == 404


def test_membro_so_atualiza_percentual(client, projeto):
    p, h = projeto
    tarefa = _criar_tarefa(client, p, h["gerente"]).json()
    url = f"/projetos/{p['id']}/tarefas/{tarefa['id']}"

    assert _criar_tarefa(client, p, h["membro"]).status_code == 403
    assert client.patch(url, json={"percentual_conclusao": 80}, headers=h["membro"]).status_code == 200
    assert client.patch(url, json={"descricao": "Hackeado"}, headers=h["membro"]).status_code == 403
    assert client.delete(url, headers=h["membro"]).status_code == 403


def test_nao_membro_nao_acessa_tarefas(client, projeto):
    p, h = projeto
    tarefa = _criar_tarefa(client, p, h["gerente"]).json()
    url = f"/projetos/{p['id']}/tarefas/{tarefa['id']}"
    assert client.patch(url, json={"percentual_conclusao": 10}, headers=h["outro"]).status_code == 404
    assert _criar_tarefa(client, p, h["outro"]).status_code == 404


def test_tarefa_de_outro_projeto_pela_url(client, projeto):
    """Trocar o projeto_id na URL não pode dar acesso a tarefa de outro projeto."""
    p, h = projeto
    tarefa = _criar_tarefa(client, p, h["gerente"]).json()
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=h["gerente"]).json()

    r = client.patch(f"/projetos/{outro['id']}/tarefas/{tarefa['id']}", json={"percentual_conclusao": 5}, headers=h["gerente"])
    assert r.status_code == 404
    # Também não move tarefa para a fase de outro projeto.
    r = client.patch(
        f"/projetos/{p['id']}/tarefas/{tarefa['id']}",
        json={"fase_id": outro["fases"][0]["id"]},
        headers=h["gerente"],
    )
    assert r.status_code == 404


def test_validacoes_de_tarefa(client, projeto):
    p, h = projeto
    assert _criar_tarefa(client, p, h["gerente"], descricao="   ").status_code == 422
    assert _criar_tarefa(client, p, h["gerente"], percentual_conclusao=101).status_code == 422
    assert _criar_tarefa(
        client, p, h["gerente"], data_inicio="2026-03-10", data_fim="2026-03-01"
    ).status_code == 422

    tarefa = _criar_tarefa(client, p, h["gerente"], data_inicio="2026-03-10").json()
    url = f"/projetos/{p['id']}/tarefas/{tarefa['id']}"
    # data_fim sozinha, mas anterior à data_inicio já gravada
    assert client.patch(url, json={"data_fim": "2026-03-01"}, headers=h["gerente"]).status_code == 422
    # null em campo obrigatório
    assert client.patch(url, json={"descricao": None}, headers=h["gerente"]).status_code == 422


# ---------- Projeto ----------


def test_editar_e_excluir_projeto(client, projeto):
    p, h = projeto
    url = f"/projetos/{p['id']}"

    r = client.patch(url, json={"status": "Em andamento", "orcamento_aprovado": "250000.00"}, headers=h["gerente"])
    assert r.status_code == 200
    assert r.json()["status"] == "Em andamento"

    assert client.patch(url, json={"status": "Inventado"}, headers=h["gerente"]).status_code == 422
    assert client.patch(url, json={"nome_projeto": None}, headers=h["gerente"]).status_code == 422
    assert client.patch(url, json={"status": "Cancelado"}, headers=h["membro"]).status_code == 403
    assert client.delete(url, headers=h["membro"]).status_code == 403

    _criar_tarefa(client, p, h["gerente"])
    assert client.delete(url, headers=h["gerente"]).status_code == 204
    assert client.get(url, headers=h["gerente"]).status_code == 404


# ---------- Indicadores ----------


def test_indicadores(client, projeto):
    p, h = projeto
    ontem = (date.today() - timedelta(days=1)).isoformat()
    _criar_tarefa(client, p, h["gerente"], "Iniciação", percentual_conclusao=100, data_inicio="2026-01-05", data_fim="2026-01-10")
    _criar_tarefa(client, p, h["gerente"], "Planejamento", percentual_conclusao=40, data_fim=ontem)  # atrasada
    _criar_tarefa(client, p, h["gerente"], "Planejamento", percentual_conclusao=0, data_fim="2099-12-31")

    r = client.get(f"/projetos/{p['id']}/indicadores", headers=h["membro"])
    assert r.status_code == 200
    ind = r.json()
    assert ind["total_tarefas"] == 3
    assert ind["tarefas_concluidas"] == 1
    assert ind["tarefas_atrasadas"] == 1
    assert ind["percentual_conclusao"] == pytest.approx(46.7)
    assert ind["fase_atual"] == "Planejamento"
    assert ind["data_inicio_prevista"] == "2026-01-05"
    assert ind["data_fim_prevista"] == "2099-12-31"
    assert [f["percentual_conclusao"] for f in ind["fases"]] == [100.0, 20.0, 0.0, 0.0, 0.0]

    assert client.get(f"/projetos/{p['id']}/indicadores", headers=h["outro"]).status_code == 404


def test_indicadores_projeto_vazio(client, projeto):
    p, h = projeto
    ind = client.get(f"/projetos/{p['id']}/indicadores", headers=h["gerente"]).json()
    assert ind["percentual_conclusao"] == 0.0
    assert ind["fase_atual"] is None
    assert len(ind["fases"]) == 5
