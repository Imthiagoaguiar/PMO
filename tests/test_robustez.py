"""
Bugs de uso real: escritório atrás de um único IP e várias abas abertas ao mesmo tempo.
"""

import json
from datetime import timedelta

from app.config import settings
from app.database import SessionLocal
from app.integracoes import ia
from app.models import PapelUsuario, RefreshToken
from app.security import agora_utc
from tests.conftest import SENHA

TEXTO_TAP = "Termo de Abertura: implantar o novo ERP financeiro na matriz e nas 3 filiais até junho. " * 2


def _login(client, email: str, senha: str = SENHA):
    return client.post("/auth/login", data={"username": email, "password": senha})


# ---------- Escritório: todo mundo sai pelo mesmo IP (NAT) ----------


def test_escritorio_inteiro_entra_no_mesmo_minuto(client, criar_usuario):
    """No TestClient todas as requisições vêm do mesmo IP, como num escritório."""
    emails = [f"pessoa{i}@empresa.com.br" for i in range(30)]
    for email in emails:
        criar_usuario(email)
    for email in emails:
        r = _login(client, email)
        assert r.status_code == 200, f"{email}: {r.status_code} {r.text}"


def test_ataque_de_varias_contas_a_partir_de_um_ip_continua_barrado(client, criar_usuario):
    """Password spraying: uma senha comum testada em muitas contas, do mesmo IP."""
    criar_usuario("vitima@empresa.com.br")
    for i in range(settings.max_falhas_por_ip):
        _login(client, f"conta{i}@empresa.com.br", "Primavera2026!")
    # Mesmo com a senha certa, o IP que está atacando fica barrado (resposta genérica).
    r = _login(client, "vitima@empresa.com.br")
    assert r.status_code == 401


def test_limite_da_ia_e_por_pessoa_nao_por_ip(client, criar_usuario, login, monkeypatch):
    resposta = {"nome_projeto": "ERP", "tarefas": [{"fase": "Iniciação", "descricao": "TAP"}]}
    monkeypatch.setattr(ia, "_chamar_groq", lambda _t: json.dumps(resposta))
    criar_usuario("ana@empresa.com.br", PapelUsuario.GERENTE)
    criar_usuario("bruno@empresa.com.br", PapelUsuario.GERENTE)
    h_ana, h_bruno = login("ana@empresa.com.br"), login("bruno@empresa.com.br")

    for _ in range(5):
        assert client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=h_ana).status_code == 200
    assert client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=h_ana).status_code == 429
    # Colega no mesmo escritório (mesmo IP) não é afetado.
    assert client.post("/projetos/ia", json={"texto": TEXTO_TAP}, headers=h_bruno).status_code == 200


# ---------- Várias abas renovando a sessão ao mesmo tempo ----------


def test_duas_abas_renovando_juntas_nao_derrubam_a_sessao(client, criar_usuario):
    criar_usuario("ana@empresa.com.br")
    _login(client, "ana@empresa.com.br")
    cookie_compartilhado = client.cookies.get("pmo_refresh")

    # Aba 1 renova primeiro.
    r1 = client.post("/auth/refresh")
    assert r1.status_code == 200
    cookie_novo = client.cookies.get("pmo_refresh")

    # Aba 2 tinha mandado o MESMO cookie quase ao mesmo tempo (antes de ver o novo).
    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_compartilhado, path="/auth")
    r2 = client.post("/auth/refresh")
    assert r2.status_code == 409  # "outra aba já renovou": tente de novo com o cookie atual

    # Nada foi revogado: com o cookie atual (que o navegador já tem), a sessão segue viva.
    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_novo, path="/auth")
    assert client.post("/auth/refresh").status_code == 200


def test_reuso_fora_da_janela_continua_sendo_tratado_como_roubo(client, criar_usuario):
    criar_usuario("ana@empresa.com.br")
    _login(client, "ana@empresa.com.br")
    cookie_roubado = client.cookies.get("pmo_refresh")
    client.post("/auth/refresh")
    cookie_legitimo = client.cookies.get("pmo_refresh")

    with SessionLocal() as db:  # a renovação legítima foi há 5 minutos
        for t in db.query(RefreshToken).filter(RefreshToken.substituido_em.isnot(None)):
            t.substituido_em = agora_utc() - timedelta(minutes=5)
        db.commit()

    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_roubado, path="/auth")
    assert client.post("/auth/refresh").status_code == 401
    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_legitimo, path="/auth")
    assert client.post("/auth/refresh").status_code == 401  # todas as sessões caíram
