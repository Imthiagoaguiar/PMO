"""Vigia: log estruturado da API, coletor (watchdog), sinais, regras, sondas, IA e acesso aos relatórios."""

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import text
from watchdog.observers import Observer

from app import registro, services
from app.config import settings
from app.database import SessionLocal
from app.models import Auditoria, PapelUsuario, VigiaRodada
from app.vigia import analista, regras, rodada, sinais, sondas
from app.vigia.__main__ import main as main_do_vigia
from app.vigia.coletor import ColetorDeLog

UTC = timezone.utc
AGORA = datetime(2026, 3, 20, 15, 0, tzinfo=UTC)
DONO = "dono@empresa.com.br"


def _linha(**campos) -> str:
    return json.dumps({"ts": AGORA.isoformat(), "nivel": "INFO", "logger": "pmo.acesso", "msg": "", **campos}) + "\n"


@pytest.fixture
def log_tmp(tmp_path):
    """Um handler JSON gravando num arquivo temporário (como a API faz com LOG_ARQUIVO)."""
    arquivo = tmp_path / "pmo.log"
    handler = logging.FileHandler(arquivo, encoding="utf-8")
    handler.setFormatter(registro.FormatoJson())
    logging.getLogger().addHandler(handler)
    yield arquivo
    logging.getLogger().removeHandler(handler)
    handler.close()


def _eventos(arquivo: Path):
    return [json.loads(l) for l in arquivo.read_text().splitlines() if l.strip()]


@pytest.fixture
def ok_na_saude(monkeypatch):
    """API de pé e disco folgado: o resultado não pode depender da máquina que roda o teste."""
    monkeypatch.setattr(sondas, "saude_api", lambda url: {"ok": True, "status": 200, "ms": 3.0})
    monkeypatch.setattr(sondas, "recursos", lambda arquivo: {"disco_livre_percentual": 60.0, "disco_livre_gb": 300.0})


# ---------- Log estruturado da API ----------


def test_cada_requisicao_vira_um_evento_com_a_rota_em_molde(client, criar_usuario, login, log_tmp):
    registro.configurar(None)  # aplica a regra do httpx (só avisos), como na API
    criar_usuario(DONO, PapelUsuario.GERENTE)
    h = login(DONO)
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).json()
    client.get(f"/projetos/{p['id']}?busca=segredo@x.com", headers=h)
    acessos = [e for e in _eventos(log_tmp) if e.get("tipo") == "acesso"]
    detalhe = next(e for e in acessos if e["rota"] == "/projetos/{projeto_id}")
    assert (detalhe["metodo"], detalhe["status"]) == ("GET", 200)
    assert isinstance(detalhe["duracao_ms"], float)
    assert "segredo@x.com" not in log_tmp.read_text()  # a query string não vai para o log


def test_erro_nao_tratado_fica_no_log_com_stack(criar_usuario, login, log_tmp, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    criar_usuario(DONO, PapelUsuario.GERENTE)
    with TestClient(app, raise_server_exceptions=False) as cliente:
        h = {"Authorization": "Bearer " + cliente.post("/auth/login", data={"username": DONO, "password": "SenhaForte!2026"}).json()["access_token"]}
        p = cliente.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).json()

        def _quebra(*_a, **_k):
            raise RuntimeError("divisão por zero no indicador")

        monkeypatch.setattr(services, "calcular_indicadores", _quebra)
        assert cliente.get(f"/projetos/{p['id']}/indicadores", headers=h).status_code == 500

    eventos = _eventos(log_tmp)
    erro = next(e for e in eventos if e.get("excecao") == "RuntimeError")
    assert erro["nivel"] == "ERROR" and erro["rota"] == "/projetos/{projeto_id}/indicadores"
    assert "app/routers/projetos.py" in erro["stack"]
    acesso = next(e for e in eventos if e.get("tipo") == "acesso" and e["rota"] == "/projetos/{projeto_id}/indicadores")
    assert (acesso["status"], acesso["nivel"]) == (500, "ERROR")


# ---------- Coletor ----------


def test_coletor_le_so_o_que_foi_acrescentado_e_linha_pela_metade(tmp_path):
    arquivo = tmp_path / "pmo.log"
    arquivo.write_text(_linha(tipo="acesso", status=200))
    coletor = ColetorDeLog(arquivo)
    coletor.ler_novidades()
    with arquivo.open("a") as f:
        metade = _linha(tipo="acesso", status=500)
        f.write(metade[:20])  # a API está no meio da escrita
    coletor.ler_novidades()
    assert len(coletor.retirar(AGORA)) == 1
    with arquivo.open("a") as f:
        f.write(metade[20:] + "isto não é json\n")
    eventos = coletor.retirar(AGORA)
    assert [e["status"] for e in eventos] == [500]
    assert coletor.linhas_ilegiveis == 1
    assert coletor.retirar(AGORA) == []  # cada evento entra numa rodada só


def test_coletor_percebe_a_rotacao_do_arquivo(tmp_path):
    arquivo = tmp_path / "pmo.log"
    arquivo.write_text(_linha(tipo="acesso", status=200) * 3)
    coletor = ColetorDeLog(arquivo)
    coletor.ler_novidades()
    arquivo.rename(tmp_path / "pmo.log.1")
    arquivo.write_text(_linha(tipo="acesso", status=503))
    eventos = coletor.retirar(AGORA)
    assert [e["status"] for e in eventos] == [200, 200, 200, 503]


def test_coletor_na_partida_pega_so_a_janela(tmp_path):
    arquivo = tmp_path / "pmo.log"
    velho = json.dumps({"ts": (AGORA - timedelta(hours=3)).isoformat(), "nivel": "ERROR", "msg": "antigo"}) + "\n"
    (tmp_path / "pmo.log.1").write_text(velho + _linha(nivel="ERROR", msg="do rotacionado"))
    arquivo.write_text(_linha(nivel="ERROR", msg="recente"))
    coletor = ColetorDeLog(arquivo)
    coletor.iniciar(desde=AGORA - timedelta(hours=1))
    assert [e["msg"] for e in coletor.retirar(AGORA)] == ["do rotacionado", "recente"]


def test_watchdog_entrega_as_linhas_novas_sozinho(tmp_path):
    arquivo = tmp_path / "pmo.log"
    arquivo.write_text("")
    coletor = ColetorDeLog(arquivo)
    coletor.ler_novidades()
    observador = Observer()
    observador.schedule(coletor, str(tmp_path), recursive=False)
    observador.start()
    try:
        with arquivo.open("a") as f:
            f.write(_linha(nivel="ERROR", msg="apareceu agora"))
            f.flush()
            os.fsync(f.fileno())
        for _ in range(50):  # sem chamar ler_novidades: quem lê é o evento do watchdog
            if coletor._eventos:
                break
            time.sleep(0.1)
        assert [e["msg"] for e in coletor._eventos] == ["apareceu agora"]
    finally:
        observador.stop()
        observador.join(timeout=5)


# ---------- Sinais e regras ----------


def _stack(arquivo: str, linha: int) -> str:
    return (f'Traceback (most recent call last):\n  File "/srv/pmo/.venv/lib/starlette/x.py", line 9, in y\n'
            f'  File "/srv/pmo/{arquivo}", line {linha}, in rota\nKeyError: 1')


def test_sinais_agrupam_erros_iguais_e_medem_lentidao():
    eventos = (
        [{"tipo": "acesso", "metodo": "GET", "rota": "/projetos/{projeto_id}", "status": 200, "duracao_ms": 30.0}] * 18
        + [{"tipo": "acesso", "metodo": "GET", "rota": "/projetos/{projeto_id}/relatorio", "status": 200, "duracao_ms": 4500.0}] * 3
        + [{"tipo": "acesso", "metodo": "PATCH", "rota": "/projetos/{projeto_id}/tarefas/{tarefa_id}", "status": 500, "duracao_ms": 12.0}] * 2
        + [{"tipo": "acesso", "metodo": "POST", "rota": "/auth/login", "status": 429, "duracao_ms": 1.0}] * 4
        + [{"nivel": "ERROR", "logger": "pmo.erro", "msg": f"Erro na tarefa {i} de ana@x.com", "excecao": "KeyError",
            "stack": _stack("app/routers/tarefas.py", 120), "ts": AGORA.isoformat()} for i in range(2)]
        + [{"nivel": "WARNING", "logger": "app.integracoes.feriados", "msg": "Brasil API indisponível para 2026"}] * 3
    )
    s = sinais.resumir(eventos)
    assert s["requisicoes"] == 27 and s["por_classe"] == {"2xx": 21, "5xx": 2, "4xx": 4}
    assert s["respostas_5xx"] == [{"rota": "PATCH /projetos/{projeto_id}/tarefas/{tarefa_id}", "status": 500, "quantidade": 2}]
    assert s["respostas_429"] == [{"rota": "POST /auth/login", "quantidade": 4}]
    assert len(s["erros"]) == 1  # mesma exceção, só mudou o número e o e-mail
    assert s["erros"][0]["quantidade"] == 2 and s["erros"][0]["local"] == "app/routers/tarefas.py:120"
    assert "ana@x.com" not in s["erros"][0]["mensagem"]
    assert [r["rota"] for r in s["rotas_lentas"]] == ["GET /projetos/{projeto_id}/relatorio"]
    assert s["avisos"][0]["quantidade"] == 3


@pytest.mark.parametrize("duracoes", [[7687.5, 300, 250, 280, 260, 270], [10, 5000], [1, 2, 3], [42.0]])
def test_p95_nunca_passa_do_maximo(duracoes):
    eventos = [{"tipo": "acesso", "metodo": "GET", "rota": "/x", "status": 200, "duracao_ms": d} for d in duracoes]
    resumo = sinais.resumir(eventos)
    assert resumo["p95_geral_ms"] <= max(duracoes)
    for lenta in resumo["rotas_lentas"]:
        assert lenta["p95_ms"] <= lenta["max_ms"]


def _sinais_base(**sobrescrever):
    s = {"janela_minutos": 60, "log": sinais.resumir([]), "coletor": {"descartados": 0},
         "saude": {"ok": True}, "migracao": {"em_dia": True}, "integridade": [],
         "seguranca": {"eventos": {}, "ips_distintos_com_falha": 0},
         "higiene": {"refresh_tokens_para_limpar": 0, "admins_sem_2fa": 0},
         "fila": {"redis_ok": True, "workers": 1, "paradas_na_fila": 0, "travadas_processando": 0, "falhas_internas_na_janela": 0},
         "recursos": {"disco_livre_percentual": 60.0, "disco_livre_gb": 100}}
    s.update(sobrescrever)
    return s


def test_sistema_saudavel_nao_gera_achado():
    assert regras.avaliar(_sinais_base()) == []
    assert regras.pior([]) is None


@pytest.mark.parametrize("sobrescrever, codigo, gravidade", [
    ({"saude": {"ok": False, "status": 503, "corpo": {"banco": "indisponivel"}}}, "api_fora_do_ar", "critica"),
    ({"saude": {"ok": False, "status": None, "erro": "ConnectError"}}, "api_desligada_dev", "baixa"),
    ({"migracao": {"em_dia": False, "banco": "a", "codigo": "b"}}, "migracao_pendente", "alta"),
    ({"integridade": [{"codigo": "projeto_sem_5_fases", "descricao": "x", "quantidade": 1, "exemplos": [7]}]},
     "integridade_projeto_sem_5_fases", "critica"),
    ({"seguranca": {"eventos": {"auth.reuso_refresh_token": 2}, "ips_distintos_com_falha": 0}}, "reuso_de_token", "alta"),
    ({"seguranca": {"eventos": {"auth.login_falhou": 80}, "ips_distintos_com_falha": 40}}, "muitas_falhas_de_login", "media"),
    ({"higiene": {"refresh_tokens_para_limpar": 0, "admins_sem_2fa": 1}}, "admin_sem_2fa", "media"),
    ({"recursos": {"disco_livre_percentual": 3.0, "disco_livre_gb": 1}}, "disco_baixo", "critica"),
    ({"recursos": {"disco_livre_percentual": 3.4, "disco_livre_gb": 8.2}}, "disco_baixo", "media"),  # o seu Mac hoje
    ({"recursos": {"disco_livre_percentual": 40.0, "disco_livre_gb": 6.0}}, "disco_baixo", "media"),
    ({"coletor": {"descartados": 10}}, "log_transbordou", "media"),
    ({"fila": {"redis_ok": False, "erro": "ConnectionError"}}, "fila_fora_do_ar", "alta"),
    ({"fila": {"redis_ok": True, "workers": 0, "paradas_na_fila": 3}}, "importacoes_paradas", "alta"),
    ({"fila": {"redis_ok": True, "workers": 2, "paradas_na_fila": 3}}, "importacoes_paradas", "media"),
    ({"fila": {"redis_ok": True, "travadas_processando": 1}}, "importacoes_travadas", "alta"),
    ({"fila": {"redis_ok": True, "falhas_internas_na_janela": 2}}, "importacoes_com_erro_interno", "alta"),
])
def test_regras(sobrescrever, codigo, gravidade):
    achados = regras.avaliar(_sinais_base(**sobrescrever))
    assert [(a["codigo"], a["gravidade"]) for a in achados] == [(codigo, gravidade)]


def test_api_sem_resposta_em_producao_e_critica(monkeypatch):
    monkeypatch.setattr(settings, "ambiente", "prod")
    achados = regras.avaliar(_sinais_base(saude={"ok": False, "status": None, "erro": "ConnectError"}))
    assert [(a["codigo"], a["gravidade"]) for a in achados] == [("api_fora_do_ar", "critica")]


def test_pior_gravidade():
    assert regras.pior([{"gravidade": "baixa"}, {"gravidade": "alta"}, {"gravidade": "media"}]) == "alta"


# ---------- Sondas ----------


@pytest.fixture
def projeto(client, criar_usuario, login):
    criar_usuario(DONO, PapelUsuario.GERENTE)
    outro = criar_usuario("fora@empresa.com.br")
    h = login(DONO)
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).json()
    q = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=h).json()
    t = client.post(f"/projetos/{p['id']}/fases/{p['fases'][0]['id']}/tarefas", json={"descricao": "T"}, headers=h).json()
    return {"p": p, "q": q, "tarefa": t, "fora": outro.id}


def test_integridade_limpa_quando_a_api_e_usada(projeto):
    with SessionLocal() as db:
        assert sondas.integridade(db) == []


def test_integridade_acha_dados_corrompidos_por_fora_da_api(projeto):
    q, t = projeto["q"], projeto["tarefa"]
    with SessionLocal() as db:
        db.execute(text("DELETE FROM fases WHERE id = :f"), {"f": q["fases"][4]["id"]})
        db.execute(text("UPDATE tarefas SET pilar_id = :pi, responsavel_id = :u WHERE id = :t"),
                   {"pi": q["pilares"][0]["id"], "u": projeto["fora"], "t": t["id"]})
        db.commit()
        achados = {i["codigo"]: i for i in sondas.integridade(db)}
    assert achados["projeto_sem_5_fases"]["exemplos"] == [q["id"]]
    assert achados["tarefa_com_pilar_de_outro_projeto"]["exemplos"] == [t["id"]]
    assert achados["responsavel_fora_do_projeto"]["exemplos"] == [t["id"]]


def test_migracao_em_dia_e_atrasada():
    with SessionLocal() as db:
        assert sondas.migracao(db)["em_dia"] is None  # banco de teste sem alembic_version
        cabeca = __import__("alembic.script", fromlist=["ScriptDirectory"]).ScriptDirectory.from_config(
            __import__("alembic.config", fromlist=["Config"]).Config(str(sondas.RAIZ / "alembic.ini"))).get_current_head()
        db.execute(text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL)"))
        try:
            db.execute(text("INSERT INTO alembic_version VALUES (:v)"), {"v": cabeca})
            assert sondas.migracao(db)["em_dia"] is True
            db.execute(text("UPDATE alembic_version SET version_num = 'antiga'"))
            assert sondas.migracao(db)["em_dia"] is False
        finally:  # a tabela não é dos modelos: o banco_limpo não a apagaria
            db.rollback()
            db.execute(text("DROP TABLE IF EXISTS alembic_version"))
            db.commit()


def test_seguranca_conta_alarmes_da_janela():
    with SessionLocal() as db:
        db.add_all([Auditoria(acao="auth.login_falhou", entidade="usuario", ip=f"10.0.0.{i % 3}") for i in range(7)]
                   + [Auditoria(acao="auth.reuso_refresh_token", entidade="usuario")])
        db.commit()
        s = sondas.seguranca(db, datetime.now(UTC) - timedelta(hours=1))
    assert s == {"eventos": {"auth.login_falhou": 7, "auth.reuso_refresh_token": 1}, "ips_distintos_com_falha": 3}


# ---------- IA ----------


def test_anonimizar_tira_dados_pessoais_e_segredos():
    texto = ("ana.souza@empresa.com.br de 187.45.10.2 e fe80::1:2:3:4 token eyJhbGciOi.eyJzdWIiOjF9.assinatura "
             "chave gsk_abcdefghij12345 cpf 123.456.789-09")
    limpo = analista.anonimizar({"x": [texto]})["x"][0]
    for vazado in ("ana.souza", "187.45.10.2", "fe80::1", "eyJhbGciOi", "gsk_abc", "123.456.789"):
        assert vazado not in limpo
    assert "<email>" in limpo and "<ip>" in limpo and "<token>" in limpo and "<segredo>" in limpo


def test_sem_chave_a_rodada_segue_so_com_regras(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    resposta, situacao = analista.analisar(_sinais_base(), [])
    assert resposta is None and "GROQ_API_KEY" in situacao


def test_ia_recebe_dados_anonimizados_e_delimitados(monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_teste")
    enviado = {}

    def _falso(conteudo):
        enviado["conteudo"] = conteudo
        return json.dumps({"resumo": "Erros 500 na edição de tarefas.", "achados": [
            {"titulo": "KeyError ao editar tarefa", "gravidade": "alta", "evidencias": ["2 ocorrências"],
             "causa_provavel": "fase removida", "impacto": "edições falham", "como_investigar": ["ver stack"]}]})

    monkeypatch.setattr(analista, "_chamar", _falso)
    sinais_com_email = _sinais_base(avisos_extra=["login de ana@empresa.com.br de 10.1.2.3"])
    resposta, situacao = analista.analisar(sinais_com_email, [])
    assert resposta.achados[0].gravidade == "alta" and "analisado" in situacao
    assert enviado["conteudo"].startswith("<dados>") and enviado["conteudo"].endswith("</dados>")
    assert "ana@empresa.com.br" not in enviado["conteudo"] and "10.1.2.3" not in enviado["conteudo"]


@pytest.mark.parametrize("bruto", ["não é json", '{"resumo": "x"}', json.dumps({"resumo": "ok", "achados": [{"titulo": "x", "gravidade": "apocalipse"}]})])
def test_resposta_da_ia_fora_do_formato_e_descartada(monkeypatch, bruto):
    monkeypatch.setattr(settings, "groq_api_key", "gsk_teste")
    monkeypatch.setattr(analista, "_chamar", lambda c: bruto)
    resposta, situacao = analista.analisar(_sinais_base(), [])
    if bruto == '{"resumo": "x"}':  # resumo curto demais
        assert resposta is None
    assert resposta is None or resposta.achados == []
    assert "IA" in situacao or "analisado" in situacao


def test_groq_fora_do_ar_nao_derruba_a_rodada(monkeypatch):
    import groq
    import httpx

    monkeypatch.setattr(settings, "groq_api_key", "gsk_teste")

    def _timeout(_c):
        raise groq.APITimeoutError(request=httpx.Request("POST", "https://api.groq.com"))

    monkeypatch.setattr(analista, "_chamar", _timeout)
    resposta, situacao = analista.analisar(_sinais_base(), [])
    assert resposta is None and "indisponível" in situacao


# ---------- Rodada completa ----------


def test_rodada_completa_grava_o_relatorio(tmp_path, ok_na_saude, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    arquivo = tmp_path / "pmo.log"
    arquivo.write_text(
        _linha(tipo="acesso", metodo="PATCH", rota="/projetos/{projeto_id}", status=500, duracao_ms=8.0)
        + json.dumps({"ts": AGORA.isoformat(), "nivel": "ERROR", "logger": "pmo.erro", "msg": "falhou para ana@x.com",
                      "excecao": "ValueError", "stack": _stack("app/routers/projetos.py", 300)}) + "\n"
    )
    coletor = ColetorDeLog(arquivo)
    coletor.iniciar(desde=AGORA - timedelta(hours=1))
    with SessionLocal() as db:
        feita = rodada.executar(db, coletor, janela_inicio=AGORA - timedelta(hours=1), agora=AGORA + timedelta(seconds=1))
        salva = db.get(VigiaRodada, feita.id)
        assert salva.pior_gravidade.value == "alta"
        assert {a["codigo"] for a in salva.achados} == {"erro_no_log", "respostas_5xx"}
        assert "1 erro(s) 5xx" in salva.resumo and "IA desligada" in salva.ia_situacao
        assert "ana@x.com" not in json.dumps(salva.sinais)


def test_sonda_que_quebra_nao_derruba_a_rodada(ok_na_saude, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)

    def _quebra(_db):
        raise RuntimeError("sem permissão")

    monkeypatch.setattr(sondas, "integridade", _quebra)
    with SessionLocal() as db:
        feita = rodada.executar(db, None, janela_inicio=AGORA - timedelta(hours=1), agora=AGORA)
        assert feita.id and feita.sinais["integridade"] == []


def test_erro_de_banco_numa_sonda_nao_contamina_as_outras(ok_na_saude, monkeypatch, criar_usuario):
    """No Postgres, um SELECT que falha aborta a transação: sem rollback, a rodada inteira se perdia."""
    monkeypatch.setattr(settings, "groq_api_key", None)
    criar_usuario("admin@empresa.com.br", PapelUsuario.ADMIN)
    monkeypatch.setattr(settings, "exigir_2fa_admin", True)

    def _select_invalido(db):
        db.execute(text("SELECT * FROM tabela_que_nao_existe"))

    monkeypatch.setattr(sondas, "integridade", _select_invalido)
    with SessionLocal() as db:
        feita = rodada.executar(db, None, janela_inicio=AGORA - timedelta(hours=1), agora=AGORA)
        assert feita.id is not None
        assert feita.sinais["higiene"]["admins_sem_2fa"] == 1  # a sonda seguinte rodou de verdade


def test_sonda_da_fila_acha_importacao_parada_e_travada(criar_usuario):
    from app.models import Importacao, StatusImportacao

    dono = criar_usuario("dono.fila@empresa.com.br", PapelUsuario.GERENTE)
    agora = datetime.now(UTC)
    with SessionLocal() as db:
        base = dict(usuario_id=dono.id, nome_projeto="P", arquivo_nome="p.csv", arquivo_bytes=1)
        db.add_all([
            Importacao(**base, status=StatusImportacao.NA_FILA, criada_em=agora - timedelta(minutes=30)),
            Importacao(**base, status=StatusImportacao.NA_FILA, criada_em=agora - timedelta(minutes=2)),  # recente: ok
            Importacao(**base, status=StatusImportacao.PROCESSANDO, iniciada_em=agora - timedelta(hours=1)),
        ])
        db.commit()
        estado = sondas.fila_de_importacao(db, agora, agora - timedelta(hours=1))
    assert estado["redis_ok"] is True  # fakeredis nos testes
    assert (estado["paradas_na_fila"], estado["travadas_processando"]) == (1, 1)


def test_linha_de_comando_uma_vez(tmp_path, ok_na_saude, monkeypatch, capsys):
    monkeypatch.setattr(settings, "groq_api_key", None)
    monkeypatch.setattr(settings, "log_arquivo", str(tmp_path / "pmo.log"))
    assert main_do_vigia(["--uma-vez"]) == 0
    assert "Rodada 1" in capsys.readouterr().out
    with SessionLocal() as db:
        assert db.query(VigiaRodada).count() == 1


# ---------- Quem vê os relatórios ----------


@pytest.fixture
def com_rodada(ok_na_saude, monkeypatch):
    monkeypatch.setattr(settings, "groq_api_key", None)
    with SessionLocal() as db:
        return rodada.executar(db, None, janela_inicio=datetime.now(UTC) - timedelta(hours=1)).id


def test_so_o_admin_listado_ve_o_vigia(client, criar_usuario, login, monkeypatch, com_rodada):
    criar_usuario("eu@empresa.com.br", PapelUsuario.ADMIN)
    criar_usuario("outro.admin@empresa.com.br", PapelUsuario.ADMIN)
    criar_usuario("gerente@empresa.com.br", PapelUsuario.GERENTE)
    monkeypatch.setattr(settings, "vigia_emails", ["EU@empresa.com.br", "gerente@empresa.com.br"])

    eu = login("eu@empresa.com.br")
    assert client.get("/auth/me", headers=eu).json()["pode_ver_vigia"] is True
    assert client.get("/vigia/rodadas", headers=eu).json()["total"] == 1
    assert client.get(f"/vigia/rodadas/{com_rodada}", headers=eu).status_code == 200
    assert client.get("/vigia/situacao", headers=eu).json()["atrasado"] is False

    for email in ("outro.admin@empresa.com.br", "gerente@empresa.com.br"):  # gerente listado, mas não é admin
        h = login(email)
        assert client.get("/auth/me", headers=h).json()["pode_ver_vigia"] is False
        for url in ("/vigia/rodadas", f"/vigia/rodadas/{com_rodada}", "/vigia/situacao"):
            r = client.get(url, headers=h)
            assert (r.status_code, r.json()) == (404, {"detail": "Não encontrado."})


def test_sem_vigia_emails_ninguem_ve(client, criar_usuario, login, monkeypatch):
    criar_usuario("eu@empresa.com.br", PapelUsuario.ADMIN)
    monkeypatch.setattr(settings, "vigia_emails", [])
    assert client.get("/vigia/rodadas", headers=login("eu@empresa.com.br")).status_code == 404


def test_situacao_avisa_quando_o_vigia_parou(client, criar_usuario, login, monkeypatch):
    criar_usuario("eu@empresa.com.br", PapelUsuario.ADMIN)
    monkeypatch.setattr(settings, "vigia_emails", ["eu@empresa.com.br"])
    h = login("eu@empresa.com.br")
    assert client.get("/vigia/situacao", headers=h).json()["atrasado"] is True  # nunca rodou
    with SessionLocal() as db:
        velha = datetime.now(UTC) - timedelta(hours=3)
        db.add(VigiaRodada(iniciada_em=velha, concluida_em=velha, janela_inicio=velha, resumo="x", achados=[], sinais={},
                           ia_situacao="x"))
        db.commit()
    assert client.get("/vigia/situacao", headers=h).json()["atrasado"] is True
