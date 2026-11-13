"""Relatório do projeto em PDF: conteúdo, permissões, segurança da marcação e projetos grandes."""

import io
import time
from datetime import datetime, timezone

import pytest
from pypdf import PdfReader

from app import relatorio, tempo
from app.integracoes import feriados
from app.models import PapelUsuario

GERENTE, LEITOR, INTRUSO = "gerente@empresa.com.br", "leitor@empresa.com.br", "intruso@empresa.com.br"


@pytest.fixture(autouse=True)
def sem_internet(monkeypatch):
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())


@pytest.fixture
def cenario(client, criar_usuario, login, monkeypatch):
    monkeypatch.setattr(tempo, "agora_utc", lambda: datetime(2026, 3, 20, 15, 30, tzinfo=timezone.utc))
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    leitor = criar_usuario(LEITOR)
    criar_usuario(INTRUSO, PapelUsuario.GERENTE)
    h = {"gerente": login(GERENTE), "leitor": login(LEITOR), "intruso": login(INTRUSO)}
    p = client.post("/projetos/manual", json={
        "nome_projeto": "Implantação do ERP Financeiro", "orcamento_aprovado": "1500000.00", "data_inicio": "2026-03-02",
        "fases": [
            {"fase": "Iniciação", "tarefas": [{"descricao": "Termo de Abertura", "duracao_dias_uteis": 3}]},
            {"fase": "Execução", "tarefas": [{"descricao": "Migrar dados contábeis", "duracao_dias_uteis": 10}]},
        ],
    }, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    ti = p["pilares"][0]["id"]
    capex = p["pilares"][2]["id"]
    client.patch(f"/projetos/{p['id']}/pilares/{ti}", json={"farol": "amarelo", "status_texto": "Fornecedor atrasou a VPN."},
                 headers=h["gerente"])
    client.patch(f"/projetos/{p['id']}/pilares/{capex}", json={"farol": "vermelho", "valor_orcado": "800000",
                 "valor_realizado": "950000.50"}, headers=h["gerente"])
    tarefa = p["fases"][0]["tarefas"][0]
    client.patch(f"/projetos/{p['id']}/tarefas/{tarefa['id']}", json={"percentual_conclusao": 100, "pilar_id": ti},
                 headers=h["gerente"])
    client.post(f"/projetos/{p['id']}/comentarios", json={"texto": "Aprovado pelo comitê.", "tarefa_id": tarefa["id"]},
                headers=h["leitor"])
    return {"h": h, "id": p["id"]}


def _texto(conteudo: bytes) -> str:
    leitor = PdfReader(io.BytesIO(conteudo))
    # Espaços normalizados: a quebra de linha de uma célula não pode separar o trecho procurado.
    return " ".join(" ".join(pagina.extract_text() for pagina in leitor.pages).split())


def _pdf(client, cenario, quem="gerente"):
    return client.get(f"/projetos/{cenario['id']}/relatorio", headers=cenario["h"][quem])


def test_pdf_tem_todas_as_secoes(client, cenario):
    r = _pdf(client, cenario)
    assert r.status_code == 200, r.text
    assert r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF")
    texto = _texto(r.content)
    for trecho in [
        "Implantação do ERP Financeiro", "gerado em 20/03/2026 às 12:30",  # 15h30 UTC = 12h30 em Brasília
        "Resumo", "Planejado", "R$ 1.500.000,00", "Andamento por fase",
        "Pilares", "TI", "Suprimentos", "CAPEX", "Pessoas", "Amarelo", "Vermelho",
        "Fornecedor atrasou a VPN.", "R$ 950.000,50", "+R$ 150.000,50",
        "Linha do tempo", "Termo de Abertura", "Migrar dados contábeis",
        "Histórico recente", "criou o projeto", "atualizou um pilar",
        "pilar: de vazio para TI",  # o nome do pilar, não o id
        "realizado: de vazio para R$ 950.000,50",  # dinheiro formatado
        "situação: de vazio para Fornecedor",
        "Comentários recentes", "Aprovado pelo comitê.", "Na tarefa “Termo de Abertura”",
    ]:
        assert trecho in texto, f"faltou no PDF: {trecho!r}"


def test_nome_do_arquivo_e_auditoria(client, cenario):
    r = _pdf(client, cenario, "leitor")
    assert "filename*=UTF-8''Relat%C3%B3rio%20-%20Implanta%C3%A7%C3%A3o" in r.headers["content-disposition"]
    historico = client.get(f"/projetos/{cenario['id']}/historico", headers=cenario["h"]["gerente"]).json()["itens"]
    assert historico[0]["acao"] == "projeto.relatorio_pdf"


def test_visualizador_gera_e_quem_nao_e_membro_nao(client, cenario):
    assert _pdf(client, cenario, "leitor").status_code == 200
    r = _pdf(client, cenario, "intruso")
    assert r.status_code == 404 and r.json() == {"detail": "Projeto não encontrado."}


def test_marcacao_digitada_nao_vira_comando_no_pdf(client, cenario):
    """O ReportLab entende <img src=...>, <font>, <a href=...>: texto do usuário tem de sair literal."""
    maliciosos = [
        '<img src="/etc/passwd" width="10" height="10"/>',
        "<font size=200>GRANDE</font>",
        '<a href="javascript:alert(1)">clique</a>',
        "Fim de linha & <b>negrito</b> < > \" '",
    ]
    for texto in maliciosos:
        assert client.post(f"/projetos/{cenario['id']}/comentarios", json={"texto": texto},
                           headers=cenario["h"]["gerente"]).status_code == 201
    pilar = client.get(f"/projetos/{cenario['id']}/pilares", headers=cenario["h"]["gerente"]).json()[1]
    client.patch(f"/projetos/{cenario['id']}/pilares/{pilar['id']}", json={"status_texto": "<b>x</b><img src='/etc/hosts'/>"},
                 headers=cenario["h"]["gerente"])
    client.patch(f"/projetos/{cenario['id']}", json={"nome_projeto": "Projeto <i>teste</i> & cia"}, headers=cenario["h"]["gerente"])

    r = _pdf(client, cenario)
    assert r.status_code == 200, r.text
    texto = _texto(r.content)
    assert "<font size=200>GRANDE</font>" in texto
    assert "<b>negrito</b>" in texto
    assert "Projeto <i>teste</i> & cia" in texto
    assert b"/Subtype /Image" not in r.content and b"/Subtype/Image" not in r.content  # nenhuma imagem embutida
    assert b"javascript" not in r.content.lower() or b"/URI" not in r.content


def test_emoji_e_textos_estranhos_nao_quebram(client, cenario):
    for texto in ["Entrega ok 🚀🎉", "日本語のテキスト", "Tab\tlinha\nnova\r\x00\x07", "→ ✓ ★", "ok"]:
        client.post(f"/projetos/{cenario['id']}/comentarios", json={"texto": texto}, headers=cenario["h"]["gerente"])
    r = _pdf(client, cenario)
    assert r.status_code == 200
    assert "Entrega ok ??" in _texto(r.content)  # emoji fora da fonte vira "?" (e não um PDF quebrado)


def test_projeto_vazio(client, criar_usuario, login):
    criar_usuario("solo@empresa.com.br", PapelUsuario.GERENTE)
    h = login("solo@empresa.com.br")
    p = client.post("/projetos/manual", json={"nome_projeto": "Vazio"}, headers=h).json()
    for pilar in p["pilares"]:
        client.delete(f"/projetos/{p['id']}/pilares/{pilar['id']}", headers=h)
    r = client.get(f"/projetos/{p['id']}/relatorio", headers=h)
    assert r.status_code == 200
    texto = _texto(r.content)
    assert "Nenhuma tarefa com início e término definidos." in texto
    assert "Este projeto não tem pilares." in texto
    assert "Nenhum comentário." in texto


def test_projeto_grande_gera_rapido_e_resume_a_linha_do_tempo(client, criar_usuario, login):
    criar_usuario("grande@empresa.com.br", PapelUsuario.GERENTE)
    h = login("grande@empresa.com.br")
    fases = [{"fase": f, "tarefas": [{"descricao": f"Tarefa {f[:4]} {i} com uma descrição bem comprida para cortar no Gantt",
                                      "duracao_dias_uteis": 2} for i in range(60)]}
             for f in ("Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento")]
    p = client.post("/projetos/manual", json={"nome_projeto": "Grande", "data_inicio": "2026-01-05", "fases": fases},
                    headers=h).json()
    inicio = time.perf_counter()
    r = client.get(f"/projetos/{p['id']}/relatorio", headers=h)
    assert r.status_code == 200
    assert time.perf_counter() - inicio < 10
    texto = _texto(r.content)
    assert "+240 tarefa(s) não aparecem aqui" in texto  # 300 tarefas, 60 no Gantt
    assert len(PdfReader(io.BytesIO(r.content)).pages) >= 3


def test_limite_por_pessoa(client, cenario):
    for _ in range(10):
        assert _pdf(client, cenario).status_code == 200
    assert _pdf(client, cenario).status_code == 429
    assert _pdf(client, cenario, "leitor").status_code == 200  # a cota é por pessoa


def test_texto_limpo_unitario():
    assert relatorio._limpo("Ação 🚀\x00") == "Ação ?"
    assert relatorio._limpo(None) == ""
    paragrafo = relatorio._p("<img src='/etc/passwd'/>", relatorio.ESTILOS["normal"])
    assert "&lt;img" in paragrafo.text
