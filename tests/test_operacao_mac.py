"""
Correções de operação de 22/11/2026 (achadas rodando os serviços no Mac):
1. o vigia espera a API subir antes da 1ª rodada e a IA não pode apontar causa sem evidência;
2. logs/servico-*.log são cortados pela limpeza (e a API não grava cada requisição em dobro);
3. vigia e lembretes contam o tempo pelo relógio de verdade (o Mac dormindo não atrasa a próxima vez).
"""

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import limpeza, tempo
from app.config import settings
from app.vigia import analista, sondas
from app.vigia import __main__ as vigia_main

INICIO = datetime(2026, 11, 23, 8, 0, tzinfo=timezone.utc)


# ---------- 3. Relógio de verdade ----------


def test_roda_logo_ao_acordar_depois_de_dormir(monkeypatch):
    """Intervalo de 15 min; o Mac dorme 1 h entre duas conferências: roda na primeira conferência ao acordar."""
    # a 1ª leitura marca a partida; depois, uma leitura por conferência
    relogio = iter([INICIO, INICIO, INICIO + timedelta(minutes=5), INICIO + timedelta(hours=1, minutes=5),
                    INICIO + timedelta(hours=1, minutes=6), INICIO + timedelta(hours=1, minutes=21)])
    monkeypatch.setattr(tempo, "agora_utc", lambda: next(relogio))
    parar = threading.Event()
    rodou = []

    def tarefa(agora):
        rodou.append(agora)

    original_wait = parar.wait
    conferencias = [0]

    def esperar(_s):
        conferencias[0] += 1
        if conferencias[0] == 5:
            parar.set()
        return original_wait(0)

    monkeypatch.setattr(parar, "wait", esperar)
    tempo.repetir_pelo_relogio(parar, timedelta(minutes=15), tarefa, conferir_a_cada=0)
    # 08:00 (na partida), 09:05 (ao acordar, sem esperar mais 15 min), 09:21 (15 min depois)
    assert [r.strftime("%H:%M") for r in rodou] == ["08:00", "09:05", "09:21"]


def test_relogio_que_volta_nao_trava_a_proxima(monkeypatch):
    relogio = iter([INICIO, INICIO, INICIO - timedelta(days=1), INICIO - timedelta(days=1) + timedelta(seconds=30)])
    monkeypatch.setattr(tempo, "agora_utc", lambda: next(relogio))
    parar, rodou = threading.Event(), []
    chamadas = [0]

    def esperar(_s):
        chamadas[0] += 1
        if chamadas[0] == 3:
            parar.set()

    monkeypatch.setattr(parar, "wait", esperar)
    tempo.repetir_pelo_relogio(parar, timedelta(minutes=15), rodou.append, conferir_a_cada=0)
    assert len(rodou) == 2  # na partida e logo depois do ajuste do relógio (não 1 dia depois)


# ---------- 1. Vigia espera a API e não inventa causa ----------


def test_vigia_espera_a_api_subir(monkeypatch):
    respostas = iter([{"ok": False}, {"ok": False}, {"ok": True}])
    monkeypatch.setattr(sondas, "saude_api", lambda url: next(respostas))
    assert vigia_main.esperar_api(threading.Event(), limite_s=5, passo_s=0) is True


def test_vigia_desiste_de_esperar_no_limite(monkeypatch, caplog):
    monkeypatch.setattr(sondas, "saude_api", lambda url: {"ok": False})
    with caplog.at_level("WARNING", logger="pmo.vigia"):
        assert vigia_main.esperar_api(threading.Event(), limite_s=0, passo_s=0) is False
    assert "não respondeu" in caplog.text


def test_prompt_proibe_causa_sem_evidencia():
    assert "evidência DIRETA" in analista.PROMPT and "não identificada nos dados" in analista.PROMPT
    assert "pouco espaço em disco não explica a API fora do ar" in analista.PROMPT


# ---------- 2. Logs dos serviços ----------


def test_corta_log_de_servico_grande_e_deixa_o_pequeno(tmp_path):
    grande, pequeno = tmp_path / "servico-api.log", tmp_path / "servico-vigia.log"
    linha = b"INFO: uma linha de log qualquer com algum texto\n"
    grande.write_bytes(linha * (3 * 1024 * 1024 // len(linha)))  # ~3 MB
    pequeno.write_bytes(b"ok\n")
    resultado = limpeza.Resultado()
    limpeza.aparar_logs_de_servico(resultado, simular=False, pasta=tmp_path, maximo_mb=2, manter_mb=1)
    conteudo = grande.read_bytes()
    assert len(conteudo) <= 1024 * 1024 + 100
    assert conteudo.startswith(b"[... inicio cortado") and conteudo.split(b"\n")[1] == linha.rstrip(b"\n")  # linha inteira
    assert pequeno.read_bytes() == b"ok\n"
    assert resultado.itens == ["servico-api.log cortado (3 MB -> 1 MB)"]


def test_simular_nao_corta(tmp_path):
    arquivo = tmp_path / "servico-api.log"
    arquivo.write_bytes(b"x\n" * (2 * 1024 * 1024))
    resultado = limpeza.Resultado()
    limpeza.aparar_logs_de_servico(resultado, simular=True, pasta=tmp_path, maximo_mb=1)
    assert arquivo.stat().st_size == 4 * 1024 * 1024 and resultado.itens


def test_corta_mesmo_com_disco_folgado(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "servico-api.log").write_bytes(b"x\n" * (6 * 1024 * 1024))
    monkeypatch.setattr(settings, "limpeza_servico_log_max_mb", 1)
    monkeypatch.setattr(limpeza, "livre_gb", lambda *a: 500.0)  # disco folgado: o resto da limpeza não roda
    assert limpeza.executar() is None
    assert Path("logs/servico-api.log").stat().st_size <= 1024 * 1024 + 100


def test_servico_da_api_sem_log_de_acesso_duplicado():
    script = Path(__file__).resolve().parents[1] / "scripts" / "instalar-servicos.sh"
    assert "--port 8000 --no-access-log" in script.read_text()
