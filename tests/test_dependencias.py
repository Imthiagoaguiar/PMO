"""
Dependências entre tarefas: "só consigo fazer B se terminar A" (início após fim) e
"só consigo terminar C se terminar A antes" (fim após fim). O sistema bloqueia e ajusta datas.
"""

import pytest

from app.integracoes import feriados
from app.models import PapelUsuario

GERENTE, LEITOR = "gerente@empresa.com.br", "leitor@empresa.com.br"


@pytest.fixture(autouse=True)
def sem_internet(monkeypatch):
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())


@pytest.fixture
def c(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    leitor = criar_usuario(LEITOR)
    h = {"gerente": login(GERENTE), "leitor": login(LEITOR)}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    fase = p["fases"][0]["id"]

    def tarefa(descricao, **dados):
        r = client.post(f"/projetos/{p['id']}/fases/{fase}/tarefas", json={"descricao": descricao, **dados},
                        headers=h["gerente"])
        assert r.status_code == 201, r.text
        return r.json()

    return {"h": h, "id": p["id"], "tarefa": tarefa}


def _dep(client, c, a, b, tipo="inicio_apos_fim", **extra):
    return client.post(f"/projetos/{c['id']}/dependencias",
                       json={"predecessora_id": a["id"], "sucessora_id": b["id"], "tipo": tipo, **extra},
                       headers=c["h"]["gerente"])


def _pct(client, c, t, valor):
    return client.patch(f"/projetos/{c['id']}/tarefas/{t['id']}", json={"percentual_conclusao": valor},
                        headers=c["h"]["gerente"])


# ---------- Bloqueios ----------


def test_b_so_comeca_depois_que_a_terminar(client, c):
    a, b = c["tarefa"]("Levantar requisitos"), c["tarefa"]("Configurar o ERP")
    assert _dep(client, c, a, b).status_code == 201
    r = _pct(client, c, b, 10)
    assert r.status_code == 422
    assert "“Configurar o ERP” só pode começar depois que “Levantar requisitos” terminar (está em 0%)" in r.json()["detail"]
    assert _pct(client, c, a, 60).status_code == 200
    assert _pct(client, c, b, 10).status_code == 422  # 60% ainda não é terminar
    assert _pct(client, c, a, 100).status_code == 200
    assert _pct(client, c, b, 10).status_code == 200


def test_c_so_termina_depois_que_a_terminar(client, c):
    a, cc = c["tarefa"]("Homologação"), c["tarefa"]("Documentação")
    assert _dep(client, c, a, cc, "fim_apos_fim").status_code == 201
    assert _pct(client, c, cc, 90).status_code == 200  # pode andar em paralelo
    r = _pct(client, c, cc, 100)
    assert r.status_code == 422 and "só pode ser concluída depois" in r.json()["detail"]
    _pct(client, c, a, 100)
    assert _pct(client, c, cc, 100).status_code == 200


def test_reabrir_a_depois_que_b_comecou_e_bloqueado(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    _dep(client, c, a, b)
    _pct(client, c, a, 100)
    _pct(client, c, b, 20)
    r = _pct(client, c, a, 80)
    assert r.status_code == 422 and "não pode ser reaberta" in r.json()["detail"] and "Volte “B” para 0%" in r.json()["detail"]
    _pct(client, c, b, 0)
    assert _pct(client, c, a, 80).status_code == 200


def test_reabrir_a_com_fim_apos_fim(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    _dep(client, c, a, b, "fim_apos_fim")
    _pct(client, c, a, 100)
    _pct(client, c, b, 100)
    assert _pct(client, c, a, 50).status_code == 422
    _pct(client, c, b, 70)
    assert _pct(client, c, a, 50).status_code == 200


def test_varias_predecessoras(client, c):
    a1, a2, b = c["tarefa"]("A1"), c["tarefa"]("A2"), c["tarefa"]("B")
    _dep(client, c, a1, b)
    _dep(client, c, a2, b)
    _pct(client, c, a1, 100)
    r = _pct(client, c, b, 5)
    assert r.status_code == 422 and "“A2”" in r.json()["detail"]


def test_editar_outros_campos_nao_e_bloqueado(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    _dep(client, c, a, b)
    r = client.patch(f"/projetos/{c['id']}/tarefas/{b['id']}", json={"descricao": "B revisada", "percentual_conclusao": 0},
                     headers=c["h"]["gerente"])
    assert r.status_code == 200


# ---------- Criação ----------


def test_criar_dependencia_validacoes(client, c, criar_usuario, login):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    assert _dep(client, c, a, a).status_code == 422
    assert _dep(client, c, a, b).status_code == 201
    assert _dep(client, c, a, b).status_code == 409  # repetida
    r = _dep(client, c, b, a)
    assert r.status_code == 422 and "ciclo" in r.json()["detail"]
    lista = client.get(f"/projetos/{c['id']}/dependencias", headers=c["h"]["leitor"]).json()
    assert [(d["predecessora_id"], d["sucessora_id"], d["tipo"]) for d in lista] == [(a["id"], b["id"], "inicio_apos_fim")]


def test_ciclo_indireto(client, c):
    a, b, d = c["tarefa"]("A"), c["tarefa"]("B"), c["tarefa"]("D")
    _dep(client, c, a, b)
    _dep(client, c, b, d)
    r = _dep(client, c, d, a)
    assert r.status_code == 422 and "ciclo" in r.json()["detail"]


def test_dependencia_que_ja_nasceria_quebrada(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B", percentual_conclusao=30)
    r = _dep(client, c, a, b)
    assert r.status_code == 422 and "já começaria quebrada" in r.json()["detail"]


def test_tarefa_de_outro_projeto(client, c):
    a = c["tarefa"]("A")
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=c["h"]["gerente"]).json()
    alheia = client.post(f"/projetos/{outro['id']}/fases/{outro['fases'][0]['id']}/tarefas", json={"descricao": "X"},
                         headers=c["h"]["gerente"]).json()
    assert _dep(client, c, a, alheia).status_code == 404


def test_leitor_ve_mas_nao_cria_nem_exclui(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    dep = _dep(client, c, a, b).json()["dependencia"]
    h = c["h"]["leitor"]
    assert client.get(f"/projetos/{c['id']}/dependencias", headers=h).status_code == 200
    assert client.post(f"/projetos/{c['id']}/dependencias", json={"predecessora_id": b["id"], "sucessora_id": a["id"]},
                       headers=h).status_code == 403
    assert client.delete(f"/projetos/{c['id']}/dependencias/{dep['id']}", headers=h).status_code == 403


def test_excluir_dependencia_libera_a_tarefa(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    dep = _dep(client, c, a, b).json()["dependencia"]
    assert client.delete(f"/projetos/{c['id']}/dependencias/{dep['id']}", headers=c["h"]["gerente"]).status_code == 204
    assert _pct(client, c, b, 50).status_code == 200


def test_excluir_a_tarefa_leva_a_dependencia(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    _dep(client, c, a, b)
    assert client.delete(f"/projetos/{c['id']}/tarefas/{a['id']}", headers=c["h"]["gerente"]).status_code == 204
    assert client.get(f"/projetos/{c['id']}/dependencias", headers=c["h"]["gerente"]).json() == []
    assert _pct(client, c, b, 50).status_code == 200


# ---------- Datas ----------


def test_ajusta_datas_em_dias_uteis_e_em_cascata(client, c):
    # A: seg 02/03 a sex 06/03. B: 3 dias úteis a partir de qua 04/03. D (depende de B): 2 dias a partir de 05/03.
    a = c["tarefa"]("A", data_inicio="2026-03-02", data_fim="2026-03-06")
    b = c["tarefa"]("B", data_inicio="2026-03-04", duracao_dias_uteis=3)
    d = c["tarefa"]("D", data_inicio="2026-03-05", duracao_dias_uteis=2)
    r = _dep(client, c, b, d)
    assert r.status_code == 201  # B termina 06/03 -> D vai para seg 09/03
    assert [(t["descricao"], t["data_inicio"], t["data_fim"]) for t in r.json()["reprogramadas"]] == [
        ("D", "2026-03-09", "2026-03-10")]

    r = _dep(client, c, a, b)
    assert r.status_code == 201
    mudou = {t["descricao"]: (t["data_inicio"], t["data_fim"]) for t in r.json()["reprogramadas"]}
    assert mudou["B"] == ("2026-03-09", "2026-03-11")  # depois de A, mantendo 3 dias úteis
    assert mudou["D"] == ("2026-03-12", "2026-03-13")  # e quem depende de B anda junto


def test_fim_apos_fim_so_empurra_o_termino(client, c):
    a = c["tarefa"]("A", data_inicio="2026-03-02", data_fim="2026-03-20")
    b = c["tarefa"]("B", data_inicio="2026-03-02", data_fim="2026-03-10")
    r = _dep(client, c, a, b, "fim_apos_fim")
    assert [(t["data_inicio"], t["data_fim"]) for t in r.json()["reprogramadas"]] == [("2026-03-02", "2026-03-20")]


def test_sem_ajustar_datas(client, c):
    a = c["tarefa"]("A", data_inicio="2026-03-02", data_fim="2026-03-06")
    b = c["tarefa"]("B", data_inicio="2026-03-03", data_fim="2026-03-04")
    r = _dep(client, c, a, b, ajustar_datas=False)
    assert r.json()["reprogramadas"] == []
    # Depois, "reprogramar" aplica todas as dependências de uma vez.
    r = client.post(f"/projetos/{c['id']}/dependencias/reprogramar", headers=c["h"]["gerente"])
    assert [(t["data_inicio"], t["data_fim"]) for t in r.json()["reprogramadas"]] == [("2026-03-09", "2026-03-10")]
    r = client.post(f"/projetos/{c['id']}/dependencias/reprogramar", headers=c["h"]["gerente"])
    assert r.json()["reprogramadas"] == []  # já está tudo em ordem


def test_reprogramar_sobe_a_versao(client, c):
    a = c["tarefa"]("A", data_inicio="2026-03-02", data_fim="2026-03-06")
    b = c["tarefa"]("B", data_inicio="2026-03-03", data_fim="2026-03-04")
    _dep(client, c, a, b)
    r = client.patch(f"/projetos/{c['id']}/tarefas/{b['id']}", json={"descricao": "x", "versao": b["versao"]},
                     headers=c["h"]["gerente"])
    assert r.status_code == 409  # a tela com as datas antigas não desfaz a reprogramação


def test_historico_registra(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    _dep(client, c, a, b)
    acoes = [e["acao"] for e in client.get(f"/projetos/{c['id']}/historico", headers=c["h"]["gerente"]).json()["itens"]]
    assert "dependencia.criada" in acoes
