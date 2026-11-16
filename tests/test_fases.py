"""
Fases editáveis: o Waterfall é só o modelo inicial. O editor cria, renomeia, reordena e exclui fases,
e escolhe se o projeto segue a ordem delas (fase atual) ou não.
"""

import io

import openpyxl
import pytest

from app.database import SessionLocal
from app.integracoes import feriados
from app.models import FASES_WATERFALL, MAX_FASES, PapelUsuario, Tarefa
from tests.conftest import postar_importacao

GERENTE, EDITORA, LEITOR = "gerente@empresa.com.br", "editora@empresa.com.br", "leitor@empresa.com.br"


@pytest.fixture(autouse=True)
def sem_internet(monkeypatch):
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())


@pytest.fixture
def cenario(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    editora, leitor = criar_usuario(EDITORA), criar_usuario(LEITOR)
    h = {"gerente": login(GERENTE), "editora": login(EDITORA), "leitor": login(LEITOR)}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": editora.id, "papel": "editor"}, headers=h["gerente"])
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    return {"h": h, "id": p["id"], "fases": {f["titulo"]: f["id"] for f in p["fases"]}}


def _fases(client, c, quem="leitor"):
    return [(f["titulo"], f["ordem_sequencia"]) for f in client.get(f"/projetos/{c['id']}", headers=c["h"][quem]).json()["fases"]]


def _url(c, sufixo=""):
    return f"/projetos/{c['id']}/fases{sufixo}"


def _tarefa(client, c, fase_id, descricao="T", **dados):
    r = client.post(f"/projetos/{c['id']}/fases/{fase_id}/tarefas", json={"descricao": descricao, **dados},
                    headers=c["h"]["gerente"])
    assert r.status_code == 201, r.text
    return r.json()


# ---------- Modelo inicial ----------


def test_projeto_nasce_com_o_waterfall_por_padrao(client, cenario):
    assert _fases(client, cenario) == [(t, i) for i, t in enumerate(FASES_WATERFALL, start=1)]


def test_projeto_em_branco_nasce_sem_fases_ou_so_com_as_escolhidas(client, cenario):
    h = cenario["h"]["gerente"]
    vazio = client.post("/projetos/manual", json={"nome_projeto": "Livre", "modelo_fases": "em_branco"}, headers=h).json()
    assert vazio["fases"] == []
    proprio = client.post("/projetos/manual", json={
        "nome_projeto": "Ágil", "modelo_fases": "em_branco", "data_inicio": "2026-03-02",
        "fases": [{"fase": "Sprint 1", "tarefas": [{"descricao": "A", "duracao_dias_uteis": 2}]},
                  {"fase": "  Sprint   2 ", "tarefas": [{"descricao": "B", "duracao_dias_uteis": 1}]}],
    }, headers=h).json()
    assert [f["titulo"] for f in proprio["fases"]] == ["Sprint 1", "Sprint 2"]
    b = proprio["fases"][1]["tarefas"][0]
    assert b["data_inicio"] == "2026-03-04"  # encadeada depois das 2 da Sprint 1


def test_waterfall_com_fase_propria_na_criacao(client, cenario):
    p = client.post("/projetos/manual", json={
        "nome_projeto": "Misto", "fases": [{"fase": "execucao", "tarefas": [{"descricao": "X"}]},
                                           {"fase": "Pós-implantação", "tarefas": [{"descricao": "Y"}]}],
    }, headers=cenario["h"]["gerente"]).json()
    assert [f["titulo"] for f in p["fases"]] == [*FASES_WATERFALL, "Pós-implantação"]
    assert p["fases"][2]["tarefas"][0]["descricao"] == "X"  # "execucao" caiu em "Execução"


def test_nomes_repetidos_na_criacao_sao_recusados(client, cenario):
    r = client.post("/projetos/manual", json={"nome_projeto": "Dup", "modelo_fases": "em_branco",
                                              "fases": [{"fase": "Teste"}, {"fase": "TESTE "}]}, headers=cenario["h"]["gerente"])
    assert r.status_code == 422


# ---------- Criar, renomear, reordenar ----------


def test_criar_fase_no_fim_e_numa_posicao(client, cenario):
    h = cenario["h"]["editora"]
    r = client.post(_url(cenario), json={"titulo": "Pós-implantação"}, headers=h)
    assert r.status_code == 201 and r.json()["ordem_sequencia"] == 6
    r = client.post(_url(cenario), json={"titulo": "Pré-entrega", "posicao": 3}, headers=h)
    assert r.status_code == 201
    assert [t for t, _ in _fases(client, cenario)] == [
        "Iniciação", "Planejamento", "Pré-entrega", "Execução", "Monitoramento", "Encerramento", "Pós-implantação"]
    assert [o for _, o in _fases(client, cenario)] == list(range(1, 8))


@pytest.mark.parametrize("nome", ["execução", "EXECUCAO", "  Execução "])
def test_nome_repetido_sem_acento_ou_caixa_e_recusado(client, cenario, nome):
    r = client.post(_url(cenario), json={"titulo": nome}, headers=cenario["h"]["gerente"])
    assert r.status_code == 409 and "Já existe" in r.json()["detail"]


def test_limite_de_fases(client, cenario):
    h = cenario["h"]["gerente"]
    for i in range(MAX_FASES - len(FASES_WATERFALL)):
        assert client.post(_url(cenario), json={"titulo": f"Extra {i}"}, headers=h).status_code == 201
    r = client.post(_url(cenario), json={"titulo": "Uma a mais"}, headers=h)
    assert r.status_code == 422 and str(MAX_FASES) in r.json()["detail"]


def test_renomear_fase(client, cenario):
    fase = cenario["fases"]["Monitoramento"]
    h = cenario["h"]["editora"]
    r = client.patch(_url(cenario, f"/{fase}"), json={"titulo": "Controle e qualidade"}, headers=h)
    assert r.status_code == 200 and r.json()["titulo"] == "Controle e qualidade"
    assert client.patch(_url(cenario, f"/{fase}"), json={"titulo": "planejamento"}, headers=h).status_code == 409
    # Renomear para o próprio nome (outra grafia) é permitido.
    assert client.patch(_url(cenario, f"/{fase}"), json={"titulo": "CONTROLE E QUALIDADE"}, headers=h).status_code == 200
    acoes = [e["acao"] for e in client.get(f"/projetos/{cenario['id']}/historico", headers=h).json()["itens"]]
    assert "fase.renomeada" in acoes


def test_reordenar_fases(client, cenario):
    ids = list(cenario["fases"].values())
    nova = [ids[4], ids[0], ids[1], ids[2], ids[3]]
    r = client.put(_url(cenario, "/ordem"), json={"ids": nova}, headers=cenario["h"]["editora"])
    assert r.status_code == 200
    assert [t for t, _ in _fases(client, cenario)] == ["Encerramento", "Iniciação", "Planejamento", "Execução", "Monitoramento"]


@pytest.mark.parametrize("ids", [[1], "faltando", "repetido"])
def test_reordenar_exige_todas_as_fases_uma_vez(client, cenario, ids):
    todas = list(cenario["fases"].values())
    corpo = {"faltando": todas[:-1], "repetido": [todas[0], *todas[:-1]], }.get(ids, ids) if isinstance(ids, str) else ids
    r = client.put(_url(cenario, "/ordem"), json={"ids": corpo}, headers=cenario["h"]["gerente"])
    assert r.status_code == 422


def test_leitor_nao_altera_fases(client, cenario):
    h = cenario["h"]["leitor"]
    fase = cenario["fases"]["Execução"]
    assert client.post(_url(cenario), json={"titulo": "X"}, headers=h).status_code == 403
    assert client.patch(_url(cenario, f"/{fase}"), json={"titulo": "X"}, headers=h).status_code == 403
    assert client.put(_url(cenario, "/ordem"), json={"ids": list(cenario["fases"].values())}, headers=h).status_code == 403
    assert client.delete(_url(cenario, f"/{fase}"), headers=h).status_code == 403


def test_projeto_encerrado_nao_altera_fases(client, cenario):
    h = cenario["h"]["gerente"]
    v = client.get(f"/projetos/{cenario['id']}", headers=h).json()["versao"]
    client.patch(f"/projetos/{cenario['id']}", json={"status": "Concluído", "versao": v}, headers=h)
    assert client.post(_url(cenario), json={"titulo": "X"}, headers=h).status_code == 409


# ---------- Excluir ----------


def test_excluir_fase_vazia(client, cenario):
    r = client.delete(_url(cenario, f"/{cenario['fases']['Monitoramento']}"), headers=cenario["h"]["editora"])
    assert r.status_code == 204
    assert _fases(client, cenario) == [("Iniciação", 1), ("Planejamento", 2), ("Execução", 3), ("Encerramento", 4)]


def test_fase_com_tarefas_exige_escolher_o_destino(client, cenario):
    execucao = cenario["fases"]["Execução"]
    _tarefa(client, cenario, execucao, "A")
    _tarefa(client, cenario, execucao, "B")
    r = client.delete(_url(cenario, f"/{execucao}"), headers=cenario["h"]["gerente"])
    assert r.status_code == 409 and "2 tarefa(s)" in r.json()["detail"]
    assert ("Execução", 3) in _fases(client, cenario)  # nada mudou


def test_excluir_movendo_as_tarefas(client, cenario):
    execucao, monitoramento = cenario["fases"]["Execução"], cenario["fases"]["Monitoramento"]
    t = _tarefa(client, cenario, execucao, "Mover")
    r = client.delete(_url(cenario, f"/{execucao}?mover_para={monitoramento}"), headers=cenario["h"]["gerente"])
    assert r.status_code == 204
    detalhe = client.get(f"/projetos/{cenario['id']}", headers=cenario["h"]["leitor"]).json()
    movida = next(x for f in detalhe["fases"] for x in f["tarefas"] if x["id"] == t["id"])
    assert movida["fase_id"] == monitoramento and movida["versao"] == t["versao"] + 1
    # Quem estava com a versão antiga aberta não devolve a tarefa para a fase excluída.
    r = client.patch(f"/projetos/{cenario['id']}/tarefas/{t['id']}", json={"descricao": "x", "versao": t["versao"]},
                     headers=cenario["h"]["gerente"])
    assert r.status_code == 409


def test_excluir_junto_com_as_tarefas(client, cenario):
    execucao = cenario["fases"]["Execução"]
    t = _tarefa(client, cenario, execucao, "Some")
    assert client.delete(_url(cenario, f"/{execucao}?excluir_tarefas=true"), headers=cenario["h"]["gerente"]).status_code == 204
    with SessionLocal() as db:
        assert db.get(Tarefa, t["id"]) is None


def test_escolhas_invalidas_na_exclusao(client, cenario, criar_usuario, login):
    execucao = cenario["fases"]["Execução"]
    h = cenario["h"]["gerente"]
    _tarefa(client, cenario, execucao)
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=h).json()
    de_outro_projeto = outro["fases"][0]["id"]
    assert client.delete(_url(cenario, f"/{execucao}?mover_para={execucao}"), headers=h).status_code == 422
    assert client.delete(_url(cenario, f"/{execucao}?mover_para={de_outro_projeto}"), headers=h).status_code == 404
    monit = cenario["fases"]["Monitoramento"]
    assert client.delete(_url(cenario, f"/{execucao}?mover_para={monit}&excluir_tarefas=true"), headers=h).status_code == 422


def test_criar_tarefa_em_fase_nova_e_excluir_todas_as_fases(client, cenario):
    h = cenario["h"]["gerente"]
    nova = client.post(_url(cenario), json={"titulo": "Homologação"}, headers=h).json()
    _tarefa(client, cenario, nova["id"], "Homologar")
    for fase_id in cenario["fases"].values():
        assert client.delete(_url(cenario, f"/{fase_id}"), headers=h).status_code == 204
    assert _fases(client, cenario) == [("Homologação", 1)]


# ---------- Seguir a ordem ----------


def test_seguir_ordem_das_fases(client, cenario):
    h = cenario["h"]["gerente"]
    _tarefa(client, cenario, cenario["fases"]["Planejamento"])
    url = f"/projetos/{cenario['id']}"
    assert client.get(f"{url}/indicadores", headers=h).json()["fase_atual"] == "Planejamento"
    v = client.get(url, headers=h).json()["versao"]
    r = client.patch(url, json={"seguir_ordem_fases": False, "versao": v}, headers=h)
    assert r.status_code == 200 and r.json()["seguir_ordem_fases"] is False
    assert client.get(f"{url}/indicadores", headers=h).json()["fase_atual"] is None
    assert client.patch(url, json={"seguir_ordem_fases": None}, headers=h).status_code == 422


# ---------- Planilha ----------


def test_importacao_com_fases_proprias(client, cenario):
    csv = ("Fase;Descrição\nExecução;A\nHomologação;B\nexecucao;C\nTreinamento;D\nHOMOLOGAÇÃO;E\n").encode()
    r = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Plan"},
                          files={"arquivo": ("p.csv", csv)}, headers=cenario["h"]["gerente"])
    assert r.status_code == 201, r.text
    fases = r.json()["projeto"]["fases"]
    assert [f["titulo"] for f in fases] == [*FASES_WATERFALL, "Homologação", "Treinamento"]
    por_fase = {f["titulo"]: [t["descricao"] for t in f["tarefas"]] for f in fases}
    assert por_fase["Execução"] == ["A", "C"] and por_fase["Homologação"] == ["B", "E"]


def test_importacao_com_fases_demais(client, cenario):
    csv = ("Fase;Descrição\n" + "".join(f"Etapa {i};T\n" for i in range(MAX_FASES))).encode()
    r = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Demais"},
                          files={"arquivo": ("p.csv", csv)}, headers=cenario["h"]["gerente"])
    assert r.status_code == 422 and "limite" in r.json()["detail"]["mensagem"]


def test_exportar_e_reimportar_com_fase_renomeada(client, cenario):
    h = cenario["h"]["gerente"]
    fase = cenario["fases"]["Monitoramento"]
    client.patch(_url(cenario, f"/{fase}"), json={"titulo": "Garantia"}, headers=h)
    _tarefa(client, cenario, fase, "Suporte pós go-live")
    planilha = client.get(f"/projetos/{cenario['id']}/exportar", headers=h).content
    linhas = list(openpyxl.load_workbook(io.BytesIO(planilha)).active.iter_rows(values_only=True))
    assert linhas[1][0] == "Garantia"
    r = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Cópia"},
                          files={"arquivo": ("c.xlsx", planilha)}, headers=h)
    assert "Garantia" in [f["titulo"] for f in r.json()["projeto"]["fases"]]


def test_pdf_com_fases_proprias(client, cenario):
    h = cenario["h"]["gerente"]
    for i in range(8):
        f = client.post(_url(cenario), json={"titulo": f"Etapa extra {i}"}, headers=h).json()
        _tarefa(client, cenario, f["id"], data_inicio="2026-03-02", data_fim="2026-03-10")
    r = client.get(f"/projetos/{cenario['id']}/relatorio", headers=h)
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
