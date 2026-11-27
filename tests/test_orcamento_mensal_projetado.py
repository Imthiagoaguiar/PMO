"""
Orçamento (26/11/2026): orçado mês a mês por item, gastos projetados (futuros) com a previsão final =
realizado + projetado, ajuste do valor gasto pelo item e justificativa obrigatória quando o gasto estoura.
"""

import io
from datetime import datetime, timezone
from decimal import Decimal

import openpyxl
import pytest

from app import tempo
from app.integracoes import feriados
from app.models import PapelUsuario

GERENTE, LEITOR = "gerente@empresa.com.br", "leitor@empresa.com.br"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())
    monkeypatch.setattr(tempo, "agora_utc", lambda: datetime(2026, 3, 20, 15, tzinfo=timezone.utc))


@pytest.fixture
def c(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    leitor = criar_usuario(LEITOR)
    h = {"gerente": login(GERENTE), "leitor": login(LEITOR)}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP", "data_inicio": "2026-01-01"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    base = f"/projetos/{p['id']}/orcamento"

    def item(descricao="Licenças", **dados):
        r = client.post(f"{base}/itens", json={"descricao": descricao, **dados}, headers=h["gerente"])
        assert r.status_code == 201, r.text
        return r.json()

    def lancar(item_id, valor, data="2026-02-10", **dados):
        return client.post(f"{base}/lancamentos", json={"item_id": item_id, "valor": valor, "data": data,
                                                         "descricao": "NF", **dados}, headers=h["gerente"])

    def resumo():
        return client.get(base, headers=h["leitor"]).json()

    return {"h": h["gerente"], "id": p["id"], "base": base, "item": item, "lancar": lancar, "resumo": resumo,
            "pilares": {x["nome"]: x["id"] for x in p["pilares"]}, "fases": [f["id"] for f in p["fases"]]}


def _d(v):
    return Decimal(str(v))


# ---------- Orçado mês a mês ----------


def test_item_com_meses_orcado_e_a_soma_e_o_planejado_segue_os_meses(client, c):
    i = c["item"]("Licenças", meses=[{"mes": "2026-04", "valor": "5000.00"}, {"mes": "2026-02", "valor": "10000.00"}])
    assert _d(i["valor_orcado"]) == 15000 and [m["mes"] for m in i["meses"]] == ["2026-02", "2026-04"]
    curva = {p["mes"]: p for p in c["resumo"]()["curva"]}
    assert _d(curva["2026-02"]["planejado_no_mes"]) == 10000 and _d(curva["2026-03"]["planejado_no_mes"]) == 0
    assert _d(curva["2026-04"]["planejado_acumulado"]) == 15000


def test_um_item_em_um_mes_e_outro_em_outro(client, c):
    c["item"]("Consultoria", meses=[{"mes": "2026-01", "valor": "8000.00"}])
    c["item"]("Hardware", categoria="capex", meses=[{"mes": "2026-03", "valor": "20000.00"}])
    curva = {p["mes"]: p for p in c["resumo"]()["curva"]}
    assert _d(curva["2026-01"]["planejado_no_mes"]) == 8000 and _d(curva["2026-02"]["planejado_no_mes"]) == 0
    assert _d(curva["2026-03"]["planejado_no_mes"]) == 20000


def test_editar_meses_e_voltar_ao_valor_unico(client, c):
    i = c["item"](valor_orcado="1000.00")
    url = f"{c['base']}/itens/{i['id']}"
    r = client.patch(url, json={"meses": [{"mes": "2026-05", "valor": "300.00"}, {"mes": "2026-06", "valor": "700.00"}],
                                "versao": i["versao"]}, headers=c["h"])
    assert r.status_code == 200 and _d(r.json()["valor_orcado"]) == 1000 and len(r.json()["meses"]) == 2
    assert r.json()["versao"] > i["versao"]  # bloqueio otimista: mudar só os meses também muda a versão
    # Salvar de novo com os mesmos meses (o diálogo sempre manda todos) e mudar um valor: atualiza no lugar.
    r = client.patch(url, json={"meses": [{"mes": "2026-05", "valor": "300.00"}, {"mes": "2026-06", "valor": "900.00"}]}, headers=c["h"])
    assert r.status_code == 200 and _d(r.json()["valor_orcado"]) == 1200
    r = client.patch(url, json={"meses": [{"mes": "2026-06", "valor": "700.00"}, {"mes": "2026-05", "valor": "300.00"}]}, headers=c["h"])
    assert r.status_code == 200 and _d(r.json()["valor_orcado"]) == 1000
    # Com meses, o total digitado é ignorado (o orçado é a soma).
    r = client.patch(url, json={"valor_orcado": "1.00"}, headers=c["h"])
    assert _d(r.json()["valor_orcado"]) == 1000
    r = client.patch(url, json={"meses": [], "valor_orcado": "2000.00"}, headers=c["h"])
    assert r.json()["meses"] == [] and _d(r.json()["valor_orcado"]) == 2000


@pytest.mark.parametrize("corpo", [
    {"descricao": "x"},  # nem valor nem meses
    {"descricao": "x", "meses": [{"mes": "2026-13", "valor": "1"}]},
    {"descricao": "x", "meses": [{"mes": "2026-01", "valor": "1"}, {"mes": "2026-01", "valor": "2"}]},
    {"descricao": "x", "meses": [{"mes": "2026-01", "valor": "-1"}]},
    {"descricao": "x", "meses": [{"mes": "1999-01", "valor": "1"}]},
])
def test_meses_invalidos(client, c, corpo):
    assert client.post(f"{c['base']}/itens", json=corpo, headers=c["h"]).status_code == 422


# ---------- Justificativa do estouro ----------


def test_gasto_que_estoura_exige_justificativa(client, c):
    i = c["item"](valor_orcado="1000.00")
    assert c["lancar"](i["id"], "800.00").status_code == 201  # dentro do orçado: sem justificativa
    r = c["lancar"](i["id"], "300.00")
    assert r.status_code == 422 and "Explique por que está estourando" in r.json()["detail"]
    assert c["lancar"](i["id"], "300.00", justificativa="   ").status_code == 422  # só espaços não vale
    r = c["lancar"](i["id"], "300.00", justificativa="Reajuste de 10% do fornecedor")
    assert r.status_code == 201 and r.json()["justificativa"] == "Reajuste de 10% do fornecedor"
    item = c["resumo"]()["itens"][0]
    assert item["estourado"] and item["justificativa"] == "Reajuste de 10% do fornecedor"
    # Estorno (diminui o gasto) nunca exige justificativa, mesmo com o item estourado.
    assert c["lancar"](i["id"], "-50.00").status_code == 201


def test_editar_lancamento_para_cima_exige_justificativa(client, c):
    i = c["item"](valor_orcado="1000.00")
    outro = c["item"]("Outro", valor_orcado="100.00")
    lanc = c["lancar"](i["id"], "900.00").json()
    url = f"{c['base']}/lancamentos/{lanc['id']}"
    assert client.patch(url, json={"valor": "950.00"}, headers=c["h"]).status_code == 200
    assert client.patch(url, json={"valor": "1200.00"}, headers=c["h"]).status_code == 422
    assert client.patch(url, json={"valor": "1200.00", "justificativa": "Escopo extra"}, headers=c["h"]).status_code == 200
    # Mudar para um item com orçado menor (100) também estoura aquele item.
    lanc2 = c["lancar"](i["id"], "-500.00").json()
    r = client.patch(f"{c['base']}/lancamentos/{lanc2['id']}", json={"item_id": outro["id"], "valor": "500.00"}, headers=c["h"])
    assert r.status_code == 422 and "“Outro” acima do orçado" in r.json()["detail"]


# ---------- Ajuste do valor gasto pelo item ----------


def test_ajustar_valor_gasto_cria_lancamento_de_ajuste(client, c):
    i = c["item"](valor_orcado="1000.00")
    c["lancar"](i["id"], "400.00")
    c["lancar"](i["id"], "200.00")
    url = f"{c['base']}/itens/{i['id']}"
    r = client.patch(url, json={"valor_realizado": "500.00"}, headers=c["h"])  # gastou 600: corrige para 500
    assert r.status_code == 200 and _d(r.json()["valor_realizado"]) == 500
    lancs = client.get(f"{c['base']}/lancamentos", headers=c["h"]).json()["itens"]
    ajuste = lancs[0]
    assert _d(ajuste["valor"]) == -100 and ajuste["descricao"] == "Ajuste do valor gasto: de R$ 600,00 para R$ 500,00"
    # Subir acima do orçado exige justificativa; o mesmo valor não cria nada.
    assert client.patch(url, json={"valor_realizado": "1500.00"}, headers=c["h"]).status_code == 422
    r = client.patch(url, json={"valor_realizado": "1500.00", "justificativa": "Horas extras"}, headers=c["h"])
    assert r.status_code == 200 and r.json()["justificativa"] == "Horas extras"
    total = client.get(f"{c['base']}/lancamentos", headers=c["h"]).json()["total"]
    client.patch(url, json={"valor_realizado": "1500.00"}, headers=c["h"])
    assert client.get(f"{c['base']}/lancamentos", headers=c["h"]).json()["total"] == total
    historico = client.get(f"/projetos/{c['id']}/historico", headers=c["h"]).json()
    assert any(h["acao"] == "orcamento.gasto_ajustado" for h in historico["itens"])


# ---------- Projetado (gastos futuros) ----------


def test_projetado_fica_fora_do_realizado_e_entra_na_previsao(client, c):
    capex = c["item"]("Servidores", categoria="capex", valor_orcado="10000.00")
    opex = c["item"]("Consultoria", categoria="opex", valor_orcado="5000.00")
    c["lancar"](capex["id"], "4000.00")
    assert c["lancar"](capex["id"], "9000.00", data="2026-06-10", projetado=True).status_code == 201  # sem justificativa
    c["lancar"](opex["id"], "1000.00", data="2026-07-01", projetado=True)
    r = c["resumo"]()
    assert _d(r["total_realizado"]) == 4000 and _d(r["total_projetado"]) == 10000 and _d(r["previsao_final"]) == 14000
    cat = {t["chave"]: t for t in r["por_categoria"]}
    assert _d(cat["capex"]["realizado"]) == 4000 and _d(cat["capex"]["projetado"]) == 9000
    assert _d(cat["opex"]["realizado"]) == 0 and _d(cat["opex"]["projetado"]) == 1000
    servidores = next(i for i in r["itens"] if i["id"] == capex["id"])
    assert _d(servidores["previsao"]) == 13000 and not servidores["estourado"]
    curva = {p["mes"]: p for p in r["curva"]}
    assert _d(curva["2026-07"]["previsto_acumulado"]) == 14000 and _d(curva["2026-07"]["realizado_acumulado"]) == 4000
    # Listas separadas: realizados (padrão) x projetados (os mais próximos primeiro).
    realizados = client.get(f"{c['base']}/lancamentos", headers=c["h"]).json()
    projetados = client.get(f"{c['base']}/lancamentos?projetado=true", headers=c["h"]).json()
    assert realizados["total"] == 1 and [p["data"] for p in projetados["itens"]] == ["2026-06-10", "2026-07-01"]


def test_alerta_quando_a_previsao_passa_do_orcado(client, c):
    i = c["item"](valor_orcado="1000.00")
    c["lancar"](i["id"], "800.00")
    c["lancar"](i["id"], "700.00", data="2026-05-01", projetado=True)
    alertas = [a["mensagem"] for a in c["resumo"]()["alertas"]]
    assert any("Com os gastos projetados (R$ 700,00), o projeto deve fechar em R$ 1.500,00, 50% acima do orçado." == a
               for a in alertas)


def test_confirmar_projetado(client, c):
    i = c["item"](valor_orcado="1000.00")
    proj = c["lancar"](i["id"], "600.00", data="2026-06-01", projetado=True).json()
    url = f"{c['base']}/lancamentos/{proj['id']}/confirmar"
    r = client.post(url, json={"valor": "650.00"}, headers=c["h"])
    assert r.status_code == 200 and not r.json()["projetado"] and r.json()["data"] == "2026-03-20"  # hoje
    assert _d(c["resumo"]()["total_realizado"]) == 650 and _d(c["resumo"]()["total_projetado"]) == 0
    assert client.post(url, json={}, headers=c["h"]).status_code == 409  # já é realizado
    # Confirmar um projetado que estoura o item também pede a justificativa.
    proj = c["lancar"](i["id"], "500.00", data="2026-06-01", projetado=True).json()
    url = f"{c['base']}/lancamentos/{proj['id']}/confirmar"
    assert client.post(url, json={"data": "2026-03-15"}, headers=c["h"]).status_code == 422
    r = client.post(url, json={"data": "2026-03-15", "justificativa": "Licença adicional"}, headers=c["h"])
    assert r.status_code == 200 and r.json()["data"] == "2026-03-15" and r.json()["justificativa"] == "Licença adicional"


def test_projetado_nao_conta_no_pilar_nem_na_curva_s(client, c):
    t = client.post(f"/projetos/{c['id']}/fases/{c['fases'][0]}/tarefas",
                    json={"descricao": "T", "data_inicio": "2026-01-05", "data_fim": "2026-06-30"}, headers=c["h"])
    assert t.status_code == 201
    i = c["item"](valor_orcado="1000.00", pilar_id=c["pilares"]["TI"])
    c["lancar"](i["id"], "100.00")
    c["lancar"](i["id"], "800.00", data="2026-05-01", projetado=True)
    pilares = client.get(f"/projetos/{c['id']}/pilares", headers=c["h"]).json()
    ti = next(p for p in pilares if p["id"] == c["pilares"]["TI"])
    assert _d(ti["valor_realizado"]) == 100
    curva = client.get(f"/projetos/{c['id']}/curva-s", headers=c["h"]).json()
    assert _d(curva["realizado_rs_hoje"]) == 100


def test_excel_do_projeto_traz_a_parte_financeira(client, c):
    i = c["item"]("Licenças", meses=[{"mes": "2026-02", "valor": "1000.00"}])
    c["lancar"](i["id"], "1200.00", justificativa="Câmbio")
    c["lancar"](i["id"], "300.00", data="2026-08-01", projetado=True)
    r = client.get(f"/projetos/{c['id']}/exportar", headers=c["h"])
    livro = openpyxl.load_workbook(io.BytesIO(r.content))
    assert livro.sheetnames[0] == "Cronograma"  # a importação lê a 1ª aba: continua reimportável
    assert {"Resumo do orçamento", "Itens do orçamento", "Mês a mês", "Orçado por mês", "Lançamentos"} <= set(livro.sheetnames)
    itens = list(livro["Itens do orçamento"].iter_rows(values_only=True))
    assert itens[1][3:7] == (1000, 1200, 300, 1500) and itens[1][9] == "Câmbio"
    lancs = list(livro["Lançamentos"].iter_rows(values_only=True))
    assert [linha[1] for linha in lancs[1:]] == ["Realizado", "Projetado"]
    resumo = {linha[0]: linha for linha in livro["Resumo do orçamento"].iter_rows(values_only=True) if linha and linha[0]}
    assert resumo["Previsão final (realizado + projetado)"][1] == 1500
    assert resumo["OPEX"][1:5] == (1000, 1200, 300, 1500)


def test_excel_sem_orcamento_so_tem_o_cronograma(client, c):
    r = client.get(f"/projetos/{c['id']}/exportar", headers=c["h"])
    assert openpyxl.load_workbook(io.BytesIO(r.content)).sheetnames == ["Cronograma"]


def test_slide_de_orcamento_traz_projetado_e_capex_opex(client, c):
    from pptx import Presentation

    i = c["item"]("Servidores", categoria="capex", valor_orcado="10000.00")
    c["lancar"](i["id"], "4000.00")
    c["lancar"](i["id"], "3000.00", data="2026-08-01", projetado=True)
    r = client.post(f"/projetos/{c['id']}/apresentacao", json={"slides": [{"tipo": "orcamento"}]}, headers=c["h"])
    assert r.status_code == 200, r.text
    prs = Presentation(io.BytesIO(r.content))
    textos = [f.text_frame.text for f in prs.slides[0].shapes if f.has_text_frame]
    junto = " ".join(textos)
    assert "Projetado (futuro)" in junto and "R$ 3.000,00" in junto and "Previsão final" in junto and "R$ 7.000,00" in junto
    assert "CAPEX: realizado R$ 4.000,00 + projetado R$ 3.000,00 = R$ 7.000,00 de R$ 10.000,00" in junto
    for forma in prs.slides[0].shapes:  # nada sai do slide
        assert forma.left >= 0 and forma.top >= 0 and forma.left + forma.width <= prs.slide_width, forma.name
        assert forma.top + forma.height <= prs.slide_height, forma.name
