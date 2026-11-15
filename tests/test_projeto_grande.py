"""
Projeto grande (dezenas de milhares de tarefas, agora possíveis com a importação em fila):
o detalhe não pode virar um JSON de 23 MB nem os indicadores carregarem todas as tarefas.
"""

import io
import json
import random
from datetime import date, datetime, timedelta, timezone

import openpyxl
import pytest
from pypdf import PdfReader

from app import tempo
from app.models import PapelUsuario
from tests.conftest import postar_importacao

GERENTE = "gerente@empresa.com.br"
FASES = ["Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento"]
HOJE = date(2026, 3, 20)


@pytest.fixture(autouse=True)
def relogio(monkeypatch):
    monkeypatch.setattr(tempo, "agora_utc", lambda: datetime(2026, 3, 20, 15, tzinfo=timezone.utc))


def _gerar_linhas(n_execucao: int, semente: int = 7):
    """Mistura de fases, percentuais, datas (passadas e futuras) e pilares, reprodutível."""
    aleatorio = random.Random(semente)
    linhas = []
    for i in range(n_execucao + 40):
        fase = "Execução" if i < n_execucao else FASES[i % 5]
        inicio = date(2026, 1, 5) + timedelta(days=aleatorio.randint(0, 120))
        fim = inicio + timedelta(days=aleatorio.randint(0, 30))
        com_datas = aleatorio.random() < 0.8
        linhas.append({
            "fase": fase, "descricao": f"T{i}", "percentual": aleatorio.choice([0, 10, 50, 90, 100, 100]),
            "inicio": inicio if com_datas else None, "fim": fim if com_datas else None,
            "pilar": aleatorio.choice(["TI", "CAPEX", "Pessoas", ""]),
        })
    return linhas


def _csv(linhas) -> bytes:
    corpo = "Fase;Descrição;% Conclusão;Data Início;Data Fim;Pilar\n" + "".join(
        f"{l['fase']};{l['descricao']};{l['percentual']};{l['inicio'] or ''};{l['fim'] or ''};{l['pilar']}\n" for l in linhas
    )
    return corpo.encode()


@pytest.fixture
def grande(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    linhas = _gerar_linhas(450)
    r = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Grande"},
                          files={"arquivo": ("g.csv", _csv(linhas))}, headers=h)
    assert r.status_code == 201, r.text
    return {"h": h, "projeto": r.json()["projeto"], "linhas": linhas}


def test_detalhe_traz_200_por_fase_e_o_total(client, grande):
    projeto = client.get(f"/projetos/{grande['projeto']['id']}", headers=grande["h"]).json()
    execucao = next(f for f in projeto["fases"] if f["titulo"] == "Execução")
    assert (len(execucao["tarefas"]), execucao["total_tarefas"]) == (200, 458)
    outras = [f for f in projeto["fases"] if f["titulo"] != "Execução"]
    assert all(len(f["tarefas"]) == f["total_tarefas"] == 8 for f in outras)
    assert [t["descricao"] for t in execucao["tarefas"][:3]] == ["T0", "T1", "T2"]  # na ordem de criação


def test_paginacao_e_filtros_do_cronograma(client, grande):
    pid, h = grande["projeto"]["id"], grande["h"]
    execucao = next(f for f in grande["projeto"]["fases"] if f["titulo"] == "Execução")["id"]
    # 200 por página: o mesmo tamanho que o front usa no "Carregar mais".
    pagina = client.get(f"/projetos/{pid}/tarefas?fase_id={execucao}&limite=200&offset=200", headers=h)
    assert pagina.status_code == 200, pagina.text
    pagina = pagina.json()
    assert pagina["total"] == 458 and len(pagina["itens"]) == 200
    assert [t["descricao"] for t in pagina["itens"][:2]] == ["T200", "T201"]
    assert client.get(f"/projetos/{pid}/tarefas?limite=501", headers=h).status_code == 422

    ti = next(p["id"] for p in grande["projeto"]["pilares"] if p["nome"] == "TI")
    esperado_ti = sum(1 for l in grande["linhas"] if l["pilar"] == "TI")
    esperado_sem = sum(1 for l in grande["linhas"] if not l["pilar"])
    assert client.get(f"/projetos/{pid}/tarefas?pilar_id={ti}&limite=1", headers=h).json()["total"] == esperado_ti
    assert client.get(f"/projetos/{pid}/tarefas?sem_pilar=true&limite=1", headers=h).json()["total"] == esperado_sem
    assert client.get(f"/projetos/{pid}/tarefas?limite=1", headers=h).json()["total"] == len(grande["linhas"])


def test_indicadores_no_sql_batem_com_a_conta_de_referencia(client, grande):
    """A mesma conta feita à mão, em Python, sobre todas as linhas da planilha."""
    linhas = grande["linhas"]

    def media(ls):
        return round(sum(l["percentual"] for l in ls) / len(ls), 1) if ls else 0.0

    def atrasada(l):
        return l["fim"] is not None and l["fim"] < HOJE and l["percentual"] < 100

    ind = client.get(f"/projetos/{grande['projeto']['id']}/indicadores", headers=grande["h"]).json()
    assert ind["total_tarefas"] == len(linhas)
    assert ind["percentual_conclusao"] == media(linhas)
    assert ind["tarefas_concluidas"] == sum(l["percentual"] == 100 for l in linhas)
    assert ind["tarefas_atrasadas"] == sum(map(atrasada, linhas))
    assert ind["data_inicio_prevista"] == min(l["inicio"] for l in linhas if l["inicio"]).isoformat()
    assert ind["data_fim_prevista"] == max(l["fim"] for l in linhas if l["fim"]).isoformat()
    for f in ind["fases"]:
        da_fase = [l for l in linhas if l["fase"] == f["titulo"]]
        assert (f["total_tarefas"], f["percentual_conclusao"], f["tarefas_atrasadas"]) == (
            len(da_fase), media(da_fase), sum(map(atrasada, da_fase)))

    pilares = {p["nome"]: p for p in client.get(f"/projetos/{grande['projeto']['id']}/pilares", headers=grande["h"]).json()}
    for nome in ("TI", "CAPEX", "Pessoas"):
        do_pilar = [l for l in linhas if l["pilar"] == nome]
        assert (pilares[nome]["total_tarefas"], pilares[nome]["percentual_conclusao"],
                pilares[nome]["tarefas_atrasadas"]) == (len(do_pilar), media(do_pilar), sum(map(atrasada, do_pilar)))


def test_fase_atual_e_a_primeira_com_pendencia(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    csv = "Fase;Descrição;% Conclusão\nIniciação;A;100\nPlanejamento;B;100\nExecução;C;40\nEncerramento;D;0\n".encode()
    r = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Fases"}, files={"arquivo": ("f.csv", csv)}, headers=h)
    ind = client.get(f"/projetos/{r.json()['projeto']['id']}/indicadores", headers=h).json()
    assert ind["fase_atual"] == "Execução"


def test_detalhe_de_projeto_enorme_tem_tamanho_limitado(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    csv = ("Fase;Descrição\n" + "".join(f"{FASES[i % 5]};Tarefa de número {i}\n" for i in range(5_000))).encode()
    r = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Enorme"}, files={"arquivo": ("e.csv", csv)}, headers=h)
    detalhe = client.get(f"/projetos/{r.json()['projeto']['id']}", headers=h)
    assert len(detalhe.content) < 300_000, len(detalhe.content)  # 1.000 tarefas embutidas, não 5.000
    assert sum(f["total_tarefas"] for f in detalhe.json()["fases"]) == 5_000


def test_pdf_de_projeto_grande_resume_o_gantt(client, grande):
    r = client.get(f"/projetos/{grande['projeto']['id']}/relatorio", headers=grande["h"])
    texto = " ".join(" ".join(p.extract_text() for p in PdfReader(io.BytesIO(r.content)).pages).split())
    com_datas = sum(1 for l in grande["linhas"] if l["inicio"])
    assert f"+{com_datas - 60} tarefa(s) não aparecem aqui" in texto


def test_exportacao_em_lotes_traz_todas_as_tarefas_na_ordem(client, grande, monkeypatch):
    """A exportação lê o banco em lotes: lotes pequenos não podem perder nem repetir tarefa."""
    from app import services

    monkeypatch.setattr(services, "LOTE_EXPORTACAO", 7)
    r = client.get(f"/projetos/{grande['projeto']['id']}/exportar", headers=grande["h"])
    assert r.status_code == 200
    planilha = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True)["Cronograma"]
    cabecalho, *linhas = planilha.iter_rows(values_only=True)
    assert cabecalho == tuple(services.COLUNAS_EXPORTACAO)

    # Ordem esperada: fases na sequência do Waterfall; dentro da fase, a ordem da importação.
    esperado = [
        (l["fase"], l["descricao"], l["inicio"], l["fim"], None, l["percentual"], l["pilar"] or None)
        for fase in FASES for l in grande["linhas"] if l["fase"] == fase
    ]
    # O modo contínuo não grava células vazias no fim da linha (o Excel, o pandas e a nossa
    # importação completam a linha): aqui também.
    completas = [tuple(linha) + (None,) * (len(cabecalho) - len(linha)) for linha in linhas]
    lidas = [(f, d, i and i.date(), fim and fim.date(), resp, pct, pil) for f, d, i, fim, resp, pct, pil in completas]
    assert lidas == esperado

    # E a planilha grande volta pela importação sem perder nada.
    de_volta = postar_importacao(client, "/projetos/importar", data={"nome_projeto": "Grande (cópia)"},
                                 files={"arquivo": ("g.xlsx", r.content)}, headers=grande["h"])
    assert de_volta.status_code == 201, de_volta.text
    assert de_volta.json()["total_tarefas_importadas"] == len(esperado)


def test_rota_paginada_respeita_permissoes(client, grande, criar_usuario, login):
    criar_usuario("intruso@empresa.com.br", PapelUsuario.GERENTE)
    r = client.get(f"/projetos/{grande['projeto']['id']}/tarefas", headers=login("intruso@empresa.com.br"))
    assert r.status_code == 404
    assert json.loads(r.text) == {"detail": "Projeto não encontrado."}
