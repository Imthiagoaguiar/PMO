"""Testes de: projeto encerrado, responsável/minhas tarefas, auditoria, rate limit, paginação, exportação."""

import io

import pandas as pd
import pytest

from app.models import PapelUsuario
from app.rate_limit import limite_login
from tests.conftest import SENHA

ADMIN, GERENTE, MEMBRO, FORA = (
    "admin@empresa.com.br",
    "gerente@empresa.com.br",
    "membro@empresa.com.br",
    "fora@empresa.com.br",
)


@pytest.fixture
def cenario(client, criar_usuario, login):
    u = {
        "admin": criar_usuario(ADMIN, PapelUsuario.ADMIN),
        "gerente": criar_usuario(GERENTE, PapelUsuario.GERENTE),
        "membro": criar_usuario(MEMBRO),
        "fora": criar_usuario(FORA),
    }
    h = {k: login(e) for k, e in (("admin", ADMIN), ("gerente", GERENTE), ("membro", MEMBRO), ("fora", FORA))}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h["gerente"]).json()
    # Neste arquivo o "membro" é editor: os testes exercitam edição por quem não é dono.
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": u["membro"].id, "papel": "editor"},
                headers=h["gerente"])
    return p, u, h


def _tarefa(client, p, h, **campos):
    fase_id = p["fases"][0]["id"]
    r = client.post(f"/projetos/{p['id']}/fases/{fase_id}/tarefas", json={"descricao": "T", **campos}, headers=h)
    return r


# ---------- Projeto encerrado ----------


def test_projeto_encerrado_fica_somente_leitura(client, cenario):
    p, _, h = cenario
    tarefa = _tarefa(client, p, h["gerente"]).json()
    url_p, url_t = f"/projetos/{p['id']}", f"/projetos/{p['id']}/tarefas/{tarefa['id']}"

    assert client.patch(url_p, json={"status": "Concluído"}, headers=h["gerente"]).status_code == 200
    assert _tarefa(client, p, h["gerente"]).status_code == 409
    assert client.patch(url_t, json={"percentual_conclusao": 90}, headers=h["membro"]).status_code == 409
    assert client.delete(url_t, headers=h["gerente"]).status_code == 409
    assert client.patch(url_p, json={"nome_projeto": "Novo nome"}, headers=h["gerente"]).status_code == 409

    # Reabrir (só status) é permitido; depois volta a aceitar edição.
    assert client.patch(url_p, json={"status": "Em andamento"}, headers=h["gerente"]).status_code == 200
    assert _tarefa(client, p, h["gerente"]).status_code == 201


# ---------- Responsável e minhas tarefas ----------


def test_responsavel_precisa_ser_membro(client, cenario):
    p, u, h = cenario
    assert _tarefa(client, p, h["gerente"], responsavel_id=u["fora"].id).status_code == 422
    assert _tarefa(client, p, h["gerente"], responsavel_id=99999).status_code == 422
    assert _tarefa(client, p, h["gerente"], responsavel_id=u["membro"].id).status_code == 201


def test_minhas_tarefas(client, cenario):
    p, u, h = cenario
    _tarefa(client, p, h["gerente"], descricao="Depois", responsavel_id=u["membro"].id, data_fim="2099-01-01")
    _tarefa(client, p, h["gerente"], descricao="Atrasada", responsavel_id=u["membro"].id, data_fim="2020-01-01")
    _tarefa(client, p, h["gerente"], descricao="Feita", responsavel_id=u["membro"].id, percentual_conclusao=100)
    _tarefa(client, p, h["gerente"], descricao="De outro")

    r = client.get("/tarefas/minhas", headers=h["membro"]).json()
    assert r["total"] == 2
    assert [t["descricao"] for t in r["itens"]] == ["Atrasada", "Depois"]  # ordenado por prazo
    assert r["itens"][0]["atrasada"] is True
    assert r["itens"][0]["nome_projeto"] == "ERP"
    assert r["itens"][0]["fase_titulo"] == "Iniciação"

    assert client.get("/tarefas/minhas?pendentes=false", headers=h["membro"]).json()["total"] == 3
    assert client.get("/tarefas/minhas", headers=h["fora"]).json()["total"] == 0


def test_remover_membro_desatribui_tarefas(client, cenario):
    p, u, h = cenario
    tarefa = _tarefa(client, p, h["gerente"], responsavel_id=u["membro"].id).json()
    client.delete(f"/projetos/{p['id']}/membros/{u['membro'].id}", headers=h["gerente"])

    detalhe = client.get(f"/projetos/{p['id']}", headers=h["gerente"]).json()
    tarefas = [t for f in detalhe["fases"] for t in f["tarefas"]]
    assert tarefas[0]["id"] == tarefa["id"]
    assert tarefas[0]["responsavel_id"] is None


# ---------- Auditoria ----------


def test_historico_do_projeto(client, cenario):
    p, _, h = cenario
    tarefa = _tarefa(client, p, h["gerente"], descricao="Kickoff").json()
    client.patch(f"/projetos/{p['id']}/tarefas/{tarefa['id']}", json={"percentual_conclusao": 30}, headers=h["membro"])

    r = client.get(f"/projetos/{p['id']}/historico", headers=h["membro"])
    assert r.status_code == 200
    eventos = r.json()["itens"]
    assert [e["acao"] for e in eventos] == ["tarefa.atualizada", "tarefa.criada", "membro.adicionado", "projeto.criado"]
    assert eventos[0]["detalhes"] == {"percentual_conclusao": [0, 30]}
    assert eventos[0]["ip"] == "testclient"

    assert client.get(f"/projetos/{p['id']}/historico", headers=h["fora"]).status_code == 404


def test_auditoria_global_so_admin_e_sobrevive_a_exclusao(client, cenario):
    p, _, h = cenario
    client.post("/auth/login", data={"username": GERENTE, "password": "senha-errada!!"})
    client.delete(f"/projetos/{p['id']}", headers=h["gerente"])

    assert client.get("/auditoria", headers=h["gerente"]).status_code == 403

    falhas = client.get("/auditoria?acao=auth.login_falhou", headers=h["admin"]).json()
    assert falhas["total"] == 1
    assert falhas["itens"][0]["detalhes"] == {"email": GERENTE}

    do_projeto = client.get(f"/auditoria?projeto_id={p['id']}", headers=h["admin"]).json()
    assert do_projeto["itens"][0]["acao"] == "projeto.excluido"


# ---------- Rate limit ----------


def test_rate_limit_no_login(client, criar_usuario):
    """Teto de requisições de login por IP (contra sobrecarga de CPU com o Argon2)."""
    criar_usuario(MEMBRO)
    for _ in range(limite_login.max_requisicoes):
        client.post("/auth/login", data={"username": MEMBRO, "password": SENHA})  # até logins certos contam
    r = client.post("/auth/login", data={"username": MEMBRO, "password": SENHA})
    assert r.status_code == 429
    assert int(r.headers["retry-after"]) > 0


# ---------- Paginação e filtros ----------


def test_paginacao_e_busca_de_projetos(client, cenario):
    _, _, h = cenario
    for nome in ("Portal RH", "Portal Vendas", "Migração 100%"):
        client.post("/projetos/manual", json={"nome_projeto": nome}, headers=h["gerente"])

    r = client.get("/projetos?limite=2&offset=0", headers=h["gerente"]).json()
    assert (len(r["itens"]), r["total"]) == (2, 4)
    r2 = client.get("/projetos?limite=2&offset=2", headers=h["gerente"]).json()
    assert {p["id"] for p in r["itens"]}.isdisjoint({p["id"] for p in r2["itens"]})

    assert client.get("/projetos?busca=portal", headers=h["gerente"]).json()["total"] == 2
    # "%" digitado é literal, não curinga que traria todos os projetos.
    assert client.get("/projetos?busca=%25", headers=h["gerente"]).json()["total"] == 1
    assert client.get("/projetos?status=Cancelado", headers=h["gerente"]).json()["total"] == 0
    assert client.get("/projetos?limite=500", headers=h["gerente"]).status_code == 422


def test_listagem_de_usuarios_com_filtros(client, cenario):
    _, _, h = cenario
    assert client.get("/usuarios", headers=h["admin"]).json()["total"] == 4
    assert client.get("/usuarios?papel=gerente", headers=h["admin"]).json()["total"] == 1
    assert client.get("/usuarios?busca=fora", headers=h["admin"]).json()["itens"][0]["email"] == FORA


# ---------- Exportação ----------


def test_exportar_e_reimportar(client, cenario):
    p, u, h = cenario
    _tarefa(client, p, h["gerente"], descricao="=HYPERLINK(\"http://malicioso\")", responsavel_id=u["membro"].id,
            data_inicio="2026-03-01", data_fim="2026-03-10", percentual_conclusao=40)
    _tarefa(client, p, h["gerente"], descricao="Normal", responsavel="Fornecedor X")

    r = client.get(f"/projetos/{p['id']}/exportar", headers=h["membro"])
    assert r.status_code == 200
    assert r.headers["content-disposition"] == 'attachment; filename="ERP.xlsx"'

    df = pd.read_excel(io.BytesIO(r.content))
    assert list(df.columns) == ["Fase", "Descrição", "Data Início", "Data Fim", "Responsável", "% Conclusão"]
    assert df.loc[0, "Descrição"].startswith("'=")  # fórmula neutralizada
    assert df.loc[0, "Responsável"] == "membro"
    assert df.loc[1, "Responsável"] == "Fornecedor X"

    # O arquivo exportado é aceito de volta pela importação.
    r = client.post(
        "/projetos/importar", data={"nome_projeto": "Cópia do ERP"},
        files={"arquivo": ("erp.xlsx", r.content)}, headers=h["gerente"],
    )
    assert r.status_code == 201, r.text
    assert r.json()["total_tarefas_importadas"] == 2

    assert client.get(f"/projetos/{p['id']}/exportar", headers=h["fora"]).status_code == 404


# ---------- Infra ----------


def test_health_check(client):
    assert client.get("/saude").json() == {"status": "ok", "banco": "ok"}
