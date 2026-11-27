"""
Marcos da linha do tempo (pins, bandeiras, formas, faixas) e a aba Orçamento (itens x lançamentos).
"""

import io
from datetime import date, datetime, timezone
from decimal import Decimal

import openpyxl
import pytest
from pypdf import PdfReader

from app import tempo
from app.integracoes import feriados
from app.models import MAX_MARCOS, PapelUsuario

GERENTE, LEITOR = "gerente@empresa.com.br", "leitor@empresa.com.br"
HOJE = date(2026, 3, 20)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())
    monkeypatch.setattr(tempo, "agora_utc", lambda: datetime(2026, 3, 20, 15, tzinfo=timezone.utc))


@pytest.fixture
def c(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    leitor = criar_usuario(LEITOR)
    h = {"gerente": login(GERENTE), "leitor": login(LEITOR)}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP", "orcamento_aprovado": "100000.00",
                                              "data_inicio": "2026-01-01"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    return {"h": h, "id": p["id"], "fases": [f["id"] for f in p["fases"]],
            "pilares": {x["nome"]: x["id"] for x in p["pilares"]}}


# ---------- Marcos ----------


def _marco(client, c, **dados):
    corpo = {"titulo": "Pré-entrega", "data": "2026-04-10", **dados}
    return client.post(f"/projetos/{c['id']}/marcos", json=corpo, headers=c["h"]["gerente"])


def test_criar_listar_e_arrastar_marco(client, c):
    r = _marco(client, c, forma="bandeira", cor="vermelho", descricao="Entrega parcial ao cliente")
    assert r.status_code == 201
    m = r.json()
    assert (m["forma"], m["cor"], m["data_fim"]) == ("bandeira", "vermelho", None)
    _marco(client, c, titulo="Kickoff", data="2026-01-05", forma="pin")
    lista = client.get(f"/projetos/{c['id']}/marcos", headers=c["h"]["leitor"]).json()
    assert [x["titulo"] for x in lista] == ["Kickoff", "Pré-entrega"]  # por data

    # "Arrastar": muda a data mandando a versão lida; a mesma versão de novo é recusada.
    url = f"/projetos/{c['id']}/marcos/{m['id']}"
    r = client.patch(url, json={"data": "2026-04-17", "versao": m["versao"]}, headers=c["h"]["gerente"])
    assert r.status_code == 200 and r.json()["data"] == "2026-04-17"
    assert client.patch(url, json={"data": "2026-04-20", "versao": m["versao"]}, headers=c["h"]["gerente"]).status_code == 409


def test_faixa_e_periodo(client, c):
    assert _marco(client, c, forma="faixa").status_code == 422  # sem fim
    assert _marco(client, c, forma="faixa", data_fim="2026-04-01").status_code == 422  # fim antes do início
    assert _marco(client, c, forma="pin", data_fim="2026-04-20").status_code == 422  # pin é um dia só
    r = _marco(client, c, titulo="Congelamento", forma="faixa", data_fim="2026-04-24", cor="cinza")
    assert r.status_code == 201
    url = f"/projetos/{c['id']}/marcos/{r.json()['id']}"
    # Virar estrela apaga o fim do período.
    r = client.patch(url, json={"forma": "estrela"}, headers=c["h"]["gerente"])
    assert r.status_code == 200 and r.json()["data_fim"] is None
    assert client.patch(url, json={"forma": "faixa"}, headers=c["h"]["gerente"]).status_code == 422
    assert client.patch(url, json={"forma": "faixa", "data_fim": "2026-04-30"}, headers=c["h"]["gerente"]).status_code == 200


@pytest.mark.parametrize("forma", ["pin", "bandeira", "circulo", "quadrado", "triangulo", "losango", "estrela"])
def test_todas_as_formas(client, c, forma):
    assert _marco(client, c, forma=forma).status_code == 201


def test_marcos_permissoes_e_limite(client, c, monkeypatch):
    m = _marco(client, c).json()
    h = c["h"]["leitor"]
    assert client.post(f"/projetos/{c['id']}/marcos", json={"titulo": "x", "data": "2026-04-01"}, headers=h).status_code == 403
    assert client.delete(f"/projetos/{c['id']}/marcos/{m['id']}", headers=h).status_code == 403
    assert client.delete(f"/projetos/{c['id']}/marcos/{m['id']}", headers=c["h"]["gerente"]).status_code == 204
    import app.routers.marcos as rotas
    monkeypatch.setattr(rotas, "MAX_MARCOS", 2)
    _marco(client, c)
    _marco(client, c)
    r = _marco(client, c)
    assert r.status_code == 422 and "Limite" in r.json()["detail"]
    assert MAX_MARCOS == 100


def test_marcos_no_pdf(client, c):
    client.post(f"/projetos/{c['id']}/fases/{c['fases'][0]}/tarefas",
                json={"descricao": "Kickoff", "data_inicio": "2026-03-02", "data_fim": "2026-03-06"}, headers=c["h"]["gerente"])
    for forma in ["pin", "bandeira", "circulo", "quadrado", "triangulo", "losango", "estrela"]:
        _marco(client, c, titulo=f"Marco {forma}", forma=forma, data="2026-03-04")
    _marco(client, c, titulo="Congelamento", forma="faixa", data="2026-03-09", data_fim="2026-03-13")
    r = client.get(f"/projetos/{c['id']}/relatorio", headers=c["h"]["leitor"])
    texto = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "Marcos" in texto and "Marco estrela" in texto and "09/03/2026 a 13/03/2026" in texto


def test_so_marcos_sem_tarefas_tambem_aparecem_no_pdf(client, c):
    _marco(client, c, titulo="Go-live")
    r = client.get(f"/projetos/{c['id']}/relatorio", headers=c["h"]["gerente"])
    texto = " ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages)
    assert "Go-live" in texto


# ---------- Orçamento ----------


def _item(client, c, descricao="Licenças", valor="40000.00", **dados):
    r = client.post(f"/projetos/{c['id']}/orcamento/itens",
                    json={"descricao": descricao, "valor_orcado": valor, **dados}, headers=c["h"]["gerente"])
    assert r.status_code == 201, r.text
    return r.json()


def _lanc(client, c, item, valor, data="2026-02-10", descricao="NF", **dados):
    return client.post(f"/projetos/{c['id']}/orcamento/lancamentos",
                       json={"item_id": item["id"], "valor": valor, "data": data, "descricao": descricao, **dados},
                       headers=c["h"]["gerente"])


def _resumo(client, c, quem="leitor"):
    r = client.get(f"/projetos/{c['id']}/orcamento", headers=c["h"][quem])
    assert r.status_code == 200, r.text
    return r.json()


def test_resumo_vazio(client, c):
    r = _resumo(client, c)
    assert Decimal(r["total_orcado"]) == 0 and r["itens"] == [] and r["curva"] == [] and r["alertas"] == []


def test_itens_lancamentos_e_totais(client, c):
    lic = _item(client, c, "Licenças", "40000.00", categoria="capex", pilar_id=c["pilares"]["TI"])
    cons = _item(client, c, "Consultoria", "30000.00", categoria="opex")
    assert _lanc(client, c, lic, "25000.00", "2026-01-15", documento="NF 123").status_code == 201
    assert _lanc(client, c, lic, "-1000.00", "2026-02-01", descricao="Estorno").status_code == 201
    assert _lanc(client, c, cons, "12000.50", "2026-02-20").status_code == 201

    r = _resumo(client, c)
    assert Decimal(r["total_orcado"]) == Decimal("70000.00")
    assert Decimal(r["total_realizado"]) == Decimal("36000.50")
    assert Decimal(r["saldo"]) == Decimal("33999.50")
    itens = {i["descricao"]: i for i in r["itens"]}
    assert Decimal(itens["Licenças"]["valor_realizado"]) == Decimal("24000.00") and itens["Licenças"]["execucao"] == 60.0
    assert itens["Licenças"]["pilar_nome"] == "TI" and itens["Licenças"]["lancamentos"] == 2
    cat = {x["chave"]: (Decimal(x["orcado"]), Decimal(x["realizado"])) for x in r["por_categoria"]}
    assert cat == {"capex": (Decimal("40000.00"), Decimal("24000.00")), "opex": (Decimal("30000.00"), Decimal("12000.50"))}
    assert [(x["nome"]) for x in r["por_pilar"]] == ["TI", "Sem pilar"]


def test_valor_zero_e_validacoes(client, c):
    item = _item(client, c)
    assert _lanc(client, c, item, "0").status_code == 422
    assert client.post(f"/projetos/{c['id']}/orcamento/itens", json={"descricao": "x", "valor_orcado": "-1"},
                       headers=c["h"]["gerente"]).status_code == 422
    assert client.post(f"/projetos/{c['id']}/orcamento/itens", json={"descricao": "x", "valor_orcado": "1.234"},
                       headers=c["h"]["gerente"]).status_code == 422  # 3 casas decimais


def test_curva_mensal_com_planejado(client, c):
    # Cronograma de 01/01 a 31/03: o orçado (90 mil) se distribui 1/3 por mês, mais ou menos.
    client.post(f"/projetos/{c['id']}/fases/{c['fases'][0]}/tarefas",
                json={"descricao": "Tudo", "data_inicio": "2026-01-01", "data_fim": "2026-03-31"}, headers=c["h"]["gerente"])
    item = _item(client, c, valor="90000.00")
    _lanc(client, c, item, "10000.00", "2026-01-20")
    _lanc(client, c, item, "5000.00", "2026-03-05")
    curva = _resumo(client, c)["curva"]
    assert [p["mes"] for p in curva] == ["2026-01", "2026-02", "2026-03"]
    assert [Decimal(p["realizado_acumulado"]) for p in curva] == [Decimal("10000"), Decimal("10000"), Decimal("15000")]
    assert Decimal(curva[0]["planejado_acumulado"]) == Decimal("31000.00")  # 31 de 90 dias
    assert Decimal(curva[-1]["planejado_acumulado"]) == Decimal("90000.00")


def test_alertas(client, c):
    t = client.post(f"/projetos/{c['id']}/fases/{c['fases'][0]}/tarefas",
                    json={"descricao": "T", "data_inicio": "2026-01-01", "data_fim": "2026-12-31", "percentual_conclusao": 20},
                    headers=c["h"]["gerente"]).json()
    assert t["percentual_conclusao"] == 20
    item = _item(client, c, "Obra", "120000.00")  # acima do aprovado (100 mil)
    _lanc(client, c, item, "60000.00", "2026-02-01")
    alertas = [a["mensagem"] for a in _resumo(client, c)["alertas"]]
    assert any("acima do orçamento aprovado (R$ 100.000,00)" in a for a in alertas)
    # 60 mil gastos com 20% feito: no ritmo atual fecha em 300 mil.
    assert any("deve fechar em R$ 300.000,00" in a for a in alertas)
    assert any("Gasto adiantado" in a for a in alertas)
    sem_motivo = _lanc(client, c, item, "70000.00", "2026-03-01")  # estoura o item: precisa dizer por quê
    assert sem_motivo.status_code == 422 and "Explique por que está estourando" in sem_motivo.json()["detail"]
    assert _lanc(client, c, item, "70000.00", "2026-03-01", justificativa="Fundação extra").status_code == 201
    alertas = [a["mensagem"] for a in _resumo(client, c)["alertas"]]
    assert any("“Obra” estourou" in a and "Justificativa: “Fundação extra”" in a for a in alertas)
    assert any("já passou do orçado" in a for a in alertas)
    r = _resumo(client, c)
    assert Decimal(r["previsao_ritmo"]) == Decimal("650000.00")  # referência: no ritmo atual
    assert Decimal(r["previsao_final"]) == Decimal("130000.00")  # realizado + projetado (nenhum projetado)


def test_editar_e_excluir(client, c):
    item = _item(client, c)
    lanc = _lanc(client, c, item, "100.00").json()
    url_item = f"/projetos/{c['id']}/orcamento/itens/{item['id']}"
    h = c["h"]["gerente"]
    r = client.patch(url_item, json={"valor_orcado": "45000.00", "versao": item["versao"]}, headers=h)
    assert r.status_code == 200 and Decimal(r.json()["valor_orcado"]) == Decimal("45000.00")
    assert client.patch(url_item, json={"descricao": "x", "versao": item["versao"]}, headers=h).status_code == 409
    url_lanc = f"/projetos/{c['id']}/orcamento/lancamentos/{lanc['id']}"
    assert client.patch(url_lanc, json={"valor": "150.00"}, headers=h).json()["valor"] in ("150.00", "150")
    # Item com lançamentos: só sai junto com eles, e só se a pessoa confirmar.
    r = client.delete(url_item, headers=h)
    assert r.status_code == 409 and "1 lançamento(s)" in r.json()["detail"]
    assert client.delete(url_item + "?excluir_lancamentos=true", headers=h).status_code == 204
    assert client.get(f"/projetos/{c['id']}/orcamento/lancamentos", headers=h).json()["total"] == 0


def test_lancamentos_filtros_e_paginacao(client, c):
    capex = _item(client, c, "Servidor", "50000.00", categoria="capex", pilar_id=c["pilares"]["TI"])
    opex = _item(client, c, "Treinamento", "8000.00", categoria="opex", pilar_id=c["pilares"]["Pessoas"])
    for i in range(5):
        _lanc(client, c, capex, "100.00", f"2026-01-{10 + i}")
    _lanc(client, c, opex, "200.00", "2026-03-01")
    url = f"/projetos/{c['id']}/orcamento/lancamentos"
    h = c["h"]["leitor"]
    tudo = client.get(url, headers=h).json()
    assert tudo["total"] == 6 and tudo["itens"][0]["data"] == "2026-03-01"  # mais recente primeiro
    assert tudo["itens"][0]["criado_por"]["nome"] == "gerente"
    assert client.get(f"{url}?categoria=opex", headers=h).json()["total"] == 1
    assert client.get(f"{url}?pilar_id={c['pilares']['TI']}", headers=h).json()["total"] == 5
    assert client.get(f"{url}?de=2026-01-12&ate=2026-01-13", headers=h).json()["total"] == 2
    assert len(client.get(f"{url}?limite=2&offset=4", headers=h).json()["itens"]) == 2


def test_isolamento_e_permissoes(client, c):
    item = _item(client, c)
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=c["h"]["gerente"]).json()
    r = client.post(f"/projetos/{outro['id']}/orcamento/lancamentos",
                    json={"item_id": item["id"], "valor": "10", "data": "2026-01-01", "descricao": "x"}, headers=c["h"]["gerente"])
    assert r.status_code == 404  # item de outro projeto
    r = client.post(f"/projetos/{c['id']}/orcamento/itens",
                    json={"descricao": "x", "valor_orcado": "1", "pilar_id": outro["pilares"][0]["id"]}, headers=c["h"]["gerente"])
    assert r.status_code == 422  # pilar de outro projeto
    h = c["h"]["leitor"]
    assert client.post(f"/projetos/{c['id']}/orcamento/itens", json={"descricao": "x", "valor_orcado": "1"}, headers=h).status_code == 403
    assert _resumo(client, c, "leitor")["itens"][0]["descricao"] == "Licenças"


def test_pilar_passa_a_usar_o_orcamento(client, c):
    ti = c["pilares"]["TI"]
    url = f"/projetos/{c['id']}/pilares"
    h = c["h"]["gerente"]
    p = next(x for x in client.get(url, headers=h).json() if x["id"] == ti)
    client.patch(f"{url}/{ti}", json={"valor_orcado": "999.00", "versao": p["versao"]}, headers=h)
    item = _item(client, c, valor="5000.00", pilar_id=ti)
    _lanc(client, c, item, "1250.00")
    p = next(x for x in client.get(url, headers=h).json() if x["id"] == ti)
    assert p["financeiro_do_orcamento"] is True
    assert (Decimal(p["valor_orcado"]), Decimal(p["valor_realizado"]), p["execucao_financeira"]) == (
        Decimal("5000.00"), Decimal("1250.00"), 25.0)
    # Mudar o financeiro pelo pilar é recusado; reenviar os mesmos valores junto com o farol, não.
    r = client.patch(f"{url}/{ti}", json={"valor_orcado": "1.00", "versao": p["versao"]}, headers=h)
    assert r.status_code == 422 and "aba Orçamento" in r.json()["detail"]
    r = client.patch(f"{url}/{ti}", json={"farol": "verde", "valor_orcado": p["valor_orcado"],
                                          "valor_realizado": p["valor_realizado"], "versao": p["versao"]}, headers=h)
    assert r.status_code == 200 and r.json()["farol"] == "verde"


def test_exportar_orcamento(client, c):
    item = _item(client, c, "=SOMA(A1)", "1000.00")
    _lanc(client, c, item, "250.00", descricao="+cmd|calc")
    r = client.get(f"/projetos/{c['id']}/orcamento/exportar", headers=c["h"]["leitor"])
    assert r.status_code == 200
    livro = openpyxl.load_workbook(io.BytesIO(r.content))
    itens = list(livro["Itens do orçamento"].iter_rows(values_only=True))
    assert itens[1][0] == "=SOMA(A1)" and livro["Itens do orçamento"]["A2"].data_type == "s"  # texto, não fórmula
    assert itens[1][3] == 1000 and itens[1][4] == 250
    lancs = list(livro["Lançamentos"].iter_rows(values_only=True))
    assert lancs[1][1] == "Realizado" and lancs[1][4] == "+cmd|calc" and lancs[1][6] == 250
    assert "Resumo do orçamento" in livro.sheetnames


def test_orcamento_no_pdf(client, c):
    item = _item(client, c, "Licenças", "40000.00")
    _lanc(client, c, item, "45000.00", "2026-01-10", justificativa="Reajuste do fornecedor")
    _lanc(client, c, item, "1000.00", "2026-02-10", justificativa="Reajuste do fornecedor")
    r = client.get(f"/projetos/{c['id']}/relatorio", headers=c["h"]["leitor"])
    texto = " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
    assert "Orçamento" in texto and "Previsão ao final" in texto and "estourou" in texto
