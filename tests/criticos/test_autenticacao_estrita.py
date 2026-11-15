"""
Cenário 3 — Autenticação estrita (login próprio + Clerk).

1. Inventário: TODA rota da API exige token, exceto uma lista explícita de rotas públicas.
   Uma rota nova esquecida sem `Depends(get_usuario_atual)` quebra este teste.
2. Header ausente, malformado, com esquema errado → 401 com WWW-Authenticate, sem revelar o motivo.
3. Tokens expirados, adulterados, do tipo errado ou de outro emissor → 401 (nunca 500).
"""

import re
from datetime import timedelta
from typing import List, Tuple

import jwt
import pytest

from app.config import settings
from app.integracoes import clerk
from app.main import app
from app.models import PapelUsuario
from app.security import agora_utc, criar_desafio_2fa
from tests.criticos.conftest import CHAVE_ATACANTE, CHAVE_CLERK, GERENTE, bearer, token_clerk

# Rotas que funcionam sem access token, cada uma com o motivo.
ROTAS_PUBLICAS = {
    ("GET", "/saude"),                # health check do balanceador
    ("POST", "/auth/login"),          # é onde se obtém o token
    ("POST", "/auth/login/2fa"),      # usa o desafio de 5 min, não o access token
    ("POST", "/auth/refresh"),        # usa o cookie httpOnly
    ("POST", "/auth/logout"),         # só apaga o cookie; funciona mesmo com sessão expirada
    ("POST", "/auth/esqueci-senha"),  # quem esqueceu a senha não tem token; resposta igual para todo e-mail
    ("POST", "/auth/redefinir-senha"),  # usa o link de 30 min do e-mail, não o access token
}
MENSAGEM_401 = {"detail": "Não autenticado."}


def _rotas_da_api() -> List[Tuple[str, str]]:
    """Pelo OpenAPI: interface estável (o FastAPI 0.16x agrupa as rotas incluídas em app.routes)."""
    metodos = {"get", "post", "put", "patch", "delete"}
    return sorted(
        (metodo.upper(), caminho)
        for caminho, operacoes in app.openapi()["paths"].items()
        for metodo in operacoes if metodo in metodos
    )


def test_inventario_enxerga_as_rotas():
    assert len(_rotas_da_api()) >= 30  # se cair muito, o inventário parou de ver as rotas


def _url(caminho: str) -> str:
    return re.sub(r"\{[^}]+\}", "1", caminho)


def test_lista_de_rotas_publicas_esta_atualizada():
    """Se uma rota pública for removida/renomeada, a lista acima precisa acompanhar."""
    assert ROTAS_PUBLICAS <= set(_rotas_da_api())


@pytest.mark.parametrize("metodo, caminho", [r for r in _rotas_da_api() if r not in ROTAS_PUBLICAS])
def test_toda_rota_privada_exige_token(client, metodo, caminho):
    r = client.request(metodo, _url(caminho))
    assert r.status_code == 401, f"{metodo} {caminho} respondeu {r.status_code} sem token"
    assert r.json() == MENSAGEM_401
    assert r.headers.get("www-authenticate") == "Bearer"


@pytest.mark.parametrize("metodo, caminho", [r for r in _rotas_da_api() if r not in ROTAS_PUBLICAS])
def test_toda_rota_privada_recusa_token_invalido(client, metodo, caminho):
    r = client.request(metodo, _url(caminho), headers=bearer("eyJhbGciOiJIUzI1NiJ9.e30.assinatura-falsa"))
    assert r.status_code == 401, f"{metodo} {caminho} respondeu {r.status_code} com token inválido"


# ---------- Header Authorization malformado ----------


@pytest.mark.parametrize("header", [
    None,
    "",
    "Bearer",
    "Bearer ",
    "Bearer    ",
    "Basic dXN1YXJpbzpzZW5oYQ==",
    "Token abc.def.ghi",
    "abc.def.ghi",                      # token sem o esquema
    "Bearer abc",
    "Bearer abc.def",
    "Bearer a.b.c.d.e",                 # formato JWE
    "Bearer " + "A" * 8000,             # token gigante
    "Bearer ../../etc/passwd",
    "Bearer null",
    "Bearer undefined",                 # bug clássico de front: `Bearer ${token}` com token vazio
], ids=lambda h: "ausente" if h is None else (h[:24] or "vazio"))
def test_header_malformado(client, header):
    headers = {} if header is None else {"Authorization": header}
    r = client.get("/projetos", headers=headers)
    assert r.status_code == 401, r.text
    assert r.json() == MENSAGEM_401


def test_token_na_query_string_nao_e_aceito(client, gerente):
    """Token em URL vaza em logs de proxy e no histórico do navegador."""
    token = gerente["Authorization"].removeprefix("Bearer ")
    assert client.get(f"/projetos?access_token={token}").status_code == 401
    assert client.get(f"/projetos?token={token}").status_code == 401


def test_esquema_bearer_e_case_insensitive(client, gerente):
    token = gerente["Authorization"].removeprefix("Bearer ")
    assert client.get("/projetos", headers={"Authorization": f"bearer {token}"}).status_code == 200


# ---------- Tokens do login próprio (HS256) ----------


def _token_proprio(usuario_id: int, chave: str = None, alg: str = "HS256", **sobrescrever) -> str:
    agora = agora_utc()
    claims = {"sub": str(usuario_id), "type": "access", "iss": settings.jwt_emissor,
              "iat": agora, "exp": agora + timedelta(minutes=5), "ver": 0}
    claims.update(sobrescrever)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, settings.secret_key if chave is None else chave, alg)


@pytest.fixture
def usuario_id(criar_usuario) -> int:
    return criar_usuario(GERENTE, PapelUsuario.GERENTE).id


def test_token_proprio_valido_e_aceito(client, usuario_id):
    assert client.get("/projetos", headers=bearer(_token_proprio(usuario_id))).status_code == 200


@pytest.mark.parametrize("sobrescrever", [
    pytest.param({"exp": agora_utc() - timedelta(seconds=1)}, id="expirado-ha-1s"),
    pytest.param({"exp": agora_utc() - timedelta(days=30)}, id="expirado-ha-30-dias"),
    pytest.param({"exp": None}, id="sem-exp"),
    pytest.param({"iat": None}, id="sem-iat"),
    pytest.param({"sub": None}, id="sem-sub"),
    pytest.param({"type": None}, id="sem-type"),
    pytest.param({"type": "refresh"}, id="tipo-refresh"),
    pytest.param({"type": "2fa"}, id="tipo-2fa"),
    pytest.param({"iss": "outro-sistema"}, id="outro-emissor"),
    pytest.param({"iss": None}, id="sem-emissor"),
    pytest.param({"sub": "abc"}, id="sub-texto"),
    pytest.param({"sub": "1.5"}, id="sub-decimal"),
    pytest.param({"sub": "-1"}, id="sub-negativo"),
    pytest.param({"sub": "99999"}, id="usuario-inexistente"),
    pytest.param({"sub": "9" * 30}, id="sub-gigante"),
    pytest.param({"ver": 1}, id="versao-de-sessao-antiga"),
    pytest.param({"ver": "abc"}, id="ver-texto"),
    pytest.param({"ver": [1]}, id="ver-lista"),
    pytest.param({"ver": {"a": 1}}, id="ver-objeto"),
])
def test_token_proprio_invalido(client, usuario_id, sobrescrever):
    r = client.get("/projetos", headers=bearer(_token_proprio(usuario_id, **sobrescrever)))
    assert r.status_code == 401, r.text
    assert r.json() == MENSAGEM_401


@pytest.mark.parametrize("chave, alg", [
    pytest.param("chave-de-outro-sistema" * 3, "HS256", id="outra-chave"),
    pytest.param(None, "HS512", id="algoritmo-trocado"),
    pytest.param(None, "HS384", id="hs384"),
    pytest.param(CHAVE_ATACANTE, "RS256", id="rs256-do-atacante"),
])
def test_token_proprio_assinatura_ou_algoritmo_errado(client, usuario_id, chave, alg):
    assert client.get("/projetos", headers=bearer(_token_proprio(usuario_id, chave=chave, alg=alg))).status_code == 401


def test_alg_none_e_recusado(client, usuario_id):
    agora = agora_utc()
    claims = {"sub": str(usuario_id), "type": "access", "iss": settings.jwt_emissor,
              "iat": agora, "exp": agora + timedelta(minutes=5)}
    token = jwt.encode(claims, None, "none")
    assert client.get("/projetos", headers=bearer(token)).status_code == 401


def test_desafio_do_2fa_nao_serve_como_access_token(client, usuario_id):
    assert client.get("/projetos", headers=bearer(criar_desafio_2fa(usuario_id, 0))).status_code == 401


def test_token_adulterado_no_payload(client, usuario_id, criar_usuario):
    """Troca o "sub" mantendo a assinatura original: tem de falhar."""
    outro = criar_usuario("admin@empresa.com.br", PapelUsuario.ADMIN)
    cabecalho, _, assinatura = _token_proprio(usuario_id).split(".")
    _, payload_admin, _ = _token_proprio(outro.id).split(".")
    assert client.get("/usuarios", headers=bearer(f"{cabecalho}.{payload_admin}.{assinatura}")).status_code == 401


def test_usuario_desativado_com_token_valido(client, usuario_id, criar_usuario, login):
    token = _token_proprio(usuario_id)
    criar_usuario("admin@empresa.com.br", PapelUsuario.ADMIN)
    client.patch(f"/usuarios/{usuario_id}", json={"ativo": False}, headers=login("admin@empresa.com.br"))
    assert client.get("/projetos", headers=bearer(token)).status_code == 401


# ---------- Tokens do Clerk (RS256) ----------


def test_token_clerk_valido(client, clerk_ligado):
    assert client.get("/auth/me", headers=bearer(token_clerk())).status_code == 200


@pytest.mark.parametrize("token", [
    pytest.param(lambda: token_clerk(exp=agora_utc() - timedelta(seconds=30)), id="expirado-ha-30s"),
    pytest.param(lambda: token_clerk(exp=agora_utc() - timedelta(hours=2)), id="expirado-ha-2h"),
    pytest.param(lambda: token_clerk(exp=None), id="sem-exp"),
    pytest.param(lambda: token_clerk(nbf=None), id="sem-nbf"),
    pytest.param(lambda: token_clerk(sub=None), id="sem-sub"),
    pytest.param(lambda: token_clerk(chave=CHAVE_ATACANTE), id="assinado-pelo-atacante"),
    pytest.param(lambda: token_clerk(chave="segredo" * 8, alg="HS256"), id="confusao-de-algoritmo"),
    pytest.param(lambda: token_clerk(iss="https://atacante.clerk.accounts.dev"), id="outro-emissor"),
    pytest.param(lambda: token_clerk(azp="https://site-malicioso.com"), id="azp-de-outro-site"),
    pytest.param(lambda: token_clerk(primaryEmail=None), id="sem-email"),
    pytest.param(lambda: token_clerk(primaryEmail="nao-e-email"), id="email-invalido"),
    pytest.param(lambda: token_clerk(primaryEmail=["a@b.com"]), id="email-lista"),
    pytest.param(lambda: token_clerk(nbf=agora_utc() + timedelta(minutes=10)), id="ainda-nao-valido"),
    pytest.param(lambda: token_clerk()[:-10] + "AAAAAAAAAA", id="assinatura-cortada"),
])
def test_token_clerk_invalido(client, clerk_ligado, token):
    r = client.get("/projetos", headers=bearer(token()))
    assert r.status_code == 401, r.text
    assert r.json() == MENSAGEM_401


def test_clerk_fora_do_ar_nao_vira_erro_500(client, monkeypatch):
    """JWKS inacessível (Clerk fora do ar ou sem rede): 401, e o motivo vai para o log."""
    monkeypatch.setattr(settings, "clerk_emissor", "https://teste.clerk.accounts.dev")

    def _jwks_fora_do_ar(token):
        raise jwt.PyJWKClientConnectionError("Fail to fetch data from the url")

    monkeypatch.setattr(clerk, "_chave_publica", _jwks_fora_do_ar)
    assert client.get("/projetos", headers=bearer(token_clerk())).status_code == 401


def test_clerk_com_kid_desconhecido(client, monkeypatch):
    monkeypatch.setattr(settings, "clerk_emissor", "https://teste.clerk.accounts.dev")

    def _kid_desconhecido(token):
        raise jwt.PyJWKClientError('Unable to find a signing key that matches: "k9"')

    monkeypatch.setattr(clerk, "_chave_publica", _kid_desconhecido)
    assert client.get("/projetos", headers=bearer(token_clerk())).status_code == 401


def test_clerk_desligado_ignora_token_do_clerk(client, monkeypatch):
    monkeypatch.setattr(settings, "clerk_emissor", None)
    monkeypatch.setattr(clerk, "_chave_publica", lambda token: CHAVE_CLERK.public_key())
    assert client.get("/projetos", headers=bearer(token_clerk())).status_code == 401


def test_token_proprio_com_emissor_do_clerk_nao_engana_o_roteamento(client, clerk_ligado, usuario_id):
    """Um HS256 com iss do Clerk é mandado ao validador do Clerk (RS256) e recusado lá."""
    token = _token_proprio(usuario_id, iss="https://teste.clerk.accounts.dev")
    assert client.get("/projetos", headers=bearer(token)).status_code == 401
