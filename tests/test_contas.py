"""Sessões, senhas e entradas: bloqueio de login, troca obrigatória, redefinição, política de senha, limite de corpo."""

from typing import Iterator, List

import httpx
import pytest

from app import senhas
from app.config import settings
from app.database import SessionLocal
from app.models import PapelUsuario, ProvedorAuth, Usuario
from app.rate_limit import limite_login, resetar_todos
from tests.conftest import SENHA, postar_importacao

ADMIN, ALVO, OUTRO = "admin@empresa.com.br", "alvo@empresa.com.br", "outro@empresa.com.br"
TEMPORARIA = "Temporaria-Cavalo-Bateria-7"
NOVA = "Grampo-Correto-Nuvem-42"


def _login(client, email: str, senha: str = SENHA) -> httpx.Response:
    return client.post("/auth/login", data={"username": email, "password": senha})


def _bearer(r: httpx.Response) -> dict:
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


# ---------- Bloqueio de login ----------


def test_bloqueio_por_email_e_ip_nao_revela_se_a_conta_existe(client, criar_usuario):
    criar_usuario(ALVO)
    criar_usuario(OUTRO)
    for email in (ALVO, "naoexiste@empresa.com.br"):
        for _ in range(settings.max_tentativas_login):
            _login(client, email, "errada-errada-1")
    limite_login.resetar()  # o limite geral por IP (10/min) não é o que este teste verifica

    existente = _login(client, ALVO)  # senha certa, mas barrado
    inexistente = _login(client, "naoexiste@empresa.com.br", "qualquer-coisa-1")
    assert existente.status_code == inexistente.status_code == 401
    assert existente.json() == inexistente.json()

    # Outra conta, do mesmo IP, continua entrando (o bloqueio é por e-mail + IP).
    assert _login(client, OUTRO).status_code == 200


def test_bloqueio_da_conta_contra_ataque_distribuido(client, criar_usuario, login):
    alvo = criar_usuario(ALVO)
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    with SessionLocal() as db:
        db.get(Usuario, alvo.id).tentativas_falhas = settings.max_tentativas_conta - 1
        db.commit()

    _login(client, ALVO, "errada-errada-1")
    resetar_todos()  # simula que a próxima tentativa vem de outro IP
    assert _login(client, ALVO).status_code == 401  # conta bloqueada, mesmo com a senha certa

    eventos = client.get("/auditoria", headers=login(ADMIN)).json()["itens"]
    acoes = [e["acao"] for e in eventos]
    assert "auth.conta_bloqueada" in acoes and "auth.login_barrado" in acoes


# ---------- Sessões ----------


def test_trocar_senha_invalida_o_access_token_na_hora(client, criar_usuario):
    criar_usuario(ALVO)
    h = _bearer(_login(client, ALVO))
    r = client.post("/auth/alterar-senha", json={"senha_atual": SENHA, "nova_senha": NOVA}, headers=h)
    assert r.status_code == 204
    # Sem esperar os 15 minutos: o token antigo morre junto com a senha antiga.
    assert client.get("/auth/me", headers=h).status_code == 401
    assert client.get("/auth/me", headers=_bearer(_login(client, ALVO, NOVA))).status_code == 200


def test_admin_encerra_sessoes(client, criar_usuario, login):
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    alvo = criar_usuario(ALVO)
    h_alvo = _bearer(_login(client, ALVO))
    cookie_alvo = client.cookies.get("pmo_refresh")

    assert client.post(f"/usuarios/{alvo.id}/encerrar-sessoes", headers=login(ADMIN)).status_code == 204
    assert client.get("/auth/me", headers=h_alvo).status_code == 401
    client.cookies.clear()
    client.cookies.set("pmo_refresh", cookie_alvo, path="/auth")
    assert client.post("/auth/refresh").status_code == 401
    assert _login(client, ALVO).status_code == 200  # pode entrar de novo


# ---------- Senha temporária e troca obrigatória ----------


def test_senha_definida_pelo_admin_exige_troca_no_primeiro_acesso(client, criar_usuario, login):
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    r = client.post("/usuarios", headers=login(ADMIN),
                    json={"email": ALVO, "nome": "Alvo", "senha": TEMPORARIA, "papel": "gerente"})
    assert r.status_code == 201 and r.json()["trocar_senha"] is True

    h = _bearer(_login(client, ALVO, TEMPORARIA))
    assert client.get("/auth/me", headers=h).json()["trocar_senha"] is True
    bloqueado = client.get("/projetos", headers=h)
    assert bloqueado.status_code == 403 and bloqueado.json()["detail"] == "Troque sua senha para continuar."
    assert client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).status_code == 403

    assert client.post("/auth/alterar-senha", json={"senha_atual": TEMPORARIA, "nova_senha": NOVA}, headers=h).status_code == 204
    h = _bearer(_login(client, ALVO, NOVA))
    assert client.get("/auth/me", headers=h).json()["trocar_senha"] is False
    assert client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).status_code == 201


def test_admin_redefine_senha_esquecida(client, criar_usuario, login):
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    alvo = criar_usuario(ALVO)
    h_antigo = _bearer(_login(client, ALVO))
    for _ in range(settings.max_tentativas_login):  # e ainda por cima se bloqueou
        _login(client, ALVO, "errada-errada-1")

    h_admin = login(ADMIN)
    r = client.post(f"/usuarios/{alvo.id}/redefinir-senha", json={"nova_senha": TEMPORARIA}, headers=h_admin)
    assert r.status_code == 204

    assert client.get("/auth/me", headers=h_antigo).status_code == 401  # sessão antiga caiu
    assert _login(client, ALVO).status_code == 401  # senha antiga não vale mais
    h = _bearer(_login(client, ALVO, TEMPORARIA))  # e o bloqueio foi desfeito
    assert client.get("/auth/me", headers=h).json()["trocar_senha"] is True

    historico = client.get("/auditoria?acao=usuario.senha_redefinida", headers=h_admin).json()
    assert historico["total"] == 1


def test_redefinir_senha_regras(client, criar_usuario, login):
    admin = criar_usuario(ADMIN, PapelUsuario.ADMIN)
    criar_usuario(OUTRO, PapelUsuario.GERENTE)
    with SessionLocal() as db:
        clerk = Usuario(email="clerk@empresa.com.br", nome="Clerk", provedor_auth=ProvedorAuth.CLERK)
        db.add(clerk)
        db.commit()
        clerk_id = clerk.id
    h = login(ADMIN)
    corpo = {"nova_senha": TEMPORARIA}
    assert client.post(f"/usuarios/{admin.id}/redefinir-senha", json=corpo, headers=h).status_code == 400  # a própria
    assert client.post(f"/usuarios/{clerk_id}/redefinir-senha", json=corpo, headers=h).status_code == 400
    assert client.post("/usuarios/99999/redefinir-senha", json=corpo, headers=h).status_code == 404
    assert client.post(f"/usuarios/{clerk_id}/redefinir-senha", json=corpo, headers=login(OUTRO)).status_code == 403


# ---------- Política de senha ----------


@pytest.mark.parametrize("senha", [
    "Senha123456!", "SENHA@2026!!", "Admin@2026!!", "mudarsenha2026", "123456789012",
    "aaaaaaaaaaaa", "qwertyuiop12", "abcdefghijkl", "Flamengo2026!", "!!!!!!!!1234",
])
def test_senhas_previsiveis_sao_recusadas(client, criar_usuario, login, senha):
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    r = client.post("/usuarios", headers=login(ADMIN), json={"email": ALVO, "nome": "Alvo", "senha": senha})
    assert r.status_code == 422, senha


def test_senha_com_nome_ou_email_e_recusada(client, criar_usuario):
    criar_usuario(ALVO)
    h = _bearer(_login(client, ALVO))
    for nova in ("Alvo-Empresa-Feliz-9", "meu-email-e-alvo!77"):
        r = client.post("/auth/alterar-senha", json={"senha_atual": SENHA, "nova_senha": nova}, headers=h)
        assert r.status_code == 422
        assert r.json()["detail"] == "Não use seu nome ou e-mail na senha."


class _RespostaPwned:
    def __init__(self, texto: str) -> None:
        self.text = texto

    def raise_for_status(self) -> None:
        pass


@pytest.fixture
def pwned(monkeypatch) -> Iterator[List[str]]:
    urls: List[str] = []
    monkeypatch.setattr(settings, "verificar_senhas_vazadas", True)
    yield urls


def test_senha_vazada_e_recusada_sem_enviar_a_senha(monkeypatch, pwned):
    import hashlib

    digest = hashlib.sha1(NOVA.encode()).hexdigest().upper()

    def _get(url, headers, timeout):
        pwned.append(url)
        assert headers["Add-Padding"] == "true"
        return _RespostaPwned(f"0000000000000000000000000000000000A:0\n{digest[5:]}:42\n")

    monkeypatch.setattr(senhas.httpx, "get", _get)
    with pytest.raises(senhas.SenhaFraca, match="vazamentos"):
        senhas.validar_senha(NOVA)
    # Só o prefixo de 5 caracteres do hash saiu do servidor.
    assert pwned == [senhas.URL_PWNED + digest[:5]]
    assert NOVA not in pwned[0]


def test_senha_so_no_padding_e_servico_fora_do_ar_nao_bloqueiam(monkeypatch, pwned):
    import hashlib

    digest = hashlib.sha1(NOVA.encode()).hexdigest().upper()
    monkeypatch.setattr(senhas.httpx, "get", lambda url, headers, timeout: _RespostaPwned(f"{digest[5:]}:0\n"))
    senhas.validar_senha(NOVA)  # aparece só como padding (0 ocorrências): aceita

    def _fora(url, headers, timeout):
        raise httpx.ConnectTimeout("timeout")

    monkeypatch.setattr(senhas.httpx, "get", _fora)
    senhas.validar_senha(NOVA)  # serviço fora do ar: não trava o usuário


# ---------- Limite de tamanho da requisição ----------


def test_corpo_grande_demais_e_recusado(client, criar_usuario, login):
    criar_usuario(ALVO, PapelUsuario.GERENTE)
    h = login(ALVO)
    enorme = {"nome_projeto": "ERP", "lixo": "x" * (2 * 1024 * 1024)}

    r = client.post("/projetos/manual", json=enorme, headers=h)
    assert r.status_code == 413

    # Envio em partes (sem Content-Length): os bytes são contados conforme chegam.
    def partes():
        yield b'{"nome_projeto": "ERP", "lixo": "'
        for _ in range(40):
            yield b"x" * 64 * 1024
        yield b'"}'

    r = client.post("/projetos/manual", content=partes(), headers={**h, "Content-Type": "application/json"})
    assert r.status_code == 413
    assert "maior que o permitido" in r.json()["detail"]

    # Requisições normais seguem funcionando.
    assert client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).status_code == 201


def test_upload_tem_limite_proprio(client, criar_usuario, login):
    criar_usuario(ALVO, PapelUsuario.GERENTE)
    h = login(ALVO)
    grande = b"Fase;Descricao\n" + b"Execucao;" + b"x" * (21 * 1024 * 1024) + b"\n"  # limite: 20 MB
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Grande"}, files={"arquivo": ("a.csv", grande)}, headers=h)
    assert r.status_code == 413
    # 1,3 MB passa do limite do JSON, mas cabe no limite do upload (chega na validação da planilha).
    medio = b"Fase;Descricao\n" + (b"Execucao;" + b"t" * 200 + b"\n") * 6_000
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Medio"}, files={"arquivo": ("a.csv", medio)}, headers=h)
    assert r.status_code != 413
