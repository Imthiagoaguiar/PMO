"""Pilares: frentes de trabalho do projeto (TI, Suprimentos, CAPEX, Pessoas...)."""

import io
from datetime import datetime, timezone

import openpyxl
import pandas as pd
import pytest

from app import tempo
from app.integracoes import feriados
from app.models import MAX_PILARES, PapelUsuario
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
    pilares = {x["nome"]: x["id"] for x in p["pilares"]}
    return {"h": h, "id": p["id"], "fases": [f["id"] for f in p["fases"]], "pilares": pilares}


def _url(c, sufixo=""):
    return f"/projetos/{c['id']}/pilares{sufixo}"


def _tarefa(client, c, fase=0, **dados):
    r = client.post(f"/projetos/{c['id']}/fases/{c['fases'][fase]}/tarefas", json={"descricao": "T", **dados},
                    headers=c["h"]["gerente"])
    assert r.status_code == 201, r.text
    return r.json()


# ---------- Padrão ----------


def test_projeto_nasce_com_os_4_pilares_padrao(client, cenario):
    lista = client.get(_url(cenario), headers=cenario["h"]["leitor"]).json()
    assert [p["nome"] for p in lista] == ["TI", "Suprimentos", "CAPEX", "Pessoas"]
    assert all(p["farol"] is None and p["total_tarefas"] == 0 for p in lista)


def test_projeto_importado_tambem_nasce_com_os_pilares(client, cenario):
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Importado"}, headers=cenario["h"]["gerente"],
                    files={"arquivo": ("p.csv", "Fase;Descrição\nIniciação;TAP\n".encode())})
    assert [p["nome"] for p in r.json()["projeto"]["pilares"]] == ["TI", "Suprimentos", "CAPEX", "Pessoas"]


# ---------- Andamento calculado pelas tarefas ----------


def test_andamento_e_atrasos_por_pilar(client, cenario, monkeypatch):
    monkeypatch.setattr(tempo, "agora_utc", lambda: datetime(2026, 3, 20, 15, tzinfo=timezone.utc))
    ti, capex = cenario["pilares"]["TI"], cenario["pilares"]["CAPEX"]
    _tarefa(client, cenario, 0, pilar_id=ti, percentual_conclusao=100)
    _tarefa(client, cenario, 2, pilar_id=ti, percentual_conclusao=40, data_inicio="2026-03-02", data_fim="2026-03-10")
    _tarefa(client, cenario, 4, pilar_id=capex, percentual_conclusao=0)
    _tarefa(client, cenario, 1)  # sem pilar: não entra em nenhum

    por_nome = {p["nome"]: p for p in client.get(_url(cenario), headers=cenario["h"]["leitor"]).json()}
    assert (por_nome["TI"]["total_tarefas"], por_nome["TI"]["tarefas_concluidas"],
            por_nome["TI"]["tarefas_atrasadas"], por_nome["TI"]["percentual_conclusao"]) == (2, 1, 1, 70.0)
    assert (por_nome["CAPEX"]["total_tarefas"], por_nome["CAPEX"]["percentual_conclusao"]) == (1, 0.0)
    assert por_nome["Pessoas"]["total_tarefas"] == 0


def test_mover_tarefa_de_pilar_e_tirar_do_pilar(client, cenario):
    t = _tarefa(client, cenario, pilar_id=cenario["pilares"]["TI"])
    url = f"/projetos/{cenario['id']}/tarefas/{t['id']}"
    r = client.patch(url, json={"pilar_id": cenario["pilares"]["Pessoas"], "versao": t["versao"]}, headers=cenario["h"]["gerente"])
    assert r.json()["pilar_id"] == cenario["pilares"]["Pessoas"]
    r = client.patch(url, json={"pilar_id": None}, headers=cenario["h"]["gerente"])
    assert r.json()["pilar_id"] is None


def test_pilar_inexistente_na_tarefa(client, cenario):
    r = client.post(f"/projetos/{cenario['id']}/fases/{cenario['fases'][0]}/tarefas",
                    json={"descricao": "T", "pilar_id": 999_999}, headers=cenario["h"]["gerente"])
    assert r.status_code == 422


# ---------- Farol, status e financeiro ----------


def test_farol_e_status_registram_quem_e_quando(client, cenario):
    url = _url(cenario, f"/{cenario['pilares']['TI']}")
    r = client.patch(url, json={"farol": "amarelo", "status_texto": "  Migração atrasou 1 semana  "},
                     headers=cenario["h"]["editora"])
    assert r.status_code == 200, r.text
    pilar = r.json()
    assert (pilar["farol"], pilar["status_texto"]) == ("amarelo", "Migração atrasou 1 semana")
    assert pilar["status_atualizado_por"]["email"] == EDITORA
    assert pilar["status_atualizado_em"].endswith(("Z", "+00:00"))

    # Mudar só o financeiro não mexe em "quem avaliou o status".
    r = client.patch(url, json={"valor_orcado": "1000.00"}, headers=cenario["h"]["gerente"])
    assert r.json()["status_atualizado_por"]["email"] == EDITORA


@pytest.mark.parametrize("orcado, realizado, desvio, execucao", [
    ("1000.00", "1250.50", "250.50", 125.0),
    ("1000.00", "400.00", "-600.00", 40.0),
    ("1000.00", None, None, None),
    (None, "500.00", None, None),
    ("0", "0", "0.00", None),
])
def test_orcado_x_realizado(client, cenario, orcado, realizado, desvio, execucao):
    r = client.patch(_url(cenario, f"/{cenario['pilares']['CAPEX']}"),
                     json={"valor_orcado": orcado, "valor_realizado": realizado}, headers=cenario["h"]["gerente"])
    assert r.status_code == 200, r.text
    p = r.json()
    assert (p["desvio"] is None and desvio is None) or float(p["desvio"]) == float(desvio)
    assert p["execucao_financeira"] == execucao


@pytest.mark.parametrize("corpo", [
    {"valor_orcado": -1}, {"valor_realizado": "1e20"}, {"valor_orcado": "10.123"},
    {"farol": "azul"}, {"status_texto": "x" * 1001}, {"nome": "   "}, {"nome": None}, {"ordem": -1},
])
def test_valores_invalidos(client, cenario, corpo):
    r = client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json=corpo, headers=cenario["h"]["gerente"])
    assert r.status_code == 422, r.text


def test_limpar_farol(client, cenario):
    url = _url(cenario, f"/{cenario['pilares']['TI']}")
    client.patch(url, json={"farol": "vermelho"}, headers=cenario["h"]["gerente"])
    assert client.patch(url, json={"farol": None}, headers=cenario["h"]["gerente"]).json()["farol"] is None


# ---------- Criar, renomear, excluir ----------


def test_criar_renomear_e_reordenar(client, cenario):
    r = client.post(_url(cenario), json={"nome": "  Jurídico   e  Compliance ", "valor_orcado": "5000"},
                    headers=cenario["h"]["editora"])
    assert r.status_code == 201, r.text
    novo = r.json()
    assert (novo["nome"], novo["ordem"], float(novo["valor_orcado"])) == ("Jurídico e Compliance", 4, 5000.0)

    r = client.patch(_url(cenario, f"/{novo['id']}"), json={"nome": "Jurídico", "ordem": 0}, headers=cenario["h"]["gerente"])
    assert r.json()["nome"] == "Jurídico"
    nomes = [p["nome"] for p in client.get(_url(cenario), headers=cenario["h"]["gerente"]).json()]
    assert nomes[0] in ("TI", "Jurídico") and "Jurídico" in nomes


@pytest.mark.parametrize("nome", ["TI", "ti", "  Ti ", "suprimentos", "Capex"])
def test_nome_repetido_ignora_maiusculas_e_espacos(client, cenario, nome):
    assert client.post(_url(cenario), json={"nome": nome}, headers=cenario["h"]["gerente"]).status_code == 409


def test_renomear_para_nome_existente(client, cenario):
    r = client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json={"nome": "pessoas"}, headers=cenario["h"]["gerente"])
    assert r.status_code == 409
    # Mudar só a caixa do próprio nome é permitido.
    r = client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json={"nome": "Ti"}, headers=cenario["h"]["gerente"])
    assert r.status_code == 200


def test_limite_de_pilares(client, cenario):
    for i in range(MAX_PILARES - 4):
        assert client.post(_url(cenario), json={"nome": f"Frente {i}"}, headers=cenario["h"]["gerente"]).status_code == 201
    assert client.post(_url(cenario), json={"nome": "Uma a mais"}, headers=cenario["h"]["gerente"]).status_code == 422


def test_excluir_pilar_mantem_as_tarefas(client, cenario):
    ti = cenario["pilares"]["TI"]
    t = _tarefa(client, cenario, pilar_id=ti)
    assert client.delete(_url(cenario, f"/{ti}"), headers=cenario["h"]["gerente"]).status_code == 204
    projeto = client.get(f"/projetos/{cenario['id']}", headers=cenario["h"]["gerente"]).json()
    tarefa = projeto["fases"][0]["tarefas"][0]
    assert (tarefa["id"], tarefa["pilar_id"]) == (t["id"], None)
    assert tarefa["versao"] == t["versao"] + 1  # quem estava com a tarefa aberta recebe 409
    assert "TI" not in [p["nome"] for p in projeto["pilares"]]


# ---------- Permissões e projeto encerrado ----------


def test_visualizador_le_mas_nao_altera(client, cenario):
    h = cenario["h"]["leitor"]
    assert client.get(_url(cenario), headers=h).status_code == 200
    assert client.post(_url(cenario), json={"nome": "X"}, headers=h).status_code == 403
    assert client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json={"farol": "verde"}, headers=h).status_code == 403
    assert client.delete(_url(cenario, f"/{cenario['pilares']['TI']}"), headers=h).status_code == 403


def test_projeto_encerrado_deixa_pilares_somente_leitura(client, cenario):
    client.patch(f"/projetos/{cenario['id']}", json={"status": "Concluído"}, headers=cenario["h"]["gerente"])
    assert client.post(_url(cenario), json={"nome": "X"}, headers=cenario["h"]["gerente"]).status_code == 409
    r = client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json={"farol": "verde"}, headers=cenario["h"]["gerente"])
    assert r.status_code == 409
    assert client.get(_url(cenario), headers=cenario["h"]["leitor"]).status_code == 200


def test_pilar_de_outro_projeto_pela_url(client, cenario):
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=cenario["h"]["gerente"]).json()
    alheio = outro["pilares"][0]["id"]
    r = client.patch(_url(cenario, f"/{alheio}"), json={"farol": "verde"}, headers=cenario["h"]["gerente"])
    assert r.status_code == 404
    assert client.delete(_url(cenario, f"/{alheio}"), headers=cenario["h"]["gerente"]).status_code == 404


def test_versao_desatualizada_do_pilar(client, cenario):
    url = _url(cenario, f"/{cenario['pilares']['TI']}")
    v = client.get(_url(cenario), headers=cenario["h"]["gerente"]).json()[0]["versao"]
    assert client.patch(url, json={"farol": "verde", "versao": v}, headers=cenario["h"]["gerente"]).status_code == 200
    assert client.patch(url, json={"farol": "vermelho", "versao": v}, headers=cenario["h"]["editora"]).status_code == 409


def test_alteracoes_ficam_no_historico(client, cenario):
    client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json={"farol": "vermelho"}, headers=cenario["h"]["gerente"])
    novo = client.post(_url(cenario), json={"nome": "Jurídico"}, headers=cenario["h"]["gerente"]).json()
    client.delete(_url(cenario, f"/{novo['id']}"), headers=cenario["h"]["gerente"])
    acoes = [e["acao"] for e in client.get(f"/projetos/{cenario['id']}/historico", headers=cenario["h"]["leitor"]).json()["itens"]]
    assert {"pilar.atualizado", "pilar.criado", "pilar.excluido"} <= set(acoes)


# ---------- Planilha ----------


def test_importar_com_coluna_pilar(client, cenario):
    csv = ("Fase;Descrição;Pilar\nIniciação;TAP;ti\nExecução;Comprar servidores;  CAPEX \n"
           "Execução;Contrato;Jurídico\nEncerramento;Lições;\n")
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Com pilares"}, headers=cenario["h"]["gerente"],
                    files={"arquivo": ("p.csv", csv.encode())})
    assert r.status_code == 201, r.text
    projeto = r.json()["projeto"]
    nomes = {p["id"]: p["nome"] for p in projeto["pilares"]}
    assert list(nomes.values()) == ["TI", "Suprimentos", "CAPEX", "Pessoas", "Jurídico"]  # "ti" casou com "TI"
    tarefas = {t["descricao"]: nomes.get(t["pilar_id"]) for f in projeto["fases"] for t in f["tarefas"]}
    assert tarefas == {"TAP": "TI", "Comprar servidores": "CAPEX", "Contrato": "Jurídico", "Lições": None}


def test_importar_com_pilares_demais(client, cenario):
    linhas = "".join(f"Iniciação;T{i};Frente {i}\n" for i in range(MAX_PILARES))
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Demais"}, headers=cenario["h"]["gerente"],
                    files={"arquivo": ("p.csv", ("Fase;Descrição;Pilar\n" + linhas).encode())})
    assert r.status_code == 422
    assert "limite" in r.json()["detail"]["mensagem"]


def test_exportar_e_reimportar_mantem_os_pilares(client, cenario):
    _tarefa(client, cenario, 0, descricao="TAP", pilar_id=cenario["pilares"]["TI"])
    _tarefa(client, cenario, 2, descricao="Sem pilar")
    exportado = client.get(f"/projetos/{cenario['id']}/exportar", headers=cenario["h"]["leitor"]).content
    df = pd.read_excel(io.BytesIO(exportado))
    assert df["Pilar"].tolist()[0] == "TI" and pd.isna(df["Pilar"].tolist()[1])

    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Cópia"}, headers=cenario["h"]["gerente"],
                    files={"arquivo": ("copia.xlsx", exportado)})
    projeto = r.json()["projeto"]
    nomes = {p["id"]: p["nome"] for p in projeto["pilares"]}
    assert {t["descricao"]: nomes.get(t["pilar_id"]) for f in projeto["fases"] for t in f["tarefas"]} == {
        "TAP": "TI", "Sem pilar": None}


def test_pilar_com_formula_e_exportado_como_texto(client, cenario):
    client.patch(_url(cenario, f"/{cenario['pilares']['TI']}"), json={"nome": "=HYPERLINK(\"x\")"}, headers=cenario["h"]["gerente"])
    _tarefa(client, cenario, pilar_id=cenario["pilares"]["TI"])
    exportado = client.get(f"/projetos/{cenario['id']}/exportar", headers=cenario["h"]["gerente"]).content
    celula = openpyxl.load_workbook(io.BytesIO(exportado)).active["G2"]
    assert celula.data_type == "s" and celula.value == "=HYPERLINK(\"x\")"
