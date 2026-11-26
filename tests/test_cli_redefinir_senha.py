"""`python -m app.cli redefinir-senha`: a saída para quem esqueceu a senha e não recebe o e-mail."""

from datetime import timedelta

import pytest
from sqlalchemy import select

from app import cli
from app.database import SessionLocal
from app.models import Auditoria, PapelUsuario, Usuario
from app.security import agora_utc

NOVA = "Cadeira-Verde-Janela-7731"


def _senhas(monkeypatch, *valores):
    respostas = iter(valores)
    monkeypatch.setattr(cli.getpass, "getpass", lambda _: next(respostas))


def test_redefine_desbloqueia_e_derruba_sessoes(client, criar_usuario, login, monkeypatch):
    u = criar_usuario("chefe@empresa.com.br", PapelUsuario.ADMIN)
    antiga = login("chefe@empresa.com.br")
    with SessionLocal() as db:
        db.get(Usuario, u.id).tentativas_falhas = 9
        db.get(Usuario, u.id).bloqueado_ate = agora_utc() + timedelta(hours=1)
        db.commit()
    _senhas(monkeypatch, NOVA, NOVA)
    cli.redefinir_senha("Chefe@Empresa.com.br", desligar_2fa=False)
    assert client.get("/auth/me", headers=antiga).status_code == 401  # sessão antiga derrubada
    login("chefe@empresa.com.br", NOVA)
    with SessionLocal() as db:
        assert db.scalar(select(Auditoria.acao).where(Auditoria.acao == "usuario.senha_redefinida_cli"))


def test_desliga_o_2fa_so_quando_pedido(client, criar_usuario, monkeypatch):
    u = criar_usuario("chefe@empresa.com.br", PapelUsuario.ADMIN)
    with SessionLocal() as db:
        db.get(Usuario, u.id).totp_ativo = True
        db.commit()
    _senhas(monkeypatch, NOVA, NOVA)
    cli.redefinir_senha("chefe@empresa.com.br", desligar_2fa=False)
    with SessionLocal() as db:
        assert db.get(Usuario, u.id).totp_ativo
    _senhas(monkeypatch, NOVA, NOVA)
    cli.redefinir_senha("chefe@empresa.com.br", desligar_2fa=True)
    with SessionLocal() as db:
        assert not db.get(Usuario, u.id).totp_ativo


@pytest.mark.parametrize("email, senhas, erro", [
    ("ninguem@empresa.com.br", (NOVA, NOVA), "Nenhum usuário"),
    ("chefe@empresa.com.br", (NOVA, NOVA + "x"), "não conferem"),
    ("chefe@empresa.com.br", ("curta", "curta"), "de 12 a 128 caracteres"),
    ("chefe@empresa.com.br", ("chefe-chefe-chefe", "chefe-chefe-chefe"), ""),  # fraca (validar_senha)
])
def test_recusas(client, criar_usuario, monkeypatch, email, senhas, erro):
    criar_usuario("chefe@empresa.com.br")
    _senhas(monkeypatch, *senhas)
    with pytest.raises(SystemExit) as saida:
        cli.redefinir_senha(email, desligar_2fa=False)
    assert erro in str(saida.value.code)
