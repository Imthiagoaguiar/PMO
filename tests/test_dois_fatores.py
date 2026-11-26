"""Verificação em duas etapas (TOTP + códigos de recuperação)."""

import re
from typing import Dict, List, Tuple

import pyotp
import pytest

from app import dois_fatores
from app.config import settings
from app.database import SessionLocal
from app.models import PapelUsuario, ProvedorAuth, Usuario
from app.security import criar_access_token
from tests.conftest import SENHA

ADMIN, ALVO = "admin@empresa.com.br", "alvo@empresa.com.br"
PERIODO = dois_fatores.PERIODO_SEGUNDOS


class Relogio:
    def __init__(self) -> None:
        self.agora = 1_900_000_000.0  # um instante fixo: os testes não dependem da hora real

    def __call__(self) -> float:
        return self.agora


@pytest.fixture
def relogio(monkeypatch) -> Relogio:
    r = Relogio()
    monkeypatch.setattr(dois_fatores.time, "time", r)
    return r


def _codigo(segredo: str, relogio: Relogio, passos: int = 0) -> str:
    return pyotp.TOTP(segredo).at(relogio.agora + passos * PERIODO)


def _bearer(r) -> Dict[str, str]:
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def _login(client, email: str, senha: str = SENHA):
    return client.post("/auth/login", data={"username": email, "password": senha})


def _ativar(client, relogio: Relogio, email: str = ALVO) -> Tuple[str, List[str]]:
    h = _bearer(_login(client, email))
    segredo = client.post("/auth/2fa/iniciar", json={"senha": SENHA}, headers=h).json()["segredo"]
    r = client.post("/auth/2fa/ativar", json={"codigo": _codigo(segredo, relogio)}, headers=h)
    assert r.status_code == 200, r.text
    return segredo, r.json()["codigos_recuperacao"]


def _entrar_com_codigo(client, email: str, codigo: str):
    r = _login(client, email)
    assert r.status_code == 200 and r.json()["requer_2fa"] is True, r.text
    return client.post("/auth/login/2fa", json={"desafio": r.json()["desafio"], "codigo": codigo})


# ---------- Ativação ----------


def test_ativar_2fa(client, criar_usuario, relogio):
    usuario = criar_usuario(ALVO)
    h = _bearer(_login(client, ALVO))

    assert client.post("/auth/2fa/iniciar", json={"senha": "errada-errada"}, headers=h).status_code == 400
    r = client.post("/auth/2fa/iniciar", json={"senha": SENHA}, headers=h)
    assert r.status_code == 200
    segredo, uri = r.json()["segredo"], r.json()["uri"]
    assert uri.startswith("otpauth://totp/OMPY:alvo%40empresa.com.br?secret=")

    with SessionLocal() as db:  # o segredo fica cifrado no banco
        guardado = db.get(Usuario, usuario.id).totp_segredo_cifrado
        assert guardado and segredo not in guardado

    assert client.post("/auth/2fa/ativar", json={"codigo": "000000"}, headers=h).status_code == 400
    r = client.post("/auth/2fa/ativar", json={"codigo": _codigo(segredo, relogio)}, headers=h)
    assert r.status_code == 200
    codigos = r.json()["codigos_recuperacao"]
    assert len(codigos) == len(set(codigos)) == 10
    assert all(re.fullmatch(r"[A-Z2-9]{5}-[A-Z2-9]{5}", c) for c in codigos)

    # Ativar derruba as sessões: as próximas entradas já passam pelo 2FA.
    assert client.get("/auth/me", headers=h).status_code == 401


def test_login_em_duas_etapas_e_replay(client, criar_usuario, relogio):
    criar_usuario(ALVO)
    segredo, _ = _ativar(client, relogio)
    client.cookies.clear()

    r = _login(client, ALVO)
    assert r.status_code == 200
    corpo = r.json()
    assert corpo["requer_2fa"] is True and "access_token" not in corpo
    assert "pmo_refresh" not in r.cookies  # nenhuma sessão aberta só com a senha
    # O desafio não serve como token de acesso.
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {corpo['desafio']}"}).status_code == 401

    def segunda_etapa(codigo):
        return client.post("/auth/login/2fa", json={"desafio": corpo["desafio"], "codigo": codigo})

    # O código usado na ativação (e o anterior a ele) não vale de novo.
    assert segunda_etapa(_codigo(segredo, relogio)).status_code == 401
    assert segunda_etapa(_codigo(segredo, relogio, -1)).status_code == 401
    r = segunda_etapa(_codigo(segredo, relogio, +1))  # próximo código (relógio do celular adiantado)
    assert r.status_code == 200 and "pmo_refresh" in r.cookies
    assert client.get("/auth/me", headers=_bearer(r)).json()["dois_fatores_ativo"] is True
    assert segunda_etapa(_codigo(segredo, relogio, +1)).status_code == 401  # replay

    relogio.agora += 3 * PERIODO  # 1min30 depois, o código da vez funciona normalmente
    assert _entrar_com_codigo(client, ALVO, _codigo(segredo, relogio)).status_code == 200


def test_codigos_errados_contam_para_o_bloqueio(client, criar_usuario, relogio):
    criar_usuario(ALVO)
    segredo, _ = _ativar(client, relogio)
    relogio.agora += 2 * PERIODO

    # Ataque de quem só tem a senha: chuta quase o limite de códigos, entra com a senha de novo
    # (desafio novo) e continua chutando. Acertar a senha NÃO pode zerar o contador.
    desafio = _login(client, ALVO).json()["desafio"]
    for i in range(settings.max_tentativas_login - 1):
        client.post("/auth/login/2fa", json={"desafio": desafio, "codigo": f"{i:06d}"})
    desafio = _login(client, ALVO).json()["desafio"]
    client.post("/auth/login/2fa", json={"desafio": desafio, "codigo": "999999"})

    r = client.post("/auth/login/2fa", json={"desafio": desafio, "codigo": _codigo(segredo, relogio)})
    assert r.status_code == 401 and "Muitas tentativas" in r.json()["detail"]
    assert _login(client, ALVO).status_code == 401  # e a senha também fica barrada


def test_desafio_invalido(client, criar_usuario, login, relogio):
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    alvo = criar_usuario(ALVO)
    segredo, _ = _ativar(client, relogio)
    relogio.agora += 2 * PERIODO
    codigo = _codigo(segredo, relogio)

    for desafio in ("lixo", criar_access_token(alvo.id, 1)):  # um access token não serve como desafio
        assert client.post("/auth/login/2fa", json={"desafio": desafio, "codigo": codigo}).status_code == 401

    desafio = _login(client, ALVO).json()["desafio"]
    client.post(f"/usuarios/{alvo.id}/encerrar-sessoes", headers=login(ADMIN))  # muda a versão da sessão
    r = client.post("/auth/login/2fa", json={"desafio": desafio, "codigo": codigo})
    assert r.status_code == 401 and "Entre novamente" in r.json()["detail"]


# ---------- Códigos de recuperação ----------


def test_codigos_de_recuperacao(client, criar_usuario, relogio):
    criar_usuario(ALVO)
    segredo, codigos = _ativar(client, relogio)

    # Sem o celular: entra com um código de recuperação (aceita minúsculas e sem o hífen).
    r = _entrar_com_codigo(client, ALVO, codigos[0].lower().replace("-", ""))
    assert r.status_code == 200
    h = _bearer(r)
    assert client.get("/auth/2fa", headers=h).json() == {"ativo": True, "obrigatorio": False, "codigos_restantes": 9}
    assert _entrar_com_codigo(client, ALVO, codigos[0]).status_code == 401  # uso único

    # Gerar novos invalida os antigos.
    relogio.agora += 2 * PERIODO
    r = client.post("/auth/2fa/codigos", json={"senha": SENHA, "codigo": _codigo(segredo, relogio)}, headers=h)
    assert r.status_code == 200
    novos = r.json()["codigos_recuperacao"]
    assert _entrar_com_codigo(client, ALVO, codigos[1]).status_code == 401
    assert _entrar_com_codigo(client, ALVO, novos[0]).status_code == 200


# ---------- Desativar / redefinir ----------


def test_desativar_2fa(client, criar_usuario, relogio):
    criar_usuario(ALVO)
    segredo, _ = _ativar(client, relogio)
    relogio.agora += 2 * PERIODO
    h = _bearer(_entrar_com_codigo(client, ALVO, _codigo(segredo, relogio)))
    relogio.agora += 2 * PERIODO

    assert client.post("/auth/2fa/desativar", json={"senha": SENHA, "codigo": "123456"}, headers=h).status_code == 400
    assert client.post("/auth/2fa/desativar", json={"senha": "errada-errada", "codigo": _codigo(segredo, relogio)}, headers=h).status_code == 400
    r = client.post("/auth/2fa/desativar", json={"senha": SENHA, "codigo": _codigo(segredo, relogio)}, headers=h)
    assert r.status_code == 204
    assert "access_token" in _login(client, ALVO).json()  # volta a entrar só com a senha


def test_admin_redefine_2fa_de_quem_perdeu_o_celular(client, criar_usuario, login, relogio):
    admin = criar_usuario(ADMIN, PapelUsuario.ADMIN)
    alvo = criar_usuario(ALVO)
    _ativar(client, relogio)
    h_admin = login(ADMIN)

    assert client.post(f"/usuarios/{alvo.id}/redefinir-2fa", headers=h_admin).status_code == 204
    assert "access_token" in _login(client, ALVO).json()
    assert client.post(f"/usuarios/{alvo.id}/redefinir-2fa", headers=h_admin).status_code == 400  # já sem 2FA
    assert client.post(f"/usuarios/{admin.id}/redefinir-2fa", headers=h_admin).status_code == 400  # o próprio
    assert client.get("/auditoria?acao=usuario.2fa_redefinido", headers=h_admin).json()["total"] == 1


# ---------- Obrigatório para administradores ----------


def test_admin_sem_2fa_so_consegue_ativar(client, criar_usuario, relogio, monkeypatch):
    monkeypatch.setattr(settings, "exigir_2fa_admin", True)
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    h = _bearer(_login(client, ADMIN))

    r = client.get("/usuarios", headers=h)
    assert r.status_code == 403 and r.json()["detail"] == "Ative a verificação em duas etapas para continuar."
    assert client.get("/auth/me", headers=h).json()["exige_configurar_2fa"] is True

    segredo = client.post("/auth/2fa/iniciar", json={"senha": SENHA}, headers=h).json()["segredo"]
    assert client.post("/auth/2fa/ativar", json={"codigo": _codigo(segredo, relogio)}, headers=h).status_code == 200

    h = _bearer(_entrar_com_codigo(client, ADMIN, _codigo(segredo, relogio, +1)))
    assert client.get("/usuarios", headers=h).status_code == 200
    relogio.agora += 2 * PERIODO
    r = client.post("/auth/2fa/desativar", json={"senha": SENHA, "codigo": _codigo(segredo, relogio)}, headers=h)
    assert r.status_code == 400 and "Administradores" in r.json()["detail"]


def test_regra_vale_so_para_admin_com_login_local(monkeypatch):
    monkeypatch.setattr(settings, "exigir_2fa_admin", True)
    assert Usuario(papel=PapelUsuario.ADMIN, provedor_auth=ProvedorAuth.LOCAL, totp_ativo=False).exige_configurar_2fa
    assert not Usuario(papel=PapelUsuario.ADMIN, provedor_auth=ProvedorAuth.CLERK, totp_ativo=False).exige_configurar_2fa
    assert not Usuario(papel=PapelUsuario.GERENTE, provedor_auth=ProvedorAuth.LOCAL, totp_ativo=False).exige_configurar_2fa
    assert not Usuario(papel=PapelUsuario.ADMIN, provedor_auth=ProvedorAuth.LOCAL, totp_ativo=True).exige_configurar_2fa


def test_usuario_do_clerk_nao_configura_2fa_aqui(client, criar_usuario):
    criar_usuario(ALVO)
    with SessionLocal() as db:
        u = db.query(Usuario).filter_by(email=ALVO).one()
        u.provedor_auth = ProvedorAuth.CLERK
        db.commit()
    h = _bearer(_login(client, ALVO))
    r = client.post("/auth/2fa/iniciar", json={"senha": SENHA}, headers=h)
    assert r.status_code == 400 and "provedor externo" in r.json()["detail"]
