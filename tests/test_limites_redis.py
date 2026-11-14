"""
Limites e bloqueios no Redis: compartilhados entre os processos da API (e com volta para a memória
se o Redis cair). Mais o health check, que agora também olha a fila.
"""

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app import fila, rate_limit
from app.config import settings
from app.models import PapelUsuario
from tests.conftest import SENHA


@pytest.fixture
def redis_compartilhado(monkeypatch):
    """Um Redis (falso) que todos os "processos" enxergam, como em produção."""
    servidor = fakeredis.FakeServer()
    conexao = fakeredis.FakeStrictRedis(server=servidor)
    monkeypatch.setattr(settings, "limites_backend", "redis")
    monkeypatch.setattr(fila, "conexao", lambda: conexao)
    rate_limit.resetar_todos()
    return conexao


def _processo(nome: str = "teste", maximo: int = 5) -> rate_limit.LimitadorTaxa:
    """Cada worker do uvicorn cria os próprios objetos limitadores: aqui, um "processo" novo."""
    limitador = rate_limit.LimitadorTaxa(nome, max_requisicoes=maximo, janela_segundos=60)
    rate_limit._REGISTRADOS.remove(limitador)
    return limitador


def _passou(limitador, chave="ip-1") -> bool:
    try:
        limitador.verificar(chave)
        return True
    except Exception:
        return False


def test_dois_processos_dividem_o_mesmo_limite(redis_compartilhado):
    worker_a, worker_b = _processo(), _processo()
    resultados = [_passou(worker_a if i % 2 == 0 else worker_b) for i in range(8)]
    assert resultados == [True] * 5 + [False] * 3  # 5 no total, não 5 em cada worker


def test_retry_after_e_chaves_independentes(redis_compartilhado):
    limitador = _processo(maximo=2)
    assert _passou(limitador, "ip-1") and _passou(limitador, "ip-1")
    with pytest.raises(Exception) as barrado:
        limitador.verificar("ip-1")
    assert barrado.value.status_code == 429 and 1 <= int(barrado.value.headers["Retry-After"]) <= 61
    assert _passou(limitador, "ip-2")  # outra chave, outra cota


def test_tentativa_barrada_nao_ocupa_a_cota(redis_compartilhado):
    limitador = _processo(maximo=3)
    for _ in range(10):
        _passou(limitador)
    chave = f"{rate_limit.PREFIXO}taxa:teste:ip-1"
    assert redis_compartilhado.zcard(chave) == 3  # as 7 barradas não ficaram gravadas
    assert 0 < redis_compartilhado.ttl(chave) <= 61  # e a chave expira sozinha


def test_bloqueio_de_login_vale_entre_processos(client, criar_usuario, redis_compartilhado):
    criar_usuario("ana@empresa.com.br")
    for _ in range(settings.max_tentativas_login):
        client.post("/auth/login", data={"username": "ana@empresa.com.br", "password": "senha-errada-123"})
    # Outro "processo" (objeto novo, mesmo Redis) já vê o bloqueio.
    outro = rate_limit.FalhasLogin()
    for contador in (outro.por_email_ip, outro.por_ip):
        rate_limit._REGISTRADOS.remove(contador)
    assert outro.motivo_bloqueio("ana@empresa.com.br", "testclient") == "e-mail + IP"
    r = client.post("/auth/login", data={"username": "ana@empresa.com.br", "password": SENHA})
    assert r.status_code == 401  # nem a senha certa entra durante o bloqueio


def test_limpar_falhas_de_um_email_com_curingas_no_nome(redis_compartilhado):
    falhas = rate_limit.falhas_login
    estranho, vizinho = "a*b?[x]@empresa.com.br", "aXbY[x]@empresa.com.br"
    for _ in range(3):
        falhas.registrar(estranho, "ip-1")
        falhas.registrar(vizinho, "ip-1")
    falhas.limpar(estranho)
    agora = __import__("time").time()
    janela = settings.bloqueio_minutos * 60
    redis_janelas = rate_limit.JanelasNoRedis(redis_compartilhado)
    assert redis_janelas.contar(falhas.por_email_ip._chave((estranho, "ip-1")), agora, janela) == 0
    assert redis_janelas.contar(falhas.por_email_ip._chave((vizinho, "ip-1")), agora, janela) == 3  # o * não apagou o vizinho


def test_redis_fora_do_ar_volta_para_a_memoria_sem_desligar_a_protecao(monkeypatch, caplog):
    class RedisQuebrado:
        def pipeline(self, *_a, **_k):
            raise RedisConnectionError("Connection refused")

        def scan_iter(self, *_a, **_k):
            raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(settings, "limites_backend", "redis")
    monkeypatch.setattr(fila, "conexao", lambda: RedisQuebrado())
    rate_limit.resetar_todos()
    limitador = _processo(maximo=2)
    assert [_passou(limitador) for _ in range(4)] == [True, True, False, False]  # continua limitando
    assert "Redis indisponível" in caplog.text


# ---------- Health check ----------


def test_saude_com_fila_sincrona(client):
    assert client.get("/saude").json() == {"status": "ok", "banco": "ok", "fila": "sincrona"}


@pytest.mark.parametrize("situacao, status", [("ok", "ok"), ("sem_worker", "degradado"), ("indisponivel", "degradado")])
def test_saude_com_fila_de_verdade(client, monkeypatch, situacao, status):
    from app import main

    monkeypatch.setattr(main, "_situacao_da_fila", lambda: situacao)
    r = client.get("/saude")
    assert r.status_code == 200  # a API continua de pé: só a importação é afetada
    assert r.json() == {"status": status, "banco": "ok", "fila": situacao}


def test_saude_detecta_redis_e_worker(monkeypatch):
    from rq import Worker

    from app import main

    monkeypatch.setattr(settings, "fila_sincrona", False)
    conexao = fakeredis.FakeStrictRedis()
    monkeypatch.setattr(fila, "conexao", lambda: conexao)
    assert main._situacao_da_fila() == "sem_worker"
    monkeypatch.setattr(Worker, "count", classmethod(lambda cls, connection=None, queue=None: 1))
    assert main._situacao_da_fila() == "ok"

    def _fora():
        raise RedisConnectionError("Connection refused")

    monkeypatch.setattr(fila, "conexao", _fora)
    assert main._situacao_da_fila() == "indisponivel"


def test_banco_fora_do_ar_e_503(client, monkeypatch):
    from app import main

    class MotorQuebrado:
        def connect(self):
            raise RuntimeError("banco caiu")

    monkeypatch.setattr(main, "engine", MotorQuebrado())
    r = client.get("/saude")
    assert r.status_code == 503 and r.json() == {"status": "erro", "banco": "indisponivel"}


def test_login_completo_usa_o_redis(client, criar_usuario, redis_compartilhado):
    criar_usuario("bia@empresa.com.br", PapelUsuario.GERENTE)
    assert client.post("/auth/login", data={"username": "bia@empresa.com.br", "password": SENHA}).status_code == 200
    assert any(k.startswith(b"pmo:limites:taxa:login:") for k in redis_compartilhado.scan_iter())
