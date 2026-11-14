"""
Condições de corrida: 5 requisições SIMULTÂNEAS (httpx.AsyncClient + asyncio.gather).

Só rodam no Postgres (TEST_DATABASE_URL): o SQLite em memória usa uma única conexão
compartilhada e não tem concorrência real de transações.

Para a corrida não depender de sorte, `sincronizar` segura as 5 requisições num ponto crítico
(threading.Barrier) e solta todas juntas: todas leem o MESMO estado antes de qualquer uma gravar.

O que é garantido:
- duplo clique / retentativa em "Criar projeto" com a mesma Idempotency-Key: 1 projeto só;
- edição simultânea da mesma tarefa/projeto: 1 vence, as outras recebem 409 (bloqueio otimista,
  coluna `versao`), sem "atualização perdida";
- adicionar o mesmo membro 5 vezes: 1 entra, as outras 409 (e não erro 500);
- o mesmo código do 2FA ou de recuperação usado 5 vezes ao mesmo tempo: 1 login só;
- o mesmo refresh token renovado 5 vezes ao mesmo tempo: 1 sessão nova só.
"""

import asyncio
import threading
from functools import wraps
from typing import Any, Callable, Dict, List

import httpx
import pyotp
import pytest
from sqlalchemy import select

from app import auditoria, dois_fatores, idempotencia, services
from app.config import settings
from app.cripto import decifrar
from app.database import engine
from app.main import app
from app.models import Auditoria, Fase, Importacao, Pilar, PapelUsuario, Projeto, ProjetoMembro, RefreshToken, Tarefa, Usuario
from app.routers import auth as rotas_auth
from app.routers import pilares as pilares_rotas
from tests.conftest import SENHA
from tests.criticos.conftest import contar

pytestmark = pytest.mark.skipif(
    engine.dialect.name != "postgresql",
    reason="concorrência real exige Postgres: rode com TEST_DATABASE_URL",
)
N = 5


def sincronizar(monkeypatch, alvo: Any, nome: str, n: int = N, timeout: float = 3.0) -> None:
    """Faz as `n` requisições pararem ANTES de `alvo.nome` e seguirem juntas."""
    barreira = threading.Barrier(n, timeout=timeout)
    original = getattr(alvo, nome)

    @wraps(original)
    def _junto(*args, **kwargs):
        try:
            barreira.wait()
        except threading.BrokenBarrierError:
            pass  # alguém ficou preso num lock do banco (é o comportamento correto): segue
        return original(*args, **kwargs)

    monkeypatch.setattr(alvo, nome, _junto)


def simultaneas(chamadas: List[Callable[[httpx.AsyncClient], Any]]) -> List[httpx.Response]:
    async def _rodar():
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://testserver") as cliente:
            return await asyncio.gather(*(c(cliente) for c in chamadas))

    return asyncio.run(_rodar())


def _status(respostas) -> List[int]:
    return sorted(r.status_code for r in respostas)


@pytest.fixture
def projeto(client, gerente) -> Dict:
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=gerente).json()
    tarefa = client.post(f"/projetos/{p['id']}/fases/{p['fases'][0]['id']}/tarefas",
                         json={"descricao": "TAP"}, headers=gerente).json()
    return {"id": p["id"], "versao": p["versao"], "tarefa": tarefa}


# ---------- Criar projeto: duplo clique / retentativa ----------


def test_mesma_chave_de_idempotencia_cria_um_projeto_so(gerente, db, monkeypatch):
    sincronizar(monkeypatch, services, "criar_projeto_com_fases")
    cabecalhos = {**gerente, "Idempotency-Key": "8b1f6c0e-criar-erp"}
    respostas = simultaneas([
        lambda c: c.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=cabecalhos)
    ] * N)

    assert _status(respostas) == [201] * N, [r.text for r in respostas]
    assert len({r.json()["id"] for r in respostas}) == 1  # todas devolvem O MESMO projeto
    assert (contar(db, Projeto), contar(db, Fase)) == (1, 5)


def test_chaves_diferentes_criam_projetos_diferentes(gerente, db, monkeypatch):
    sincronizar(monkeypatch, services, "criar_projeto_com_fases")
    respostas = simultaneas([
        (lambda i: lambda c: c.post("/projetos/manual", json={"nome_projeto": f"ERP {i}"},
                                    headers={**gerente, "Idempotency-Key": f"chave-distinta-{i}"}))(i)
        for i in range(N)
    ])
    assert _status(respostas) == [201] * N
    assert (contar(db, Projeto), contar(db, Fase)) == (N, 5 * N)


def test_importacao_repetida_com_a_mesma_chave(gerente, db, monkeypatch):
    """5 envios da mesma planilha com a mesma chave, juntos: UMA importação na fila e UM projeto."""
    sincronizar(monkeypatch, idempotencia, "resumo")  # todas passam da checagem "já existe?" juntas
    cabecalhos = {**gerente, "Idempotency-Key": "importacao-1"}
    planilha = "Fase;Descrição\nIniciação;TAP\nExecução;Construir\n".encode()
    respostas = simultaneas([
        lambda c: c.post("/projetos/importar", data={"nome_projeto": "Importado"},
                         files={"arquivo": ("p.csv", planilha)}, headers=cabecalhos)
    ] * N)
    assert _status(respostas) == [202] * N, [r.text for r in respostas]
    assert len({r.json()["id"] for r in respostas}) == 1
    assert (contar(db, Importacao), contar(db, Projeto), contar(db, Tarefa)) == (1, 1, 2)


# ---------- Edição simultânea: bloqueio otimista ----------


def test_cinco_edicoes_da_mesma_versao_da_tarefa(gerente, projeto, db, monkeypatch):
    sincronizar(monkeypatch, auditoria, "aplicar_alteracoes")
    url = f"/projetos/{projeto['id']}/tarefas/{projeto['tarefa']['id']}"
    versao = projeto["tarefa"]["versao"]
    respostas = simultaneas([
        (lambda p: lambda c: c.patch(url, json={"percentual_conclusao": p, "versao": versao}, headers=gerente))(p)
        for p in (10, 20, 30, 40, 50)
    ])

    assert _status(respostas) == [200] + [409] * (N - 1), [r.text for r in respostas]
    vencedora = next(r.json() for r in respostas if r.status_code == 200)
    db.expire_all()
    tarefa = db.get(Tarefa, projeto["tarefa"]["id"])
    assert (tarefa.percentual_conclusao, tarefa.versao) == (vencedora["percentual_conclusao"], versao + 1)
    assert all("alterad" in r.json()["detail"] for r in respostas if r.status_code == 409)


def test_edicao_simultanea_sem_informar_versao_tambem_nao_perde_dados(gerente, projeto, db, monkeypatch):
    """Cliente antigo (sem `versao`): ainda assim só uma gravação vence; nenhuma é sobrescrita em silêncio."""
    sincronizar(monkeypatch, auditoria, "aplicar_alteracoes")
    url = f"/projetos/{projeto['id']}/tarefas/{projeto['tarefa']['id']}"
    respostas = simultaneas([
        (lambda d: lambda c: c.patch(url, json={"descricao": d}, headers=gerente))(f"Versão {i}") for i in range(N)
    ])
    assert _status(respostas) == [200] + [409] * (N - 1), [r.text for r in respostas]


def test_versao_desatualizada_e_recusada_mesmo_sem_corrida(client, gerente, projeto):
    url = f"/projetos/{projeto['id']}/tarefas/{projeto['tarefa']['id']}"
    v1 = projeto["tarefa"]["versao"]
    assert client.patch(url, json={"percentual_conclusao": 10, "versao": v1}, headers=gerente).status_code == 200
    r = client.patch(url, json={"percentual_conclusao": 99, "versao": v1}, headers=gerente)  # editou a tela velha
    assert r.status_code == 409


def test_cinco_mudancas_de_status_do_projeto(gerente, projeto, db, monkeypatch):
    sincronizar(monkeypatch, auditoria, "aplicar_alteracoes")
    status = ["Em andamento", "Concluído", "Cancelado", "Em andamento", "Concluído"]
    respostas = simultaneas([
        (lambda s: lambda c: c.patch(f"/projetos/{projeto['id']}", json={"status": s, "versao": projeto["versao"]},
                                     headers=gerente))(s) for s in status
    ])
    assert _status(respostas) == [200] + [409] * (N - 1), [r.text for r in respostas]
    vencedora = next(r.json() for r in respostas if r.status_code == 200)
    db.expire_all()
    assert db.get(Projeto, projeto["id"]).status.value == vencedora["status"]


# ---------- Membros ----------


def test_adicionar_o_mesmo_membro_cinco_vezes(gerente, projeto, criar_usuario, db, monkeypatch):
    novo = criar_usuario("novo@empresa.com.br")
    sincronizar(monkeypatch, auditoria, "registrar")
    respostas = simultaneas([
        lambda c: c.post(f"/projetos/{projeto['id']}/membros", json={"usuario_id": novo.id}, headers=gerente)
    ] * N)
    assert _status(respostas) == [201] + [409] * (N - 1), [r.text for r in respostas]
    assert contar(db, ProjetoMembro, ProjetoMembro.usuario_id == novo.id) == 1


# ---------- 2FA: o mesmo código não pode valer duas vezes ----------


@pytest.fixture
def com_2fa(client, criar_usuario, login, db) -> Dict:
    """Usuário com 2FA ativo; devolve o segredo e os códigos de recuperação."""
    criar_usuario("dois@empresa.com.br")
    h = login("dois@empresa.com.br")
    segredo = client.post("/auth/2fa/iniciar", json={"senha": SENHA}, headers=h).json()["segredo"]
    passo = pyotp.TOTP(segredo).timecode(__import__("datetime").datetime.now()) - 1  # código anterior
    codigos = client.post("/auth/2fa/ativar", json={"codigo": pyotp.TOTP(segredo).generate_otp(passo)},
                          headers=h).json()["codigos_recuperacao"]
    return {"segredo": segredo, "codigos": codigos}


def _desafio(client) -> str:
    r = client.post("/auth/login", data={"username": "dois@empresa.com.br", "password": SENHA})
    return r.json()["desafio"]


def test_mesmo_codigo_do_app_cinco_vezes_ao_mesmo_tempo(client, com_2fa, db, monkeypatch):
    desafios = [_desafio(client) for _ in range(N)]
    codigo = pyotp.TOTP(com_2fa["segredo"]).now()
    sincronizar(monkeypatch, dois_fatores, "verificar_codigo")
    respostas = simultaneas([
        (lambda d: lambda c: c.post("/auth/login/2fa", json={"desafio": d, "codigo": codigo}))(d) for d in desafios
    ])
    assert _status(respostas).count(200) == 1, [r.status_code for r in respostas]
    db.expire_all()
    usuario = db.scalar(select(Usuario).where(Usuario.email == "dois@empresa.com.br"))
    ativas = contar(db, RefreshToken, RefreshToken.usuario_id == usuario.id, RefreshToken.revogado_em.is_(None))
    assert ativas == 1  # 1 sessão só


def test_mesmo_codigo_de_recuperacao_cinco_vezes_ao_mesmo_tempo(client, com_2fa, db, monkeypatch):
    desafios = [_desafio(client) for _ in range(N)]
    codigo = com_2fa["codigos"][0]
    sincronizar(monkeypatch, dois_fatores, "verificar_codigo")
    respostas = simultaneas([
        (lambda d: lambda c: c.post("/auth/login/2fa", json={"desafio": d, "codigo": codigo}))(d) for d in desafios
    ])
    assert _status(respostas).count(200) == 1, [r.status_code for r in respostas]
    db.expire_all()
    uso = db.scalar(select(Auditoria).where(Auditoria.acao == "auth.codigo_recuperacao_usado"))
    assert uso.detalhes == {"restantes": 9}  # 10 gerados, 1 usado (uma vez só)


# ---------- Refresh token ----------


def test_mesmo_refresh_token_renovado_cinco_vezes_ao_mesmo_tempo(client, criar_usuario, db, monkeypatch):
    """Rotação: um refresh token vale UMA vez. Em paralelo, 1 renova; as outras recebem 409 (outra aba)."""
    criar_usuario("ana@empresa.com.br")
    client.post("/auth/login", data={"username": "ana@empresa.com.br", "password": SENHA})
    cookie = client.cookies.get("pmo_refresh")
    sincronizar(monkeypatch, rotas_auth, "_emitir_sessao", timeout=2.0)
    respostas = simultaneas([
        lambda c: c.post("/auth/refresh", headers={"Cookie": f"pmo_refresh={cookie}"})
    ] * N)

    assert _status(respostas) == [200] + [409] * (N - 1), [r.status_code for r in respostas]
    db.expire_all()
    ativos = db.scalars(select(RefreshToken).where(RefreshToken.revogado_em.is_(None))).all()
    assert len(ativos) == 1  # uma única sessão nova


# ---------- Pilares ----------


def test_cinco_avaliacoes_do_mesmo_pilar(client, gerente, projeto, db, monkeypatch):
    pilar = client.get(f"/projetos/{projeto['id']}/pilares", headers=gerente).json()[0]
    sincronizar(monkeypatch, auditoria, "aplicar_alteracoes")
    farois = ["verde", "amarelo", "vermelho", "verde", "amarelo"]
    respostas = simultaneas([
        (lambda f: lambda c: c.patch(f"/projetos/{projeto['id']}/pilares/{pilar['id']}",
                                     json={"farol": f, "status_texto": f"avaliado {f}", "versao": pilar["versao"]},
                                     headers=gerente))(f) for f in farois
    ])
    assert _status(respostas) == [200] + [409] * (N - 1), [r.text for r in respostas]
    vencedora = next(r.json() for r in respostas if r.status_code == 200)
    db.expire_all()
    gravado = db.get(Pilar, pilar["id"])
    assert (gravado.farol.value, gravado.status_texto) == (vencedora["farol"], vencedora["status_texto"])


def test_criar_o_mesmo_pilar_cinco_vezes_com_grafias_diferentes(gerente, projeto, db, monkeypatch):
    """ "Jurídico", "juridico", "JURÍDICO"... ao mesmo tempo: a unicidade no banco é sobre o nome normalizado."""
    sincronizar(monkeypatch, pilares_rotas, "_exigir_nome_livre")
    grafias = ["Jurídico", "juridico", "JURÍDICO", "  Jurídico ", "jurídico"]
    respostas = simultaneas([
        (lambda n: lambda c: c.post(f"/projetos/{projeto['id']}/pilares", json={"nome": n}, headers=gerente))(n)
        for n in grafias
    ])
    assert _status(respostas) == [201] + [409] * (N - 1), [r.text for r in respostas]
    assert contar(db, Pilar, Pilar.projeto_id == projeto["id"], Pilar.nome_chave == "juridico") == 1
