"""Limpeza do disco: só o que é recriável, só quando falta espaço, e nunca nada pessoal."""

import json
import logging
import os
from datetime import timedelta

import pytest

from app import limpeza, registro
from app.config import settings
from app.database import SessionLocal
from app.models import Importacao, PapelUsuario, RefreshToken, StatusImportacao, VigiaRodada
from app.security import agora_utc
from app.vigia import regras, sinais

MB = 1024 * 1024


@pytest.fixture
def pouco_espaco(monkeypatch):
    monkeypatch.setattr(limpeza, "livre_gb", lambda caminho=None: 3.0)
    avisos = []
    monkeypatch.setattr(limpeza, "avisar_no_mac", lambda titulo, texto: avisos.append(texto))
    return avisos


@pytest.fixture
def logs(tmp_path, monkeypatch):
    for nome in ("pmo.log", "pmo.log.1", "pmo.log.2", "pmo.log.3", "pmo.log.bak", "relatorio.txt", "trabalhador.log.4"):
        (tmp_path / nome).write_text("x" * 100)
    monkeypatch.setattr(settings, "log_arquivo", str(tmp_path / "pmo.log"))
    monkeypatch.setattr(settings, "log_arquivo_trabalhador", str(tmp_path / "trabalhador.log"))
    return tmp_path


@pytest.fixture
def registros(criar_usuario):
    dono = criar_usuario("dono@empresa.com.br", PapelUsuario.GERENTE)
    velho, novo = agora_utc() - timedelta(days=120), agora_utc() - timedelta(days=2)
    with SessionLocal() as db:
        for quando in (velho, novo):
            db.add(VigiaRodada(iniciada_em=quando, concluida_em=quando, janela_inicio=quando, resumo="x",
                               achados=[], sinais={}, ia_situacao="x"))
        db.add_all([
            RefreshToken(usuario_id=dono.id, token_hash="a" * 64, expira_em=agora_utc() - timedelta(days=3)),
            RefreshToken(usuario_id=dono.id, token_hash="b" * 64, expira_em=agora_utc() + timedelta(days=3)),
        ])
        base = dict(usuario_id=dono.id, nome_projeto="P", arquivo_nome="p.csv", arquivo_bytes=1)
        db.add_all([
            Importacao(**base, status=StatusImportacao.CONCLUIDA, concluida_em=velho),
            Importacao(**base, status=StatusImportacao.FALHOU, concluida_em=novo),
            Importacao(**base, status=StatusImportacao.NA_FILA),  # ainda na fila: nunca é apagada
        ])
        db.commit()


def _contagens():
    with SessionLocal() as db:
        return (db.query(VigiaRodada).count(), db.query(RefreshToken).count(), db.query(Importacao).count())


def test_com_espaco_sobrando_nao_faz_nada(logs, registros, monkeypatch):
    monkeypatch.setattr(limpeza, "livre_gb", lambda caminho=None: 50.0)
    assert limpeza.executar() is None
    assert (logs / "pmo.log.3").exists() and _contagens() == (2, 2, 3)


def test_com_pouco_espaco_apaga_so_o_recriavel(logs, registros, pouco_espaco):
    resultado = limpeza.executar()
    assert sorted(p.name for p in logs.iterdir()) == ["pmo.log", "pmo.log.1", "pmo.log.bak", "relatorio.txt"]
    assert _contagens() == (1, 1, 2)  # 1 relatório velho, 1 token vencido, 1 importação encerrada há 120 dias
    assert any("relatório(s) do vigia" in i for i in resultado.itens)
    assert pouco_espaco and "GB livres" in pouco_espaco[0]  # avisou no Mac


def test_simulacao_nao_apaga_nada(logs, registros, pouco_espaco):
    resultado = limpeza.executar(simular=True)
    assert len(resultado.itens) >= 4
    assert (logs / "pmo.log.2").exists() and _contagens() == (2, 2, 3)
    assert pouco_espaco == []


def test_lista_de_caches_nunca_inclui_nada_pessoal():
    proibidos = (".Trash", "Downloads", "Documents", "Desktop", "Pictures", "Movies", "Music")
    for pasta in limpeza.CACHES_PERMITIDOS.values():
        assert not any(p in str(pasta) for p in proibidos), pasta
        assert "cache" in str(pasta).lower()


def test_esvaziar_recusa_pasta_fora_da_lista(tmp_path):
    (tmp_path / "importante.txt").write_text("não pode sumir")
    with pytest.raises(ValueError):
        limpeza._esvaziar(tmp_path)
    assert (tmp_path / "importante.txt").exists()


def test_caches_de_desenvolvimento(tmp_path, monkeypatch, pouco_espaco):
    pastas = {nome: tmp_path / nome for nome in ("pip", "npm", "Homebrew")}
    for pasta in pastas.values():
        (pasta / "sub").mkdir(parents=True)
        arquivo = pasta / "sub" / "pacote.bin"
        arquivo.touch()
        os.truncate(arquivo, 60 * MB)  # arquivo esparso: "60 MB" sem ocupar disco de verdade
    pequeno = tmp_path / "pequeno"
    (pequeno).mkdir()
    (pequeno / "a").write_text("x")
    monkeypatch.setattr(limpeza, "CACHES_PERMITIDOS", {**pastas, "pequeno": pequeno})
    comandos = []
    resultado = limpeza.Resultado()
    limpeza.caches_de_desenvolvimento(resultado, simular=False, executar=lambda cmd, **_: comandos.append(cmd))
    assert any(c[-3:] == ["pip", "cache", "purge"] for c in comandos)  # o pip limpa pelo próprio comando
    assert list(pastas["Homebrew"].iterdir()) == [] and pastas["Homebrew"].exists()  # conteúdo some, a pasta fica
    assert (pequeno / "a").exists()  # menos de 50 MB: nem mexe
    assert len(resultado.itens) == 3


def test_sem_a_opcao_nao_mexe_nos_caches(logs, pouco_espaco, monkeypatch):
    chamado = []
    monkeypatch.setattr(limpeza, "caches_de_desenvolvimento", lambda *a, **k: chamado.append(1))
    limpeza.executar()
    assert chamado == []
    limpeza.executar(caches=True)
    assert chamado == [1]


def test_registra_no_log_e_o_vigia_informa(logs, registros, pouco_espaco, tmp_path):
    arquivo = tmp_path / "limpeza.log"
    handler = logging.FileHandler(arquivo, encoding="utf-8")
    handler.setFormatter(registro.FormatoJson())
    logging.getLogger().addHandler(handler)
    try:
        limpeza.executar()
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()
    eventos = [json.loads(l) for l in arquivo.read_text().splitlines()]
    evento = next(e for e in eventos if e.get("tipo") == "limpeza")
    assert evento["livre_antes_gb"] == 3.0 and evento["itens"]

    achados = regras.avaliar({
        "janela_minutos": 60, "log": sinais.resumir(eventos), "coletor": {}, "saude": {"ok": True},
        "migracao": {"em_dia": True}, "integridade": [], "seguranca": {"eventos": {}, "ips_distintos_com_falha": 0},
        "higiene": {"refresh_tokens_para_limpar": 0, "admins_sem_2fa": 0},
        "recursos": {"disco_livre_percentual": 60.0, "disco_livre_gb": 300},
    })
    assert [(a["codigo"], a["gravidade"]) for a in achados] == [("limpeza_automatica", "baixa")]


def test_linha_de_comando(logs, registros, pouco_espaco, capsys):
    assert limpeza.main(["--simular"]) == 0
    assert capsys.readouterr().out.startswith("Apagaria:")
