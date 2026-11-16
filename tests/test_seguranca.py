from datetime import timedelta

import jwt

from app.config import settings
from app.models import PapelUsuario
from app.security import agora_utc
from tests.conftest import SENHA, postar_importacao

ADMIN, GERENTE, GERENTE2, MEMBRO = (
    "admin@empresa.com.br",
    "gerente@empresa.com.br",
    "gerente2@empresa.com.br",
    "membro@empresa.com.br",
)


def _cenario(criar_usuario):
    return {
        "admin": criar_usuario(ADMIN, PapelUsuario.ADMIN),
        "gerente": criar_usuario(GERENTE, PapelUsuario.GERENTE),
        "gerente2": criar_usuario(GERENTE2, PapelUsuario.GERENTE),
        "membro": criar_usuario(MEMBRO, PapelUsuario.MEMBRO),
    }


# ---------- Autenticação ----------


def test_rotas_exigem_token(client):
    assert client.get("/projetos").status_code == 401
    assert client.post("/projetos/manual", json={"nome_projeto": "X"}).status_code == 401
    assert client.get("/usuarios").status_code == 401


def test_token_adulterado_ou_expirado_e_rejeitado(client, criar_usuario):
    usuario = criar_usuario(GERENTE, PapelUsuario.GERENTE)
    base = {"sub": str(usuario.id), "type": "access", "iss": settings.jwt_emissor, "iat": agora_utc()}

    outra_chave = jwt.encode({**base, "exp": agora_utc() + timedelta(minutes=5)}, "chave-errada" * 4, "HS256")
    expirado = jwt.encode({**base, "exp": agora_utc() - timedelta(minutes=1)}, settings.secret_key, "HS256")
    sem_assinatura = jwt.encode({**base, "exp": agora_utc() + timedelta(minutes=5)}, None, "none")

    for token in (outra_chave, expirado, sem_assinatura, "lixo"):
        r = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401


def test_login_invalido_tem_mensagem_generica(client, criar_usuario):
    criar_usuario(MEMBRO)
    r1 = client.post("/auth/login", data={"username": MEMBRO, "password": "errada-errada"})
    r2 = client.post("/auth/login", data={"username": "naoexiste@empresa.com.br", "password": "x"})
    assert r1.status_code == r2.status_code == 401
    assert r1.json() == r2.json()  # não revela se o e-mail existe


def test_bloqueio_apos_tentativas(client, criar_usuario):
    criar_usuario(MEMBRO)
    for _ in range(settings.max_tentativas_login):
        errada = client.post("/auth/login", data={"username": MEMBRO, "password": "errada-errada"})
    r = client.post("/auth/login", data={"username": MEMBRO, "password": SENHA})
    # Barrado mesmo com a senha correta, e com a mesma resposta de senha errada (não revela o bloqueio).
    assert r.status_code == 401
    assert r.json() == errada.json()


def test_usuario_desativado_perde_acesso_na_hora(client, criar_usuario, login):
    _cenario(criar_usuario)
    h_admin, h_membro = login(ADMIN), login(MEMBRO)
    membro_id = client.get("/auth/me", headers=h_membro).json()["id"]

    assert client.patch(f"/usuarios/{membro_id}", json={"ativo": False}, headers=h_admin).status_code == 200
    assert client.get("/auth/me", headers=h_membro).status_code == 401


def test_refresh_rotaciona_e_detecta_reuso(client, criar_usuario, monkeypatch):
    # Sem a janela de corrida entre abas (ver test_robustez.py), todo reuso é tratado como roubo.
    monkeypatch.setattr(settings, "janela_renovacao_paralela_segundos", 0)
    criar_usuario(MEMBRO)
    r = client.post("/auth/login", data={"username": MEMBRO, "password": SENHA})
    cookie_original = r.cookies["pmo_refresh"]
    assert "httponly" in r.headers["set-cookie"].lower()
    assert "samesite=strict" in r.headers["set-cookie"].lower()

    r = client.post("/auth/refresh")
    assert r.status_code == 200
    cookie_novo = r.cookies["pmo_refresh"]
    assert cookie_novo != cookie_original

    # Um atacante reaproveita o token antigo -> rejeitado e todas as sessões caem.
    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_original, path="/auth")
    assert client.post("/auth/refresh").status_code == 401

    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_novo, path="/auth")
    assert client.post("/auth/refresh").status_code == 401


def test_logout_invalida_refresh(client, criar_usuario):
    criar_usuario(MEMBRO)
    r = client.post("/auth/login", data={"username": MEMBRO, "password": SENHA})
    cookie = r.cookies["pmo_refresh"]
    assert client.post("/auth/logout").status_code == 204

    client.cookies.set("pmo_refresh", cookie, path="/auth")
    assert client.post("/auth/refresh").status_code == 401


def test_alterar_senha_derruba_sessoes(client, criar_usuario, login):
    criar_usuario(MEMBRO)
    h = login(MEMBRO)
    cookie = client.cookies.get("pmo_refresh")
    nova = "OutraSenhaForte!2026"
    r = client.post("/auth/alterar-senha", json={"senha_atual": SENHA, "nova_senha": nova}, headers=h)
    assert r.status_code == 204

    client.cookies.set("pmo_refresh", cookie, path="/auth")
    assert client.post("/auth/refresh").status_code == 401
    assert client.post("/auth/login", data={"username": MEMBRO, "password": SENHA}).status_code == 401
    assert client.post("/auth/login", data={"username": MEMBRO, "password": nova}).status_code == 200


# ---------- Autorização ----------


def test_somente_admin_gerencia_usuarios(client, criar_usuario, login):
    _cenario(criar_usuario)
    novo = {"email": "novo@empresa.com.br", "nome": "Novo", "senha": "SenhaForte!2026"}
    assert client.post("/usuarios", json=novo, headers=login(GERENTE)).status_code == 403
    assert client.post("/usuarios", json=novo, headers=login(ADMIN)).status_code == 201
    assert client.post("/usuarios", json=novo, headers=login(ADMIN)).status_code == 409


def test_senha_fraca_rejeitada(client, criar_usuario, login):
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    r = client.post(
        "/usuarios",
        json={"email": "x@empresa.com.br", "nome": "X", "senha": "curta"},
        headers=login(ADMIN),
    )
    assert r.status_code == 422


def test_admin_nao_se_desativa(client, criar_usuario, login):
    admin = criar_usuario(ADMIN, PapelUsuario.ADMIN)
    r = client.patch(f"/usuarios/{admin.id}", json={"ativo": False}, headers=login(ADMIN))
    assert r.status_code == 400


def test_membro_nao_cria_projeto(client, criar_usuario, login):
    criar_usuario(MEMBRO)
    r = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=login(MEMBRO))
    assert r.status_code == 403


def test_isolamento_de_projetos_por_membro(client, criar_usuario, login):
    u = _cenario(criar_usuario)
    h_gerente, h_gerente2, h_membro, h_admin = login(GERENTE), login(GERENTE2), login(MEMBRO), login(ADMIN)

    r = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h_gerente)
    assert r.status_code == 201
    projeto = r.json()
    assert projeto["dono_id"] == u["gerente"].id
    assert len(projeto["fases"]) == 5
    pid = projeto["id"]

    # Outro gerente e o membro não enxergam (404, sem revelar existência).
    assert client.get(f"/projetos/{pid}", headers=h_gerente2).status_code == 404
    assert client.get("/projetos", headers=h_membro).json()["itens"] == []
    # Admin enxerga tudo.
    assert client.get(f"/projetos/{pid}", headers=h_admin).status_code == 200

    # Quem não é dono não adiciona membros.
    r = client.post(f"/projetos/{pid}/membros", json={"usuario_id": u["membro"].id}, headers=h_gerente2)
    assert r.status_code == 404

    # O dono adiciona o membro, que passa a ver o projeto, mas não pode gerenciá-lo.
    r = client.post(f"/projetos/{pid}/membros", json={"usuario_id": u["membro"].id}, headers=h_gerente)
    assert r.status_code == 201
    assert client.get(f"/projetos/{pid}", headers=h_membro).status_code == 200
    r = client.post(f"/projetos/{pid}/membros", json={"usuario_id": u["gerente2"].id}, headers=h_membro)
    assert r.status_code == 403

    # O dono não pode ser removido; o membro pode.
    assert client.delete(f"/projetos/{pid}/membros/{u['gerente'].id}", headers=h_gerente).status_code == 400
    assert client.delete(f"/projetos/{pid}/membros/{u['membro'].id}", headers=h_gerente).status_code == 204
    assert client.get(f"/projetos/{pid}", headers=h_membro).status_code == 404


# ---------- Importação ----------


def test_importacao_autenticada(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    with open("exemplos/cronograma_exemplo.csv", "rb") as f:
        r = postar_importacao(client,
            "/projetos/importar",
            data={"nome_projeto": "Importado"},
            files={"arquivo": ("cronograma.csv", f.read())},
            headers=login(GERENTE),
        )
    assert r.status_code == 201
    assert r.json()["total_tarefas_importadas"] == 7


def test_xlsx_corrompido_nao_vaza_detalhes(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    r = postar_importacao(client,
        "/projetos/importar",
        data={"nome_projeto": "Ruim"},
        files={"arquivo": ("p.xlsx", b"isto nao e um zip")},
        headers=login(GERENTE),
    )
    assert r.status_code == 422
    assert r.json()["detail"]["mensagem"] == "Arquivo .xlsx inválido ou corrompido."


# ---------- Headers ----------


def test_headers_de_seguranca(client):
    r = client.get("/projetos")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["cache-control"] == "no-store"


def test_testes_nunca_usam_as_integracoes_reais_do_env():
    """Com chaves no .env, a suíte mandaria e-mails de verdade e gastaria tokens (aconteceu em 15/11/2026)."""
    from app.config import settings

    assert not settings.resend_api_key and not settings.groq_api_key and not settings.clerk_emissor
