"""
Caractere NUL (\\x00): o Postgres não grava NUL em texto e o driver levanta um ValueError que
virava erro 500 em qualquer rota. O SQLite aceita, por isso o problema só aparecia em produção.

Regra: texto vindo da API com NUL é recusado com 422 (em qualquer banco); célula de planilha
com NUL é limpa (planilhas exportadas de outros sistemas às vezes trazem esse lixo).
"""

import pytest

from tests.criticos.conftest import contar
from app.models import Comentario, Projeto

NUL_JSON = "texto\u0000escondido"


@pytest.fixture
def projeto(client, gerente):
    return client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=gerente).json()


@pytest.mark.parametrize("rota, corpo", [
    ("/projetos/manual", {"nome_projeto": NUL_JSON}),
    ("/projetos/manual", {"nome_projeto": "ERP", "fases": [{"fase": "Iniciação", "tarefas": [{"descricao": NUL_JSON}]}]}),
    ("/projetos/{id}/comentarios", {"texto": NUL_JSON}),
    ("/projetos/{id}/pilares", {"nome": NUL_JSON}),
    ("/projetos/ia", {"texto": NUL_JSON * 20}),
])
def test_json_com_nul_e_recusado(client, gerente, projeto, db, rota, corpo):
    antes = (contar(db, Projeto), contar(db, Comentario))
    r = client.post(rota.format(id=projeto["id"]), json=corpo, headers=gerente)
    assert r.status_code == 422, r.text
    assert "caractere inválido" in r.json()["detail"]
    assert (contar(db, Projeto), contar(db, Comentario)) == antes


def test_patch_com_nul_e_recusado(client, gerente, projeto):
    r = client.patch(f"/projetos/{projeto['id']}", json={"nome_projeto": NUL_JSON}, headers=gerente)
    assert r.status_code == 422


def test_chave_do_json_com_nul(client, gerente):
    r = client.post("/projetos/manual", json={"nome_projeto": "ERP", "x\u0000": 1}, headers=gerente)
    assert r.status_code == 422


@pytest.mark.parametrize("url", [
    "/projetos?busca=a%00b",
    "/usuarios/diretorio?busca=ab%00",
    "/tarefas/minhas?pendentes=true%00",
])
def test_query_string_com_nul(client, gerente, url):
    assert client.get(url, headers=gerente).status_code == 422


def test_login_com_nul_no_formulario(client, gerente):
    r = client.post("/auth/login", data={"username": "gerente@empresa.com.br\x00", "password": "x" * 12})
    assert r.status_code == 422


def test_texto_normal_com_barra_u_continua_aceito(client, gerente, projeto):
    """Quem escreve literalmente "\\u0000" (com a barra) não é confundido com o caractere NUL."""
    r = client.post(f"/projetos/{projeto['id']}/comentarios", json={"texto": "o escape \\u0000 em JSON"}, headers=gerente)
    assert r.status_code == 201
    assert r.json()["texto"] == "o escape \\u0000 em JSON"


def test_planilha_com_nul_nas_celulas_e_limpa(importar, db):
    r = importar("Fase;Descrição;Responsável\nIniciação;Le\x00vantar;Jo\x00ão\n".encode(), "plano\x00.csv")
    assert r.status_code == 201, r.text
    tarefa = r.json()["projeto"]["fases"][0]["tarefas"][0]
    assert (tarefa["descricao"], tarefa["responsavel"]) == ("Levantar", "João")


def test_nome_do_projeto_no_formulario_da_importacao(importar):
    r = importar("Fase;Descrição\nIniciação;TAP\n".encode(), nome_projeto="Projeto\x00X")
    assert r.status_code == 422


def test_json_invalido_continua_com_a_mensagem_do_fastapi(client, gerente):
    r = client.post("/projetos/manual", content=b'{"nome_projeto": ', headers={**gerente, "Content-Type": "application/json"})
    assert r.status_code == 422
