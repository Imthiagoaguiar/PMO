"""
"Esqueci minha senha": link por e-mail, de 30 minutos e uso único.

O que é garantido:
- a resposta do pedido é a mesma para qualquer e-mail (não revela quem tem conta);
- conta desativada ou do Clerk não recebe link;
- o link vale UMA vez e morre se a senha mudar por outro caminho;
- a redefinição derruba as sessões abertas, desbloqueia a conta e mantém o 2FA;
- ninguém lota a caixa de alguém pedindo links (limite por e-mail e por IP);
- sem o Resend, o link só aparece no log em desenvolvimento, nunca em produção.
"""

import logging
import re
from datetime import timedelta
from typing import List, Tuple

import jwt
import pytest

from app.config import Settings, settings
from app.database import SessionLocal
from app.integracoes import notificacoes
from app.models import ProvedorAuth, Usuario
from app.security import agora_utc, criar_access_token, criar_desafio_2fa, criar_token_redefinicao
from tests.conftest import SENHA

ANA = "ana@empresa.com.br"
NOVA = "Ponte-Azul-Gaveta-2919"
MENSAGEM = "Se o e-mail estiver cadastrado, enviamos um link"


@pytest.fixture
def emails(monkeypatch) -> List[Tuple[str, str, str]]:
    enviados: List[Tuple[str, str, str]] = []
    monkeypatch.setattr(notificacoes, "enviar_redefinicao_senha", lambda *a: enviados.append(a))
    return enviados


def _pedir(client, email: str = ANA):
    return client.post("/auth/esqueci-senha", json={"email": email})


def _token(enviados) -> str:
    link = enviados[-1][2]
    assert link.startswith(settings.url_do_front + "/redefinir-senha#token=")
    return link.split("#token=", 1)[1]


def _redefinir(client, token: str, senha: str = NOVA):
    return client.post("/auth/redefinir-senha", json={"token": token, "nova_senha": senha})


def _entrar(client, senha: str, email: str = ANA):
    return client.post("/auth/login", data={"username": email, "password": senha})


def test_fluxo_completo(client, criar_usuario, emails):
    criar_usuario(ANA)
    sessao_antiga = _entrar(client, SENHA).json()["access_token"]

    r = _pedir(client, "  Ana@Empresa.com.br ")  # o e-mail é normalizado como no login
    assert r.status_code == 202 and r.json()["mensagem"].startswith(MENSAGEM)
    assert len(emails) == 1 and emails[0][0] == ANA

    assert _redefinir(client, _token(emails)).status_code == 204
    assert _entrar(client, NOVA).status_code == 200
    assert _entrar(client, SENHA).status_code == 401
    # Quem estava logado (talvez quem roubou a senha antiga) cai na hora.
    assert client.get("/auth/me", headers={"Authorization": f"Bearer {sessao_antiga}"}).status_code == 401


def test_link_vale_uma_vez(client, criar_usuario, emails):
    criar_usuario(ANA)
    _pedir(client)
    token = _token(emails)
    assert _redefinir(client, token).status_code == 204
    r = _redefinir(client, token, "Outra-Senha-Bem-Longa-77")
    assert r.status_code == 400 and "já usado" in r.json()["detail"]
    assert _entrar(client, NOVA).status_code == 200  # a segunda tentativa não sobrescreveu


def test_link_morre_quando_a_senha_muda_por_outro_caminho(client, criar_usuario, login, emails):
    criar_usuario(ANA)
    _pedir(client)
    token = _token(emails)
    h = login(ANA)
    assert client.post("/auth/alterar-senha", json={"senha_atual": SENHA, "nova_senha": "Trocada-Pela-Tela-2026"},
                       headers=h).status_code == 204
    assert _redefinir(client, token).status_code == 400


def test_so_o_link_mais_novo_importa_depois_do_uso(client, criar_usuario, emails):
    """Dois links pedidos em seguida: usar um invalida o outro (a senha e a versão mudaram)."""
    criar_usuario(ANA)
    _pedir(client)
    _pedir(client)
    primeiro, segundo = (e[2].split("#token=")[1] for e in emails)
    assert _redefinir(client, segundo).status_code == 204
    assert _redefinir(client, primeiro).status_code == 400


@pytest.mark.parametrize("caso", ["inexistente", "desativada", "clerk"])
def test_resposta_igual_e_nenhum_email_para_quem_nao_pode(client, criar_usuario, emails, caso):
    if caso == "desativada":
        criar_usuario(ANA, ativo=False)
    elif caso == "clerk":
        usuario = criar_usuario(ANA)
        with SessionLocal() as db:
            db.get(Usuario, usuario.id).provedor_auth = ProvedorAuth.CLERK
            db.commit()
    referencia = criar_usuario("bia@empresa.com.br")
    assert referencia

    r, r_ref = _pedir(client), _pedir(client, "bia@empresa.com.br")
    assert (r.status_code, r.json()) == (r_ref.status_code, r_ref.json())
    assert [e[0] for e in emails] == ["bia@empresa.com.br"]


def test_token_errado_vencido_ou_de_outro_tipo(client, criar_usuario, emails):
    usuario = criar_usuario(ANA)
    with SessionLocal() as db:
        u = db.get(Usuario, usuario.id)
        vencido = jwt.encode(
            {"sub": str(u.id), "type": "redefinir_senha", "iss": settings.jwt_emissor,
             "iat": agora_utc() - timedelta(hours=2), "exp": agora_utc() - timedelta(hours=1),
             "ver": u.versao_sessao, "pwd": "x"},
            settings.secret_key, algorithm=settings.jwt_algoritmo,
        )
        outra_chave = jwt.encode(
            {"sub": str(u.id), "type": "redefinir_senha", "iss": settings.jwt_emissor, "iat": agora_utc(),
             "exp": agora_utc() + timedelta(minutes=5), "ver": u.versao_sessao, "pwd": "x"},
            "chave-de-quem-quer-forjar-o-link-xxxxxxxxxxxxxxxxxx", algorithm=settings.jwt_algoritmo,
        )
        de_outro_tipo = [criar_access_token(u.id, u.versao_sessao), criar_desafio_2fa(u.id, u.versao_sessao)]
    for token in ["", "abc", vencido, outra_chave, *de_outro_tipo]:
        r = _redefinir(client, token)
        assert r.status_code in (400, 422), token
        if r.status_code == 400:
            assert "Link inválido" in r.json()["detail"]
    assert _entrar(client, SENHA).status_code == 200  # nada mudou


def test_link_de_outra_pessoa_nao_vale_para_mim(client, criar_usuario, emails):
    """A impressão da senha está presa à conta: trocar o "sub" quebra a assinatura do JWT."""
    criar_usuario(ANA)
    bia = criar_usuario("bia@empresa.com.br")
    _pedir(client)
    payload = jwt.decode(_token(emails), options={"verify_signature": False})
    payload["sub"] = str(bia.id)
    adulterado = jwt.encode(payload, "nao-sei-a-chave-xxxxxxxxxxxxxxxxxxxxxxxxxxxx", algorithm=settings.jwt_algoritmo)
    assert _redefinir(client, adulterado).status_code == 400


def test_senha_fraca_nao_gasta_o_link(client, criar_usuario, emails):
    criar_usuario(ANA)
    _pedir(client)
    token = _token(emails)
    r = _redefinir(client, token, "aaaaaaaaaaaa1")  # previsível (caracteres repetidos)
    assert r.status_code == 422
    assert _redefinir(client, token, "curta").status_code == 422  # abaixo do mínimo
    assert _redefinir(client, token).status_code == 204  # o link continua valendo


def test_redefinir_desbloqueia_a_conta_e_mantem_o_2fa(client, criar_usuario, emails):
    usuario = criar_usuario(ANA)
    for _ in range(settings.max_tentativas_login):
        _entrar(client, "senha-errada-123")
    assert _entrar(client, SENHA).status_code == 401  # bloqueada
    with SessionLocal() as db:
        db.get(Usuario, usuario.id).totp_ativo = True
        db.commit()

    _pedir(client)
    assert _redefinir(client, _token(emails)).status_code == 204
    r = _entrar(client, NOVA)
    assert r.status_code == 200 and r.json()["requer_2fa"] is True  # a senha nova não pula o 2FA


def test_troca_obrigatoria_some_depois_de_redefinir(client, criar_usuario, emails, login):
    usuario = criar_usuario(ANA)
    with SessionLocal() as db:
        db.get(Usuario, usuario.id).trocar_senha = True
        db.commit()
    _pedir(client)
    assert _redefinir(client, _token(emails)).status_code == 204
    assert client.get("/auth/me", headers=login(ANA, NOVA)).json()["trocar_senha"] is False


def test_limite_por_email_nao_avisa_quem_pede(client, criar_usuario, emails):
    criar_usuario(ANA)
    respostas = [_pedir(client) for _ in range(4)]
    assert [r.status_code for r in respostas] == [202] * 4
    assert len({r.text for r in respostas}) == 1
    assert len(emails) == 3  # o 4º pedido na mesma hora não manda nada


def test_limite_por_ip(client, emails):
    codigos = [_pedir(client, f"p{i}@empresa.com.br").status_code for i in range(6)]
    assert codigos == [202] * 5 + [429]


def test_sem_resend_o_link_so_vai_para_o_log_em_dev(monkeypatch, caplog):
    monkeypatch.setattr(settings, "resend_api_key", None)
    link = "https://pmo.exemplo.com/redefinir-senha#token=abc"
    with caplog.at_level(logging.INFO, logger=notificacoes.__name__):
        monkeypatch.setattr(settings, "ambiente", "dev")
        assert notificacoes.enviar_redefinicao_senha(ANA, "Ana", link) is False
        assert link in caplog.text
        caplog.clear()
        monkeypatch.setattr(settings, "ambiente", "prod")
        assert notificacoes.enviar_redefinicao_senha(ANA, "Ana", link) is False
        assert "abc" not in caplog.text


def test_email_escapa_o_nome_e_leva_o_link(monkeypatch):
    enviados = []

    class Emails:
        @staticmethod
        def send(dados):
            enviados.append(dados)

    monkeypatch.setattr(settings, "resend_api_key", "re_teste")
    monkeypatch.setattr(notificacoes.resend, "Emails", Emails)
    link = "https://pmo.exemplo.com/redefinir-senha#token=a.b-c_d"
    assert notificacoes.enviar_redefinicao_senha(ANA, "<b>Ana</b>\nBcc: x", link) is True
    corpo = enviados[0]
    assert "<b>Ana</b>" not in corpo["html"] and "&lt;b&gt;Ana&lt;/b&gt;" in corpo["html"]
    assert f'href="{link}"' in corpo["html"] and link in corpo["text"]
    assert "\n" not in corpo["subject"]


def test_link_usa_o_endereco_do_front(client, criar_usuario, emails, monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://pmo.exemplo.com/")
    criar_usuario(ANA)
    _pedir(client)
    assert re.fullmatch(r"https://pmo\.exemplo\.com/redefinir-senha#token=[\w.-]+", emails[0][2])


def test_producao_exige_link_https():
    base = dict(ambiente="prod", cookie_secure=True, chave_2fa="x" * 44, secret_key="s" * 64)
    with pytest.raises(ValueError, match="https://"):
        Settings(**base, cors_origens=["http://pmo.exemplo.com"])
    assert Settings(**base, cors_origens=["http://interno"], frontend_url="https://pmo.exemplo.com").url_do_front \
        == "https://pmo.exemplo.com"


def test_token_traz_so_o_necessario():
    """O link vai por e-mail: nada de hash de senha nem e-mail dentro dele."""
    token = criar_token_redefinicao(7, 3, "$argon2id$v=19$m=65536,t=3,p=4$sal$hash")
    payload = jwt.decode(token, options={"verify_signature": False})
    assert set(payload) == {"sub", "type", "iss", "iat", "exp", "jti", "ver", "pwd"}
    assert "argon2" not in token and "$" not in payload["pwd"]
    assert payload["exp"] - payload["iat"] == settings.redefinicao_senha_minutos * 60


def test_vigia_nao_manda_o_link_para_a_ia():
    """Em dev o link vai para o log, e o vigia manda trechos do log ao Groq: o token tem de sumir."""
    from app.vigia.analista import anonimizar

    token = criar_token_redefinicao(7, 0, "hash")
    linha = f"RESEND_API_KEY não configurada (dev): link de redefinição de senha: http://localhost:5173/redefinir-senha#token={token}"
    limpa = anonimizar(linha)
    assert token not in limpa and "<token>" in limpa
