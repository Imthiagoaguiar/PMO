"""Integrações externas (Clerk, Brasil API, Groq, Resend), todas simuladas: nenhum teste usa a rede."""

import json
from datetime import date, timedelta
from typing import Any, Dict, List

import groq
import httpx
import jwt
import pytest
import resend
from cryptography.hazmat.primitives.asymmetric import rsa

from app.config import settings
from app.integracoes import clerk, feriados, ia
from app.models import PapelUsuario
from app.security import agora_utc

GERENTE, MEMBRO = "gerente@empresa.com.br", "membro@empresa.com.br"
EMISSOR_CLERK = "https://teste.clerk.accounts.dev"
TEXTO_TAP = "Termo de Abertura: implantar o novo ERP financeiro na matriz e nas 3 filiais até junho. " * 2


@pytest.fixture(autouse=True)
def _isolar_integracoes():
    feriados.limpar_cache()
    yield
    feriados.limpar_cache()


# ======================= Brasil API (feriados) =======================

FERIADOS_FALSOS = {
    2026: [{"date": "2026-04-21", "name": "Tiradentes", "type": "national"},
           {"date": "2026-05-01", "name": "Dia do Trabalho", "type": "national"}],
    2027: [{"date": "2027-01-01", "name": "Confraternização mundial", "type": "national"}],
}


@pytest.fixture
def brasil_api(monkeypatch) -> List[str]:
    chamadas: List[str] = []

    def _get(url: str, timeout: float) -> httpx.Response:
        chamadas.append(url)
        ano = int(url.rsplit("/", 1)[-1])
        return httpx.Response(200, json=FERIADOS_FALSOS.get(ano, []), request=httpx.Request("GET", url))

    monkeypatch.setattr(feriados.httpx, "get", _get)
    return chamadas


def test_data_fim_pula_fim_de_semana_e_feriado(brasil_api):
    # Sex 17/04, (sáb, dom), seg 20/04, (ter 21/04 Tiradentes), qua 22/04.
    assert feriados.calcular_data_fim(date(2026, 4, 17), 3) == date(2026, 4, 22)
    # Começando num feriado: o próprio dia não conta.
    assert feriados.calcular_data_fim(date(2026, 5, 1), 1) == date(2026, 5, 4)
    # Virada de ano busca os feriados do ano seguinte: 31/12 (qui), 01/01 feriado, fim de semana, 04 e 05/01.
    assert feriados.calcular_data_fim(date(2026, 12, 31), 3) == date(2027, 1, 5)
    assert len(brasil_api) == 2  # um request por ano, o resto vem do cache


def test_brasil_api_fora_do_ar_usa_so_fins_de_semana(monkeypatch):
    chamadas = []

    def _falha(url: str, timeout: float):
        chamadas.append(url)
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(feriados.httpx, "get", _falha)
    # Sem a API, Tiradentes não é pulado: sex 17, seg 20, ter 21.
    assert feriados.calcular_data_fim(date(2026, 4, 17), 3) == date(2026, 4, 21)
    feriados.calcular_data_fim(date(2026, 4, 17), 3)
    assert len(chamadas) == 1  # a falha fica em cache: não paga o timeout a cada tarefa


def test_tarefa_com_duracao_calcula_data_fim(client, criar_usuario, login, brasil_api):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    membro = criar_usuario(MEMBRO)
    h, hm = login(GERENTE), login(MEMBRO)
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": membro.id}, headers=h)
    url = f"/projetos/{p['id']}/fases/{p['fases'][0]['id']}/tarefas"

    r = client.post(url, json={"descricao": "Kickoff", "data_inicio": "2026-04-17", "duracao_dias_uteis": 3}, headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["data_fim"] == "2026-04-22"

    sem_inicio = {"descricao": "X", "duracao_dias_uteis": 3}
    os_dois = {"descricao": "X", "data_inicio": "2026-04-17", "data_fim": "2026-04-30", "duracao_dias_uteis": 3}
    assert client.post(url, json=sem_inicio, headers=h).status_code == 422
    assert client.post(url, json=os_dois, headers=h).status_code == 422

    url_t = f"/projetos/{p['id']}/tarefas/{r.json()['id']}"
    assert client.patch(url_t, json={"duracao_dias_uteis": 5}, headers=hm).status_code == 403  # membro
    r = client.patch(url_t, json={"duracao_dias_uteis": 1, "data_inicio": "2026-04-30"}, headers=h)
    assert (r.json()["data_inicio"], r.json()["data_fim"]) == ("2026-04-30", "2026-04-30")
    r = client.patch(url_t, json={"duracao_dias_uteis": 2}, headers=h)  # usa a data_inicio salva; pula 01/05
    assert r.json()["data_fim"] == "2026-05-04"


# ======================= Groq (IA) =======================


def _resposta_ia(monkeypatch, conteudo: Any) -> None:
    texto = conteudo if isinstance(conteudo, str) else json.dumps(conteudo)
    monkeypatch.setattr(ia, "_chamar_groq", lambda _texto: texto)


def test_ia_sugere_escopo_validado(client, criar_usuario, login, monkeypatch):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    _resposta_ia(monkeypatch, {
        "nome_projeto": "  Implantação ERP Financeiro ",
        "tarefas": [
            {"fase": "Iniciação", "descricao": "Aprovar o TAP", "duracao_dias_uteis": 2},
            {"fase": "EXECUCAO", "descricao": "Migrar dados", "duracao_dias_uteis": "10"},
            {"fase": "Planejamento", "descricao": "Montar EAP", "duracao_dias_uteis": 9999},
            {"fase": "Fase Inventada", "descricao": "Descartar"},
            {"fase": "Encerramento", "descricao": ""},
            "lixo",
        ],
    })
    r = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=login(GERENTE))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["nome_projeto"] == "Implantação ERP Financeiro"
    assert [f["fase"] for f in s["fases"]] == ["Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento"]
    assert s["fases"][2]["tarefas"] == [{"descricao": "Migrar dados", "duracao_dias_uteis": 10}]
    assert s["fases"][1]["tarefas"][0]["duracao_dias_uteis"] is None  # duração absurda vira "sem estimativa"
    assert s["descartadas"] == 3

    # Nada foi criado, e a auditoria registra o uso sem guardar o texto.
    assert client.get("/projetos", headers=login(GERENTE)).json()["total"] == 0


def test_ia_permissoes_e_validacao(client, criar_usuario, login, monkeypatch):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    criar_usuario(MEMBRO)
    _resposta_ia(monkeypatch, {"nome_projeto": "ERP", "tarefas": [{"fase": "Iniciação", "descricao": "TAP"}]})
    assert client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=login(MEMBRO)).status_code == 403
    assert client.post("/projetos/ia", json={"texto": "curto"}, headers=login(GERENTE)).status_code == 422


@pytest.mark.parametrize("conteudo", ["isto não é json", "[1, 2]", {"nome_projeto": "ERP", "tarefas": []}])
def test_ia_resposta_inutilizavel_vira_502(client, criar_usuario, login, monkeypatch, conteudo):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    _resposta_ia(monkeypatch, conteudo)
    r = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=login(GERENTE))
    assert r.status_code == 502


def test_ia_sem_chave_e_erros_da_groq(client, criar_usuario, login, monkeypatch):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    monkeypatch.setattr(settings, "groq_api_key", None)
    assert client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=h).status_code == 503

    class _GroqComTimeout:
        def __init__(self, **_):
            self.chat = self
            self.completions = self

        def create(self, **_):
            raise groq.APITimeoutError(request=httpx.Request("POST", "https://api.groq.com"))

    monkeypatch.setattr(settings, "groq_api_key", "gsk_teste")
    monkeypatch.setattr(ia.groq, "Groq", _GroqComTimeout)
    r = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=h)
    assert r.status_code == 504
    assert "demorou" in r.json()["detail"]


# ======================= Resend (e-mail) =======================


@pytest.fixture
def emails(monkeypatch) -> List[Dict[str, Any]]:
    enviados: List[Dict[str, Any]] = []
    monkeypatch.setattr(settings, "resend_api_key", "re_teste")
    monkeypatch.setattr(resend.Emails, "send", lambda params: enviados.append(params) or {"id": "1"})
    return enviados


def test_criar_projeto_envia_email_ao_dono(client, criar_usuario, login, emails):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    r = client.post("/projetos/manual", json={"nome_projeto": "ERP <script>\nBcc: x"}, headers=login(GERENTE))
    assert r.status_code == 201

    assert len(emails) == 1
    assert emails[0]["to"] == [GERENTE]
    assert emails[0]["subject"] == "O Projeto ERP <script> Bcc: x foi iniciado com sucesso"  # sem quebra de linha
    assert "&lt;script&gt;" in emails[0]["html"] and "<script>" not in emails[0]["html"]


def test_importar_projeto_tambem_envia_email(client, criar_usuario, login, emails):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    with open("exemplos/cronograma_exemplo.csv", "rb") as f:
        r = client.post("/projetos/importar", data={"nome_projeto": "Importado"},
                        files={"arquivo": ("c.csv", f.read())}, headers=login(GERENTE))
    assert r.status_code == 201
    assert "Importado" in emails[0]["subject"]


def test_falha_no_resend_nao_afeta_criacao(client, criar_usuario, login, monkeypatch):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    monkeypatch.setattr(settings, "resend_api_key", "re_teste")

    def _falha(params):
        raise resend.exceptions.ResendError(500, "application_error", "fora do ar", "tente depois")

    monkeypatch.setattr(resend.Emails, "send", _falha)
    assert client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=login(GERENTE)).status_code == 201


def test_sem_chave_do_resend_nao_envia(client, criar_usuario, login, monkeypatch):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    monkeypatch.setattr(settings, "resend_api_key", None)
    monkeypatch.setattr(resend.Emails, "send", lambda p: pytest.fail("não deveria enviar"))
    assert client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=login(GERENTE)).status_code == 201


# ======================= Clerk =======================

_CHAVE_CLERK = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_CHAVE_ATACANTE = rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def clerk_ligado(monkeypatch):
    monkeypatch.setattr(settings, "clerk_emissor", EMISSOR_CLERK)
    monkeypatch.setattr(settings, "clerk_cadastro_automatico", True)
    monkeypatch.setattr(clerk, "_chave_publica", lambda token: _CHAVE_CLERK.public_key())


def _token_clerk(chave=_CHAVE_CLERK, alg="RS256", **sobrescrever: Any) -> Dict[str, str]:
    agora = agora_utc()
    claims = {
        "iss": EMISSOR_CLERK, "sub": "user_2abc", "azp": "http://localhost:5173",
        "iat": agora, "nbf": agora, "exp": agora + timedelta(minutes=1),
        "sid": "sess_1", "primaryEmail": "Nova@Empresa.com.br", "fullName": "Nova Pessoa",
    }
    claims.update(sobrescrever)
    claims = {k: v for k, v in claims.items() if v is not None}
    return {"Authorization": f"Bearer {jwt.encode(claims, chave, alg, headers={'kid': 'k1'})}"}


def test_clerk_primeiro_login_cria_membro_sem_acesso(client, clerk_ligado):
    r = client.get("/auth/me", headers=_token_clerk())
    assert r.status_code == 200, r.text
    me = r.json()
    assert (me["email"], me["nome"], me["papel"], me["provedor_auth"]) == (
        "nova@empresa.com.br", "Nova Pessoa", "membro", "clerk")

    assert client.get("/auth/me", headers=_token_clerk()).json()["id"] == me["id"]  # não duplica
    assert client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=_token_clerk()).status_code == 403


def test_clerk_vincula_usuario_existente_e_protege_projetos(client, criar_usuario, clerk_ligado):
    gerente = criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = _token_clerk(primaryEmail=GERENTE)

    r = client.post("/projetos/manual", json={"nome_projeto": "ERP via Clerk"}, headers=h)
    assert r.status_code == 201
    assert r.json()["dono_id"] == gerente.id  # mesma conta, mesmo papel
    assert client.get("/auth/me", headers=h).json()["provedor_auth"] == "clerk"


@pytest.mark.parametrize("token", [
    pytest.param(lambda: _token_clerk(exp=agora_utc() - timedelta(minutes=5)), id="expirado"),
    pytest.param(lambda: _token_clerk(chave=_CHAVE_ATACANTE), id="assinado-por-outra-chave"),
    pytest.param(lambda: _token_clerk(azp="https://site-malicioso.com"), id="azp-de-outro-site"),
    pytest.param(lambda: _token_clerk(iss="https://outro.clerk.accounts.dev"), id="outro-emissor"),
    pytest.param(lambda: _token_clerk(primaryEmail=None), id="sem-email"),
    pytest.param(lambda: _token_clerk(nbf=agora_utc() + timedelta(minutes=5)), id="ainda-nao-valido"),
    # Confusão de algoritmo: HS256 "assinado" com a chave pública não pode passar.
    pytest.param(lambda: _token_clerk(chave="segredo-qualquer" * 4, alg="HS256"), id="hs256-forjado"),
])
def test_clerk_rejeita_tokens_invalidos(client, clerk_ligado, token):
    assert client.get("/auth/me", headers=token()).status_code == 401


def test_clerk_desligado_rejeita_token_do_clerk(client, monkeypatch):
    monkeypatch.setattr(settings, "clerk_emissor", None)
    monkeypatch.setattr(clerk, "_chave_publica", lambda token: _CHAVE_CLERK.public_key())
    assert client.get("/auth/me", headers=_token_clerk()).status_code == 401


def test_clerk_sem_cadastro_automatico_e_email_de_outra_conta(client, clerk_ligado, monkeypatch):
    monkeypatch.setattr(settings, "clerk_cadastro_automatico", False)
    assert client.get("/auth/me", headers=_token_clerk()).status_code == 403

    monkeypatch.setattr(settings, "clerk_cadastro_automatico", True)
    assert client.get("/auth/me", headers=_token_clerk(sub="user_A")).status_code == 200
    # Outra conta do Clerk com o mesmo e-mail não assume o usuário já vinculado.
    assert client.get("/auth/me", headers=_token_clerk(sub="user_B")).status_code == 403


def test_clerk_usuario_desativado_perde_acesso(client, criar_usuario, login, clerk_ligado):
    criar_usuario("admin@empresa.com.br", PapelUsuario.ADMIN)
    me = client.get("/auth/me", headers=_token_clerk()).json()
    client.patch(f"/usuarios/{me['id']}", json={"ativo": False}, headers=login("admin@empresa.com.br"))
    assert client.get("/auth/me", headers=_token_clerk()).status_code == 401


# ======================= Criação com cronograma (IA -> /manual) =======================


def test_criar_projeto_com_cronograma_encadeado(client, criar_usuario, login, brasil_api, emails):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    payload = {
        "nome_projeto": "ERP",
        "data_inicio": "2026-04-17",  # sexta
        "fases": [
            # Fora de ordem de propósito: o cronograma segue a ordem do Waterfall, não a da lista.
            {"fase": "EXECUCAO", "tarefas": [{"descricao": "E", "duracao_dias_uteis": 1}]},
            {"fase": "Iniciação", "tarefas": [{"descricao": "A", "duracao_dias_uteis": 1},
                                              {"descricao": "B", "duracao_dias_uteis": 2}]},
            {"fase": "Planejamento", "tarefas": [{"descricao": "C"},  # sem duração: sem datas
                                                 {"descricao": "D", "duracao_dias_uteis": 1,
                                                  "responsavel": "Fornecedor X"}]},
        ],
    }
    r = client.post("/projetos/manual", json=payload, headers=h)
    assert r.status_code == 201, r.text

    detalhe = client.get(f"/projetos/{r.json()['id']}", headers=h).json()
    datas = {t["descricao"]: (t["data_inicio"], t["data_fim"]) for f in detalhe["fases"] for t in f["tarefas"]}
    assert datas == {
        "A": ("2026-04-17", "2026-04-17"),
        "B": ("2026-04-20", "2026-04-22"),  # pula o fim de semana e o Tiradentes (21/04)
        "C": (None, None),
        "D": ("2026-04-23", "2026-04-23"),
        "E": ("2026-04-24", "2026-04-24"),
    }
    assert [t["descricao"] for t in detalhe["fases"][0]["tarefas"]] == ["A", "B"]  # ordem preservada
    assert detalhe["fases"][1]["tarefas"][1]["responsavel"] == "Fornecedor X"

    historico = client.get(f"/projetos/{r.json()['id']}/historico", headers=h).json()["itens"]
    assert historico[-1]["detalhes"]["tarefas"] == 5
    assert len(emails) == 1  # e-mail continua saindo uma vez só


@pytest.mark.parametrize("fases, data_inicio", [
    pytest.param([{"fase": "Fase Inventada", "tarefas": []}], "2026-04-17", id="fase-inexistente"),
    pytest.param([{"fase": "Iniciação"}, {"fase": "iniciacao"}], "2026-04-17", id="fase-repetida"),
    pytest.param([{"fase": "Iniciação", "tarefas": [{"descricao": "A", "duracao_dias_uteis": 2}]}], None,
                 id="duracao-sem-data-inicio"),
    pytest.param([{"fase": "Iniciação", "tarefas": [{"descricao": "  "}]}], None, id="descricao-vazia"),
])
def test_criar_projeto_com_cronograma_invalido(client, criar_usuario, login, fases, data_inicio):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    r = client.post("/projetos/manual", json={"nome_projeto": "ERP", "data_inicio": data_inicio, "fases": fases}, headers=h)
    assert r.status_code == 422
    assert client.get("/projetos", headers=h).json()["total"] == 0  # tudo ou nada


def test_fluxo_completo_sugestao_da_ia_vira_projeto(client, criar_usuario, login, monkeypatch, brasil_api):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    _resposta_ia(monkeypatch, {"nome_projeto": "ERP Financeiro", "tarefas": [
        {"fase": fase, "descricao": f"Tarefa de {fase}", "duracao_dias_uteis": 3}
        for fase in ("Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento")
    ]})
    sugestao = client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=h).json()

    # O front envia a sugestão de volta, sem transformar o formato.
    r = client.post("/projetos/manual", headers=h, json={
        "nome_projeto": sugestao["nome_projeto"], "data_inicio": "2026-04-17", "fases": sugestao["fases"],
    })
    assert r.status_code == 201, r.text
    tarefas = [t for f in r.json()["fases"] for t in f["tarefas"]]
    assert len(tarefas) == 5
    assert tarefas[-1]["data_fim"] == "2026-05-11"  # 15 dias úteis a partir de 17/04, sem 21/04 e 01/05
