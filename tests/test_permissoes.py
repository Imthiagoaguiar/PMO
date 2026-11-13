"""Papéis no projeto (editor x visualizador) e comentários."""

import io

import pandas as pd
import pytest

from app.models import PapelUsuario

ADMIN, DONO, EDITOR, LEITOR, FORA = (
    "admin@empresa.com.br",
    "dono@empresa.com.br",
    "editor@empresa.com.br",
    "leitor@empresa.com.br",
    "fora@empresa.com.br",
)


@pytest.fixture
def cenario(client, criar_usuario, login):
    u = {
        "admin": criar_usuario(ADMIN, PapelUsuario.ADMIN),
        "dono": criar_usuario(DONO, PapelUsuario.GERENTE),
        "editor": criar_usuario(EDITOR),
        "leitor": criar_usuario(LEITOR),
        "fora": criar_usuario(FORA),
    }
    h = {k: login(e) for k, e in (("admin", ADMIN), ("dono", DONO), ("editor", EDITOR), ("leitor", LEITOR), ("fora", FORA))}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h["dono"]).json()
    url_membros = f"/projetos/{p['id']}/membros"
    client.post(url_membros, json={"usuario_id": u["editor"].id, "papel": "editor"}, headers=h["dono"])
    client.post(url_membros, json={"usuario_id": u["leitor"].id}, headers=h["dono"])  # padrão: visualizador
    tarefa = client.post(
        f"/projetos/{p['id']}/fases/{p['fases'][0]['id']}/tarefas", json={"descricao": "Kickoff"}, headers=h["dono"]
    ).json()
    return p, tarefa, u, h


# ---------- Papéis ----------


def test_permissoes_informadas_ao_front(client, cenario):
    p, _, _, h = cenario
    esperado = {
        "dono": {"editar": True, "gerenciar": True, "comentar": True},
        "admin": {"editar": True, "gerenciar": True, "comentar": True},  # admin sem ser membro
        "editor": {"editar": True, "gerenciar": False, "comentar": True},
        "leitor": {"editar": False, "gerenciar": False, "comentar": True},
    }
    for quem, permissoes in esperado.items():
        assert client.get(f"/projetos/{p['id']}", headers=h[quem]).json()["permissoes"] == permissoes, quem


def test_visualizador_ve_e_extrai_mas_nao_altera(client, cenario):
    p, tarefa, _, h = cenario
    base, url_t = f"/projetos/{p['id']}", f"/projetos/{p['id']}/tarefas/{tarefa['id']}"
    leitor = h["leitor"]

    # Vê e extrai dados.
    assert client.get(base, headers=leitor).status_code == 200
    assert client.get(f"{base}/indicadores", headers=leitor).status_code == 200
    assert client.get(f"{base}/historico", headers=leitor).status_code == 200
    assert client.get(f"{base}/membros", headers=leitor).status_code == 200
    exportado = client.get(f"{base}/exportar", headers=leitor)
    assert exportado.status_code == 200
    assert pd.read_excel(io.BytesIO(exportado.content)).loc[0, "Descrição"] == "Kickoff"

    # Não altera nada.
    assert client.patch(url_t, json={"percentual_conclusao": 50}, headers=leitor).status_code == 403
    assert client.post(f"{base}/fases/{p['fases'][0]['id']}/tarefas", json={"descricao": "X"}, headers=leitor).status_code == 403
    assert client.delete(url_t, headers=leitor).status_code == 403
    assert client.patch(base, json={"nome_projeto": "Outro nome"}, headers=leitor).status_code == 403
    assert client.patch(base, json={"status": "Cancelado"}, headers=leitor).status_code == 403

    r = client.patch(url_t, json={"percentual_conclusao": 50}, headers=leitor)
    assert r.json()["detail"] == "Você tem acesso apenas de visualização a este projeto."


def test_editor_edita_projeto_e_cronograma_mas_nao_gerencia(client, cenario):
    p, tarefa, u, h = cenario
    base, url_t = f"/projetos/{p['id']}", f"/projetos/{p['id']}/tarefas/{tarefa['id']}"
    editor = h["editor"]

    assert client.patch(url_t, json={"percentual_conclusao": 50, "descricao": "Kickoff v2"}, headers=editor).status_code == 200
    assert client.post(f"{base}/fases/{p['fases'][1]['id']}/tarefas", json={"descricao": "EAP"}, headers=editor).status_code == 201
    assert client.patch(base, json={"nome_projeto": "ERP 2.0", "status": "Em andamento"}, headers=editor).status_code == 200

    # Gerenciar continua só com dono/admin.
    assert client.post(f"{base}/membros", json={"usuario_id": u["fora"].id}, headers=editor).status_code == 403
    assert client.patch(f"{base}/membros/{u['leitor'].id}", json={"papel": "editor"}, headers=editor).status_code == 403
    assert client.delete(base, headers=editor).status_code == 403


def test_dono_promove_e_rebaixa_membros(client, cenario):
    p, tarefa, u, h = cenario
    base, url_t = f"/projetos/{p['id']}", f"/projetos/{p['id']}/tarefas/{tarefa['id']}"

    membros = client.get(f"{base}/membros", headers=h["leitor"]).json()
    assert [(m["email"], m["papel"], m["dono"]) for m in membros] == [
        (DONO, "editor", True), ("editor@empresa.com.br", "editor", False), (LEITOR, "visualizador", False),
    ]

    r = client.patch(f"{base}/membros/{u['leitor'].id}", json={"papel": "editor"}, headers=h["dono"])
    assert r.status_code == 200
    assert client.patch(url_t, json={"percentual_conclusao": 30}, headers=h["leitor"]).status_code == 200

    client.patch(f"{base}/membros/{u['leitor'].id}", json={"papel": "visualizador"}, headers=h["admin"])
    assert client.patch(url_t, json={"percentual_conclusao": 40}, headers=h["leitor"]).status_code == 403

    historico = client.get(f"{base}/historico?limite=100", headers=h["dono"]).json()["itens"]
    alteracoes = [e["detalhes"]["papel"] for e in historico if e["acao"] == "membro.papel_alterado"]
    assert alteracoes == [["editor", "visualizador"], ["visualizador", "editor"]]

    # O papel do dono não muda; papel inexistente é recusado; quem não é membro dá 404.
    assert client.patch(f"{base}/membros/{u['dono'].id}", json={"papel": "visualizador"}, headers=h["dono"]).status_code == 400
    assert client.patch(f"{base}/membros/{u['leitor'].id}", json={"papel": "dono"}, headers=h["dono"]).status_code == 422
    assert client.patch(f"{base}/membros/{u['fora'].id}", json={"papel": "editor"}, headers=h["dono"]).status_code == 404


# ---------- Comentários ----------


def test_visualizador_comenta_no_projeto_e_na_tarefa(client, cenario):
    p, tarefa, _, h = cenario
    url = f"/projetos/{p['id']}/comentarios"

    r = client.post(url, json={"texto": "  Precisamos validar o escopo  "}, headers=h["leitor"])
    assert r.status_code == 201
    c = r.json()
    assert (c["texto"], c["tarefa_id"], c["autor"]["email"], c["editado_em"]) == (
        "Precisamos validar o escopo", None, LEITOR, None)

    assert client.post(url, json={"texto": "Kickoff marcado", "tarefa_id": tarefa["id"]}, headers=h["editor"]).status_code == 201

    todos = client.get(url, headers=h["dono"]).json()
    assert todos["total"] == 2
    assert todos["itens"][0]["texto"] == "Kickoff marcado"  # mais recente primeiro
    assert client.get(f"{url}?tarefa_id={tarefa['id']}", headers=h["dono"]).json()["total"] == 1
    assert client.get(f"{url}?somente_projeto=true", headers=h["dono"]).json()["total"] == 1

    assert client.post(url, json={"texto": "   "}, headers=h["leitor"]).status_code == 422
    assert client.post(url, json={"texto": "x" * 2001}, headers=h["leitor"]).status_code == 422


def test_comentario_em_tarefa_de_outro_projeto_e_quem_nao_e_membro(client, cenario):
    p, _, _, h = cenario
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=h["dono"]).json()
    tarefa_outro = client.post(
        f"/projetos/{outro['id']}/fases/{outro['fases'][0]['id']}/tarefas", json={"descricao": "T"}, headers=h["dono"]
    ).json()

    url = f"/projetos/{p['id']}/comentarios"
    assert client.post(url, json={"texto": "Oi", "tarefa_id": tarefa_outro["id"]}, headers=h["dono"]).status_code == 404
    assert client.get(url, headers=h["fora"]).status_code == 404
    assert client.post(url, json={"texto": "Oi"}, headers=h["fora"]).status_code == 404


def test_editar_e_excluir_comentario(client, cenario):
    p, _, _, h = cenario
    url = f"/projetos/{p['id']}/comentarios"
    c = client.post(url, json={"texto": "Versão 1"}, headers=h["leitor"]).json()
    url_c = f"{url}/{c['id']}"

    r = client.patch(url_c, json={"texto": "Versão 2"}, headers=h["leitor"])
    assert r.status_code == 200
    assert r.json()["texto"] == "Versão 2" and r.json()["editado_em"] is not None

    # Ninguém além do autor reescreve o texto, nem o admin.
    for quem in ("editor", "dono", "admin"):
        assert client.patch(url_c, json={"texto": "Adulterado"}, headers=h[quem]).status_code == 403, quem
    # Excluir: o autor ou a moderação (dono/admin); editor comum não.
    assert client.delete(url_c, headers=h["editor"]).status_code == 403
    assert client.delete(url_c, headers=h["dono"]).status_code == 204
    assert client.get(url, headers=h["leitor"]).json()["total"] == 0

    proprio = client.post(url, json={"texto": "Meu"}, headers=h["editor"]).json()
    assert client.delete(f"{url}/{proprio['id']}", headers=h["editor"]).status_code == 204

    acoes = [e["acao"] for e in client.get(f"/projetos/{p['id']}/historico", headers=h["dono"]).json()["itens"]]
    assert acoes[:5] == ["comentario.excluido", "comentario.criado", "comentario.excluido",
                         "comentario.editado", "comentario.criado"]


def test_projeto_encerrado_continua_aceitando_comentarios(client, cenario):
    p, _, _, h = cenario
    client.patch(f"/projetos/{p['id']}", json={"status": "Concluído"}, headers=h["dono"])
    r = client.post(f"/projetos/{p['id']}/comentarios", json={"texto": "Lição aprendida: ..."}, headers=h["leitor"])
    assert r.status_code == 201


def test_excluir_tarefa_ou_projeto_apaga_os_comentarios(client, cenario):
    p, tarefa, _, h = cenario
    url = f"/projetos/{p['id']}/comentarios"
    client.post(url, json={"texto": "Na tarefa", "tarefa_id": tarefa["id"]}, headers=h["leitor"])
    client.post(url, json={"texto": "No projeto"}, headers=h["leitor"])

    assert client.delete(f"/projetos/{p['id']}/tarefas/{tarefa['id']}", headers=h["dono"]).status_code == 204
    restantes = client.get(url, headers=h["dono"]).json()
    assert [c["texto"] for c in restantes["itens"]] == ["No projeto"]

    assert client.delete(f"/projetos/{p['id']}", headers=h["dono"]).status_code == 204


def test_diretorio_de_usuarios_para_adicionar_membros(client, cenario, criar_usuario):
    _, _, _, h = cenario
    criar_usuario("inativo.leitor@empresa.com.br", ativo=False)

    r = client.get("/usuarios/diretorio?busca=leitor", headers=h["dono"])  # gerente
    assert r.status_code == 200
    assert r.json() == [{"id": r.json()[0]["id"], "nome": "leitor", "email": LEITOR}]  # sem inativos, sem papel

    assert client.get("/usuarios/diretorio?busca=leitor", headers=h["leitor"]).status_code == 403  # membro
    assert client.get("/usuarios/diretorio?busca=l", headers=h["dono"]).status_code == 422  # mínimo 2 letras
    assert client.get("/usuarios/diretorio?busca=%25", headers=h["admin"]).status_code == 422
