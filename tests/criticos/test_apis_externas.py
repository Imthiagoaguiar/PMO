"""
Cenário 2 — APIs externas fora do ar (unittest.mock).

- Brasil API (feriados): falhar NÃO pode travar o cronograma. O cálculo segue só com fins de
  semana, a falha fica em cache por 5 minutos (para não pagar o timeout em cada requisição)
  e a API volta a ser consultada depois disso.
- Groq (IA): cada falha vira um status HTTP claro (502/503/504), sem vazar detalhe interno
  (mensagem do provedor, chave, stack trace) e sem gravar nada.
"""

import json
from datetime import date
from unittest import mock

import groq
import httpx
import pytest

from app.integracoes import feriados
from app.models import Auditoria, Projeto
from tests.criticos.conftest import SEGUNDA, contar, resposta_groq, resposta_http

TEXTO_TAP = "Termo de Abertura: implantar o novo ERP financeiro na matriz e nas 3 filiais até junho. " * 2
URL_GROQ = "https://api.groq.com/openai/v1/chat/completions"


def _projeto_com_cronograma(client, headers, inicio: date, duracoes):
    tarefas = [{"descricao": f"T{i}", "duracao_dias_uteis": d} for i, d in enumerate(duracoes)]
    return client.post("/projetos/manual", json={
        "nome_projeto": "ERP", "data_inicio": inicio.isoformat(),
        "fases": [{"fase": "Execução", "tarefas": tarefas}],
    }, headers=headers)


# ======================= Brasil API =======================

FALHAS_BRASIL_API = [
    pytest.param(httpx.ConnectTimeout("timeout ao conectar"), id="timeout-conexao"),
    pytest.param(httpx.ReadTimeout("timeout de leitura"), id="timeout-leitura"),
    pytest.param(httpx.ConnectError("DNS falhou"), id="sem-rede"),
    pytest.param(resposta_http(500, json={"message": "Internal Server Error"}), id="erro-500"),
    pytest.param(resposta_http(503, text="Service Unavailable"), id="erro-503"),
    pytest.param(resposta_http(429, json={"message": "Too Many Requests"}), id="erro-429"),
    pytest.param(resposta_http(200, text="<html>manutenção</html>"), id="200-com-html"),
    pytest.param(resposta_http(200, json={"feriados": []}), id="200-objeto-em-vez-de-lista"),
    pytest.param(resposta_http(200, json=[{"name": "Natal"}]), id="200-item-sem-data"),
    pytest.param(resposta_http(200, json=[{"date": "25/12/2026"}]), id="200-data-em-outro-formato"),
    pytest.param(resposta_http(200, json=[{"date": None}]), id="200-data-nula"),
    pytest.param(resposta_http(200, json=None), id="200-null"),
]


@pytest.mark.parametrize("falha", FALHAS_BRASIL_API)
def test_brasil_api_falhando_nao_impede_criar_o_cronograma(client, gerente, brasil_api, falha):
    brasil_api.side_effect = falha if isinstance(falha, Exception) else (lambda url, **_: falha)

    # 17/04/2026 é sexta; 21/04 (Tiradentes) seria pulado se a API respondesse.
    r = _projeto_com_cronograma(client, gerente, date(2026, 4, 17), [3])
    assert r.status_code == 201, r.text
    tarefa = r.json()["fases"][2]["tarefas"][0]
    # Sem feriados: sex 17, seg 20, ter 21 (só fins de semana pulados).
    assert (tarefa["data_inicio"], tarefa["data_fim"]) == ("2026-04-17", "2026-04-21")


def test_com_a_brasil_api_no_ar_o_feriado_e_pulado(client, gerente, brasil_api):
    tarefa = _projeto_com_cronograma(client, gerente, date(2026, 4, 17), [3]).json()["fases"][2]["tarefas"][0]
    assert tarefa["data_fim"] == "2026-04-22"  # sex 17, seg 20, (21 feriado), qua 22


def test_chamada_a_brasil_api_sempre_tem_timeout(client, gerente, brasil_api):
    _projeto_com_cronograma(client, gerente, SEGUNDA, [2])
    assert brasil_api.called
    for chamada in brasil_api.call_args_list:
        assert 0 < chamada.kwargs["timeout"] <= 10  # nunca espera indefinidamente


def test_falha_fica_em_cache_e_a_api_volta_a_ser_consultada_depois(client, gerente, brasil_api):
    brasil_api.side_effect = httpx.ReadTimeout("lento")
    relogio = [1_000.0]
    with mock.patch.object(feriados.time, "monotonic", side_effect=lambda: relogio[0]):
        for _ in range(3):  # 3 projetos seguidos: só a 1ª requisição paga o timeout
            assert _projeto_com_cronograma(client, gerente, SEGUNDA, [2]).status_code == 201
        assert brasil_api.call_count == 1

        relogio[0] += feriados.NOVA_TENTATIVA_APOS_FALHA_SEGUNDOS + 1
        brasil_api.side_effect = lambda url, **_: resposta_http(200, url=url, json=[])
        assert _projeto_com_cronograma(client, gerente, SEGUNDA, [2]).status_code == 201
        assert brasil_api.call_count == 2  # tentou de novo depois da janela


def test_falha_num_ano_nao_contamina_outro(client, gerente, brasil_api):
    def _so_2027_falha(url, **_):
        if url.endswith("/2027"):
            raise httpx.ReadTimeout("lento")
        return resposta_http(200, url=url, json=[{"date": "2026-12-25", "name": "Natal"}])

    brasil_api.side_effect = _so_2027_falha
    # 24/12/2026 (qui) + 4 dias úteis: 24, (25 Natal), 28, 29, 30 -> atravessa para 2027 sem quebrar.
    r = _projeto_com_cronograma(client, gerente, date(2026, 12, 24), [4, 3])
    assert r.status_code == 201, r.text
    tarefas = r.json()["fases"][2]["tarefas"]
    assert tarefas[0]["data_fim"] == "2026-12-30"
    assert tarefas[1]["data_inicio"] == "2026-12-31"


def test_brasil_api_fora_do_ar_na_edicao_da_tarefa(client, gerente, brasil_api):
    projeto = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=gerente).json()
    fase_id = projeto["fases"][0]["id"]
    tarefa = client.post(f"/projetos/{projeto['id']}/fases/{fase_id}/tarefas",
                         json={"descricao": "TAP", "data_inicio": "2026-03-02"}, headers=gerente).json()
    brasil_api.side_effect = httpx.ConnectTimeout("fora")
    r = client.patch(f"/projetos/{projeto['id']}/tarefas/{tarefa['id']}",
                     json={"duracao_dias_uteis": 5}, headers=gerente)
    assert r.status_code == 200, r.text
    assert r.json()["data_fim"] == "2026-03-06"


# ======================= Groq =======================

_REQ = httpx.Request("POST", URL_GROQ)


def _erro_status(classe, status: int):
    return classe("mensagem interna do provedor: org_123 sem créditos",
                  response=httpx.Response(status, request=_REQ), body=None)


FALHAS_GROQ = [
    pytest.param(groq.APITimeoutError(request=_REQ), 504, id="timeout"),
    pytest.param(groq.APIConnectionError(request=_REQ), 502, id="sem-conexao"),
    pytest.param(_erro_status(groq.InternalServerError, 500), 502, id="erro-500"),
    pytest.param(_erro_status(groq.InternalServerError, 503), 502, id="erro-503"),
    pytest.param(_erro_status(groq.RateLimitError, 429), 503, id="limite-429"),
    pytest.param(_erro_status(groq.AuthenticationError, 401), 502, id="chave-invalida-401"),
    pytest.param(_erro_status(groq.NotFoundError, 404), 502, id="modelo-desativado-404"),
    pytest.param(_erro_status(groq.BadRequestError, 400), 502, id="requisicao-recusada-400"),
]


@pytest.mark.parametrize("excecao, status_esperado", FALHAS_GROQ)
def test_groq_falhando_vira_erro_http_claro(client, gerente, groq_falso, db, excecao, status_esperado):
    groq_falso.comportamento = excecao
    r = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=gerente)

    assert r.status_code == status_esperado, r.text
    detalhe = r.json()["detail"]
    assert isinstance(detalhe, str) and detalhe
    for vazamento in ("org_123", "gsk_", "Traceback", "groq."):
        assert vazamento not in r.text
    assert contar(db, Projeto) == 0
    assert contar(db, Auditoria, Auditoria.acao == "projeto.sugestao_ia") == 0  # só registra uso que deu certo


RESPOSTAS_INUTEIS = [
    pytest.param(resposta_groq(None), id="conteudo-nulo"),
    pytest.param(resposta_groq(""), id="conteudo-vazio"),
    pytest.param(resposta_groq(None, sem_choices=True), id="sem-choices"),
    pytest.param(resposta_groq("Claro! Aqui está o cronograma: ..."), id="texto-em-vez-de-json"),
    pytest.param(resposta_groq('{"nome_projeto": "ERP", "tarefas": [{"fase": "Iniciação"'), id="json-cortado"),
    pytest.param(resposta_groq("[]"), id="lista"),
    pytest.param(resposta_groq("null"), id="null"),
    pytest.param(resposta_groq('{"nome_projeto": "ERP", "tarefas": "nenhuma"}'), id="tarefas-nao-e-lista"),
    pytest.param(resposta_groq('{"nome_projeto": "", "tarefas": [{"fase": "Iniciação", "descricao": "TAP"}]}'), id="sem-nome"),
    pytest.param(resposta_groq(json.dumps({"nome_projeto": "ERP", "tarefas": [
        {"fase": "Fase 6", "descricao": "X"}, {"fase": "Iniciação", "descricao": "   "}, "string solta", 42]})),
        id="so-tarefas-invalidas"),
]


@pytest.mark.parametrize("resposta", RESPOSTAS_INUTEIS)
def test_resposta_inutil_da_groq_vira_502(client, gerente, groq_falso, resposta):
    groq_falso.comportamento = resposta
    r = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=gerente)
    assert r.status_code == 502, r.text


def test_resposta_parcialmente_valida_descarta_o_lixo(client, gerente, groq_falso):
    groq_falso.comportamento = resposta_groq(json.dumps({"nome_projeto": "  ERP   Financeiro ", "tarefas": [
        {"fase": "EXECUCAO", "descricao": "Construir", "duracao_dias_uteis": "10"},
        {"fase": "Iniciação", "descricao": "TAP", "duracao_dias_uteis": -5},
        {"fase": "Iniciação", "descricao": "Kickoff", "duracao_dias_uteis": 10**9},
        {"fase": "Inexistente", "descricao": "Lixo"},
        {"fase": "Encerramento", "descricao": "x" * 5000, "duracao_dias_uteis": 2.7},
    ]}))
    r = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=gerente)
    assert r.status_code == 200, r.text
    corpo = r.json()
    assert corpo["nome_projeto"] == "ERP Financeiro"
    assert corpo["descartadas"] == 1
    fases = {f["fase"]: f["tarefas"] for f in corpo["fases"]}
    assert list(fases) == ["Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento"]
    assert fases["Execução"] == [{"descricao": "Construir", "duracao_dias_uteis": 10}]
    assert [t["duracao_dias_uteis"] for t in fases["Iniciação"]] == [None, None]  # absurdas viram "sem duração"
    assert len(fases["Encerramento"][0]["descricao"]) == 500


def test_cliente_groq_tem_timeout_e_poucas_retentativas(client, gerente, groq_falso):
    """Sem timeout, uma Groq travada prenderia um worker do servidor indefinidamente."""
    groq_falso.comportamento = groq.APITimeoutError(request=_REQ)
    client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=gerente)
    assert 0 < groq_falso.construido_com["timeout"] <= 120
    assert groq_falso.construido_com["max_retries"] <= 2


def test_falha_da_groq_ainda_conta_no_limite_por_pessoa(client, gerente, groq_falso):
    """Senão, alguém poderia martelar a Groq (que cobra por chamada) enquanto ela falha."""
    groq_falso.comportamento = groq.APITimeoutError(request=_REQ)
    status = [client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=gerente).status_code for _ in range(6)]
    assert status == [504] * 5 + [429]
