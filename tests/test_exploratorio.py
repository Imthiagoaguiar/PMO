"""
Testes exploratórios (16/11/2026): situações de borda das funcionalidades novas que as suítes
de cada funcionalidade não cobriam — combinações entre fases, dependências, marcos e orçamento.
"""

import time
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app import tempo
from app.integracoes import feriados
from app.models import PapelUsuario

GERENTE = "gerente@empresa.com.br"


@pytest.fixture(autouse=True)
def ambiente(monkeypatch):
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())
    monkeypatch.setattr(tempo, "agora_utc", lambda: datetime(2026, 3, 20, 15, tzinfo=timezone.utc))


@pytest.fixture
def c(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    h = login(GERENTE)
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP", "data_inicio": "2026-01-05"}, headers=h).json()
    fases = {f["titulo"]: f["id"] for f in p["fases"]}

    def tarefa(descricao, fase="Iniciação", **dados):
        r = client.post(f"/projetos/{p['id']}/fases/{fases[fase]}/tarefas", json={"descricao": descricao, **dados}, headers=h)
        assert r.status_code == 201, r.text
        return r.json()

    def dep(a, b, tipo="inicio_apos_fim", **extra):
        return client.post(f"/projetos/{p['id']}/dependencias",
                           json={"predecessora_id": a["id"], "sucessora_id": b["id"], "tipo": tipo, **extra}, headers=h)

    return {"h": h, "id": p["id"], "fases": fases, "tarefa": tarefa, "dep": dep}


# ---------- Fases x dependências ----------


def test_excluir_fase_com_tarefas_leva_as_dependencias_e_libera_quem_dependia(client, c):
    a = c["tarefa"]("A", fase="Planejamento")
    b = c["tarefa"]("B", fase="Execução")
    assert c["dep"](a, b).status_code == 201
    r = client.delete(f"/projetos/{c['id']}/fases/{c['fases']['Planejamento']}?excluir_tarefas=true", headers=c["h"])
    assert r.status_code == 204
    assert client.get(f"/projetos/{c['id']}/dependencias", headers=c["h"]).json() == []
    r = client.patch(f"/projetos/{c['id']}/tarefas/{b['id']}", json={"percentual_conclusao": 50}, headers=c["h"])
    assert r.status_code == 200


def test_mover_tarefas_ao_excluir_fase_mantem_as_dependencias(client, c):
    a = c["tarefa"]("A", fase="Planejamento")
    b = c["tarefa"]("B", fase="Execução")
    c["dep"](a, b)
    client.delete(f"/projetos/{c['id']}/fases/{c['fases']['Planejamento']}?mover_para={c['fases']['Iniciação']}", headers=c["h"])
    r = client.patch(f"/projetos/{c['id']}/tarefas/{b['id']}", json={"percentual_conclusao": 50}, headers=c["h"])
    assert r.status_code == 422  # A mudou de fase, mas B continua esperando por ela


def test_cadeia_longa_de_dependencias_reprograma_rapido(client, c):
    """500 tarefas encadeadas: criar o último elo empurra as 500 e não pode levar uma eternidade."""
    tarefas = [c["tarefa"](f"T{i}", data_inicio="2026-03-02", duracao_dias_uteis=1) for i in range(500)]
    for a, b in zip(tarefas[1:-1], tarefas[2:]):
        assert c["dep"](a, b, ajustar_datas=False).status_code == 201
    inicio = time.perf_counter()
    r = c["dep"](tarefas[0], tarefas[1])  # empurra T1, que empurra T2... até T499
    assert r.status_code == 201
    assert time.perf_counter() - inicio < 5
    assert len(r.json()["reprogramadas"]) == 499
    ultima = r.json()["reprogramadas"][-1]
    assert ultima["descricao"] == "T499" and ultima["data_inicio"] > "2028-01-01"  # 499 dias úteis depois
    r = c["dep"](tarefas[499], tarefas[0])
    assert r.status_code == 422 and "ciclo" in r.json()["detail"]


# ---------- Orçamento: bordas ----------


def _item(client, c, valor="10000.00", **dados):
    r = client.post(f"/projetos/{c['id']}/orcamento/itens", json={"descricao": "Item", "valor_orcado": valor, **dados}, headers=c["h"])
    assert r.status_code == 201, r.text
    return r.json()


def _lanc(client, c, item, valor, data):
    return client.post(f"/projetos/{c['id']}/orcamento/lancamentos",
                       json={"item_id": item["id"], "valor": valor, "data": data, "descricao": "x"}, headers=c["h"])


def test_previsao_nao_explode_com_cronograma_quase_parado(client, c):
    """Com 1% feito, "realizado ÷ avanço" daria 100x o gasto: no começo do projeto isso não é previsão."""
    c["tarefa"]("Kickoff", data_inicio="2026-01-05", data_fim="2026-12-31", percentual_conclusao=1)
    item = _item(client, c, "100000.00")
    _lanc(client, c, item, "5000.00", "2026-02-01")
    r = client.get(f"/projetos/{c['id']}/orcamento", headers=c["h"]).json()
    assert r["previsao_ritmo"] is None and Decimal(r["previsao_final"]) == Decimal("5000.00")
    assert not any("deve fechar" in a["mensagem"] for a in r["alertas"])


def test_lancamento_com_data_absurda_nao_some_com_o_mes_atual(client, c):
    """Digitou 2062 em vez de 2026: a curva não pode perder os meses reais, e o erro precisa aparecer."""
    c["tarefa"]("T", data_inicio="2026-01-05", data_fim="2026-06-30")
    item = _item(client, c)
    _lanc(client, c, item, "1000.00", "2026-02-10")
    _lanc(client, c, item, "1000.00", "2062-02-10")
    r = client.get(f"/projetos/{c['id']}/orcamento", headers=c["h"]).json()
    meses = [p["mes"] for p in r["curva"]]
    assert "2026-02" in meses
    assert any("fora do período" in a["mensagem"] for a in r["alertas"])


def test_valores_no_limite(client, c):
    grande = "999999999999.99"
    itens = [_item(client, c, grande) for _ in range(3)]
    assert _lanc(client, c, itens[0], grande, "2026-02-01").status_code == 201
    r = client.get(f"/projetos/{c['id']}/orcamento", headers=c["h"])
    assert r.status_code == 200 and Decimal(r.json()["total_orcado"]) == Decimal(grande) * 3
    assert client.get(f"/projetos/{c['id']}/orcamento/exportar", headers=c["h"]).status_code == 200
    assert client.get(f"/projetos/{c['id']}/relatorio", headers=c["h"]).status_code == 200
    assert _lanc(client, c, itens[0], "1000000000000.00", "2026-02-01").status_code == 422


def test_excluir_pilar_tira_o_item_do_pilar_sem_perder_o_item(client, c):
    pilares = client.get(f"/projetos/{c['id']}/pilares", headers=c["h"]).json()
    ti = pilares[0]
    item = _item(client, c, pilar_id=ti["id"])
    assert client.delete(f"/projetos/{c['id']}/pilares/{ti['id']}", headers=c["h"]).status_code == 204
    r = client.get(f"/projetos/{c['id']}/orcamento", headers=c["h"]).json()
    assert r["itens"][0]["id"] == item["id"] and r["itens"][0]["pilar_id"] is None


def test_excluir_projeto_com_tudo(client, c):
    a, b = c["tarefa"]("A"), c["tarefa"]("B")
    c["dep"](a, b)
    client.post(f"/projetos/{c['id']}/marcos", json={"titulo": "M", "data": "2026-04-01"}, headers=c["h"])
    item = _item(client, c)
    _lanc(client, c, item, "10.00", "2026-02-01")
    assert client.delete(f"/projetos/{c['id']}", headers=c["h"]).status_code == 204
    assert client.get(f"/projetos/{c['id']}", headers=c["h"]).status_code == 404


# ---------- Marcos ----------


def test_marco_fora_do_periodo_das_tarefas_e_faixa_longa(client, c):
    c["tarefa"]("T", data_inicio="2026-03-02", data_fim="2026-03-06")
    for corpo in ({"titulo": "Antes", "data": "2025-01-01"},
                  {"titulo": "Década", "data": "2026-01-01", "data_fim": "2035-12-31", "forma": "faixa"}):
        assert client.post(f"/projetos/{c['id']}/marcos", json=corpo, headers=c["h"]).status_code == 201
    assert client.get(f"/projetos/{c['id']}/relatorio", headers=c["h"]).status_code == 200


def test_titulos_com_marcacao_nao_quebram_o_pdf(client, c):
    """O PDF interpreta <b>, <img>...: título de marco, item e fase precisam sair escapados."""
    perigoso = '<img src="/etc/passwd"/> & <b>negrito</b>'
    client.post(f"/projetos/{c['id']}/marcos", json={"titulo": perigoso[:80], "data": "2026-04-01", "descricao": perigoso},
                headers=c["h"])
    _item(client, c, descricao=perigoso[:150])
    client.patch(f"/projetos/{c['id']}/fases/{c['fases']['Execução']}", json={"titulo": "<b>Exec</b> & cia"}, headers=c["h"])
    r = client.get(f"/projetos/{c['id']}/relatorio", headers=c["h"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")


# ---------- Carga (achados do scripts/carga.sh de 16/11/2026) ----------


def test_get_db_e_assincrona_para_nao_travar_o_pool():
    """Síncrona, o fechamento da sessão disputa thread com as rotas: com 300 usuários o pool travou."""
    import inspect

    from app.database import get_db

    assert inspect.isasyncgenfunction(get_db)


def test_pool_esgotado_vira_503_com_retry_after(client, c, monkeypatch):
    from sqlalchemy.exc import TimeoutError as PoolEsgotado

    from app.routers import projetos as rotas

    def _sem_conexao(*a, **k):
        raise PoolEsgotado("QueuePool limit of size 10 overflow 10 reached")

    monkeypatch.setattr(rotas, "filtrar_projetos_visiveis", _sem_conexao)
    r = client.get("/projetos", headers=c["h"])
    assert r.status_code == 503 and r.headers["Retry-After"] == "5"
    assert "muitos acessos" in r.json()["detail"]


def test_argon2_respeita_o_teto_de_simultaneos(monkeypatch):
    import threading

    from app import security

    em_uso, pico, trava = [0], [0], threading.Lock()

    class HasherLento:
        def verify(self, _hash, _senha):
            with trava:
                em_uso[0] += 1
                pico[0] = max(pico[0], em_uso[0])
            time.sleep(0.05)
            with trava:
                em_uso[0] -= 1
            return True

    monkeypatch.setattr(security, "_hasher", HasherLento())
    monkeypatch.setattr(security, "_argon2_livres", threading.BoundedSemaphore(3))
    threads = [threading.Thread(target=security.verificar_senha, args=("x", "hash")) for _ in range(12)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert pico[0] == 3


def test_fila_de_entrada_limita_simultaneas_e_responde_503_quando_demora():
    import asyncio

    import httpx
    from starlette.responses import PlainTextResponse

    from app.limites import LimiteSimultaneas

    em_uso, pico = [0], [0]

    async def lenta(scope, receive, send):
        em_uso[0] += 1
        pico[0] = max(pico[0], em_uso[0])
        await asyncio.sleep(0.5 if scope["path"] != "/saude" else 0)
        em_uso[0] -= 1
        await PlainTextResponse("ok")(scope, receive, send)

    app = LimiteSimultaneas(lenta, maximo=2, espera_max=0.3)

    async def rodar():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            respostas = await asyncio.gather(*(c.get("/x") for _ in range(5)), c.get("/saude"))
        return respostas

    respostas = asyncio.run(rodar())
    codigos = sorted(r.status_code for r in respostas[:5])
    assert codigos == [200, 200, 503, 503, 503] and respostas[0].status_code in (200, 503)
    assert respostas[5].status_code == 200  # o health check não entra na fila
    assert pico[0] <= 3  # 2 na fila + o health check, que passa por fora
    assert next(r for r in respostas if r.status_code == 503).headers["Retry-After"] == "5"
