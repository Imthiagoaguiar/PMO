"""
Importação em segundo plano (Redis + RQ): a API responde 202 na hora e o worker processa.

Nos testes, FILA_SINCRONA=true (fakeredis): o job roda dentro da própria requisição. Os testes de
"fila de verdade" trocam para uma fila assíncrona no fakeredis e rodam um worker RQ em modo burst.
"""

import io
import time

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError
from rq import Queue, SimpleWorker
from rq.job import Job
from sqlalchemy import func, select

from app import fila, importacao, services
from app.database import SessionLocal
from app.integracoes import notificacoes
from app.models import Auditoria, Importacao, PapelUsuario, Projeto, StatusImportacao, Tarefa
from tests.conftest import SENHA

GERENTE, OUTRO = "gerente@empresa.com.br", "outro@empresa.com.br"
CSV_OK = "Fase;Descrição;Pilar\nIniciação;TAP;TI\nExecução;Construir;CAPEX\n".encode()
CSV_RUIM = "Fase;Descrição\nIniciação;TAP\nFase Inventada;X\nExecução;\n".encode()


@pytest.fixture
def h(criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    criar_usuario(OUTRO, PapelUsuario.GERENTE)
    return {"gerente": login(GERENTE), "outro": login(OUTRO)}


def _enviar(client, headers, conteudo=CSV_OK, nome="plano.csv", **extra):
    return client.post("/projetos/importar", data={"nome_projeto": "Importado", **extra},
                       files={"arquivo": (nome, conteudo)}, headers=headers)


def _contar(modelo, *filtros):
    with SessionLocal() as db:
        return db.scalar(select(func.count()).select_from(modelo).where(*filtros))


# ---------- Contrato da API ----------


def test_responde_202_com_o_endereco_para_acompanhar(client, h):
    r = _enviar(client, h["gerente"])
    assert r.status_code == 202, r.text
    imp = r.json()
    assert r.headers["location"] == f"/importacoes/{imp['id']}"
    assert (imp["status"], imp["progresso"], imp["total_linhas"]) == ("concluida", 100, 2)

    acompanhamento = client.get(r.headers["location"], headers=h["gerente"]).json()
    assert acompanhamento["projeto_id"] == imp["projeto_id"]
    projeto = client.get(f"/projetos/{imp['projeto_id']}", headers=h["gerente"]).json()
    assert sum(len(f["tarefas"]) for f in projeto["fases"]) == 2
    with SessionLocal() as db:
        assert db.get(Importacao, imp["id"]).conteudo is None  # o arquivo não fica guardado


def test_planilha_invalida_termina_falhou_com_os_erros_por_linha(client, h):
    r = _enviar(client, h["gerente"], CSV_RUIM)
    assert r.status_code == 202
    imp = r.json()
    assert imp["status"] == "falhou" and imp["projeto_id"] is None
    assert imp["mensagem"] == "A planilha contém erros de validação."
    assert [e.split(":")[0] for e in imp["erros"]] == ["Linha 3", "Linha 4"]
    assert _contar(Projeto) == 0
    with SessionLocal() as db:
        assert db.get(Importacao, imp["id"]).conteudo is None


@pytest.mark.parametrize("nome, conteudo, status, trecho", [
    ("plano.txt", CSV_OK, 422, "Formato não suportado"),
    ("plano.csv", b"", 422, "vazio"),
    ("plano.csv", b"Fase;Descricao\n" + b"x" * (21 * 1024 * 1024), 413, "20 MB"),
])
def test_erros_baratos_respondem_na_hora_sem_criar_importacao(client, h, nome, conteudo, status, trecho):
    r = _enviar(client, h["gerente"], conteudo, nome)
    assert r.status_code == status and trecho in r.text
    assert _contar(Importacao) == 0


def test_formulario_invalido_responde_na_hora(client, h):
    assert _enviar(client, h["gerente"], nome_projeto="  ").status_code == 422
    assert _contar(Importacao) == 0


# ---------- Fila de verdade (assíncrona) ----------


@pytest.fixture
def fila_assincrona(monkeypatch):
    """Fila no fakeredis com is_async=True: o job só roda quando um worker o pega."""
    redis_falso = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(fila, "conexao", lambda: redis_falso)
    monkeypatch.setattr(fila, "fila", lambda: Queue(fila.NOME_FILA, connection=redis_falso, is_async=True))
    return redis_falso


def test_api_responde_antes_e_o_worker_processa_depois(client, h, fila_assincrona):
    imp = _enviar(client, h["gerente"]).json()
    assert (imp["status"], imp["progresso"], imp["etapa"]) == ("na_fila", 0, "Na fila")
    assert _contar(Projeto) == 0  # nada foi processado ainda: a requisição já terminou

    SimpleWorker([fila.fila()], connection=fila_assincrona).work(burst=True)

    depois = client.get(f"/importacoes/{imp['id']}", headers=h["gerente"]).json()
    assert (depois["status"], depois["progresso"], depois["total_linhas"]) == ("concluida", 100, 2)
    assert depois["iniciada_em"] and depois["concluida_em"]
    assert _contar(Projeto) == 1


def test_progresso_ao_vivo_vem_do_redis(client, h, fila_assincrona):
    imp = _enviar(client, h["gerente"]).json()
    with SessionLocal() as db:  # o worker pegou a importação e está gravando
        db.get(Importacao, imp["id"]).status = StatusImportacao.PROCESSANDO
        db.commit()
    job = Job.fetch(fila.id_do_job(imp["id"]), connection=fila_assincrona)
    job.meta.update({"progresso": 63, "etapa": "Gravando tarefas (40.000 de 100.000)"})
    job.save_meta()
    agora = client.get(f"/importacoes/{imp['id']}", headers=h["gerente"]).json()
    assert (agora["status"], agora["progresso"], agora["etapa"]) == ("processando", 63, "Gravando tarefas (40.000 de 100.000)")


def test_fila_fora_do_ar_responde_503_e_registra_a_falha(client, h, monkeypatch):
    def _fora(_id):
        raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(fila, "enfileirar_importacao", _fora)
    r = _enviar(client, h["gerente"])
    assert r.status_code == 503 and "fora do ar" in r.json()["detail"]
    with SessionLocal() as db:
        imp = db.scalar(select(Importacao))
        assert (imp.status, imp.conteudo) == (StatusImportacao.FALHOU, None)


def test_job_entregue_duas_vezes_nao_duplica_o_projeto(client, h, fila_assincrona):
    imp = _enviar(client, h["gerente"]).json()
    assert importacao.processar(imp["id"]) is not None
    assert importacao.processar(imp["id"]) is None  # já não está "na fila": ignora
    assert _contar(Projeto) == 1


def test_erro_inesperado_no_meio_nao_deixa_projeto_pela_metade(client, h, monkeypatch):
    def _quebra(*_a, **_k):
        raise RuntimeError("disco cheio no meio do insert")

    monkeypatch.setattr(services, "gravar_tarefas_em_lote", _quebra)
    imp = _enviar(client, h["gerente"]).json()
    assert imp["status"] == "falhou" and imp["mensagem"] == importacao.FALHA_INTERNA
    assert "disco cheio" not in str(imp)  # o detalhe vai para o log, não para o usuário
    assert (_contar(Projeto), _contar(Tarefa)) == (0, 0)


# ---------- Quem vê, idempotência, auditoria e e-mail ----------


def test_cada_um_so_ve_as_proprias_importacoes(client, h):
    imp = _enviar(client, h["gerente"]).json()
    r = client.get(f"/importacoes/{imp['id']}", headers=h["outro"])
    assert (r.status_code, r.json()) == (404, {"detail": "Importação não encontrada."})
    assert client.get("/importacoes/999999", headers=h["outro"]).json() == r.json()
    assert client.get("/importacoes", headers=h["outro"]).json()["total"] == 0
    assert client.get("/importacoes", headers=h["gerente"]).json()["itens"][0]["id"] == imp["id"]


def test_mesma_chave_devolve_a_mesma_importacao(client, h):
    cabecalhos = {**h["gerente"], "Idempotency-Key": "envio-planilha-1"}
    primeira = _enviar(client, cabecalhos)
    segunda = _enviar(client, cabecalhos)
    assert segunda.json()["id"] == primeira.json()["id"]
    assert segunda.headers["idempotent-replayed"] == "true"
    assert _contar(Projeto) == 1
    outra = _enviar(client, cabecalhos, CSV_RUIM)  # mesma chave, outro arquivo
    assert outra.status_code == 422


def test_auditoria_registra_quem_enviou_e_o_ip(client, h):
    imp = _enviar(client, h["gerente"]).json()
    with SessionLocal() as db:
        evento = db.scalar(select(Auditoria).where(Auditoria.acao == "projeto.importado"))
        assert evento.projeto_id == imp["projeto_id"] and evento.usuario.email == GERENTE
        assert evento.ip == "testclient"  # o IP do upload, mesmo a auditoria sendo gravada pelo worker


def test_email_sai_uma_vez_pelo_worker(client, h, monkeypatch):
    enviados = []
    monkeypatch.setattr(notificacoes, "enviar_projeto_iniciado", lambda *a: enviados.append(a))
    _enviar(client, h["gerente"])
    _enviar(client, h["gerente"], CSV_RUIM)  # falhou: não avisa
    assert enviados == [(GERENTE, "gerente", "Importado")]


# ---------- Volume ----------


def test_cem_mil_linhas_em_segundos(client, h):
    linhas = "".join(f"{['Iniciação', 'Execução', 'Encerramento'][i % 3]};Tarefa {i};{['TI', 'CAPEX', ''][i % 3]}\n"
                     for i in range(100_000))
    conteudo = ("Fase;Descrição;Pilar\n" + linhas).encode()
    inicio = time.perf_counter()
    imp = _enviar(client, h["gerente"], conteudo).json()
    duracao = time.perf_counter() - inicio
    assert imp["status"] == "concluida" and imp["total_linhas"] == 100_000, imp.get("mensagem")
    assert _contar(Tarefa) == 100_000
    assert duracao < 60, f"{duracao:.1f}s"
    print(f"\n100 mil linhas: {duracao:.1f}s")


def test_acima_do_limite_de_linhas_falha_com_mensagem(client, h, monkeypatch):
    monkeypatch.setattr(services, "MAX_LINHAS", 10)
    imp = _enviar(client, h["gerente"], ("Fase;Descrição\n" + "Iniciação;T\n" * 11).encode()).json()
    assert imp["status"] == "falhou" and "10 linhas" in imp["mensagem"]


def test_senha_do_fixture_continua_valida():
    assert SENHA  # o login do fixture h depende dela (sanidade do arquivo)
