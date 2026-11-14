"""Backup do banco: pg_dump conferido, retenção, senha fora da linha de comando, restauração e alerta do vigia."""

import os
import stat
import time
from pathlib import Path

import pytest

from app import backup
from app.config import settings
from app.database import engine
from app.vigia import regras, sinais, sondas

so_postgres = pytest.mark.skipif(engine.dialect.name != "postgresql", reason="pg_dump exige Postgres (TEST_DATABASE_URL)")


def test_senha_vai_por_variavel_de_ambiente_nunca_na_linha_de_comando():
    argumentos, ambiente, banco = backup.conexao("postgresql+psycopg2://pmo_app:s3nh4-secreta@db.interna:5433/pmo")
    assert argumentos == ["-h", "db.interna", "-p", "5433", "-U", "pmo_app"]
    assert "s3nh4-secreta" not in " ".join(argumentos)
    assert ambiente["PGPASSWORD"] == "s3nh4-secreta" and banco == "pmo"


def test_so_postgres():
    with pytest.raises(backup.FalhaBackup):
        backup.conexao("sqlite:///x.db")


def test_retencao_mantem_os_mais_recentes_e_nao_toca_em_outros_arquivos(tmp_path):
    for i in range(5):
        (tmp_path / f"pmo-2026111{i}-020000.dump").write_text("x")
    (tmp_path / "anotacoes.txt").write_text("meu")
    (tmp_path / "outro-sistema.dump").write_text("alheio")
    removidos = backup.aplicar_retencao(tmp_path, manter=2)
    assert removidos == ["pmo-20261110-020000.dump", "pmo-20261111-020000.dump", "pmo-20261112-020000.dump"]
    assert sorted(p.name for p in tmp_path.iterdir()) == [
        "anotacoes.txt", "outro-sistema.dump", "pmo-20261113-020000.dump", "pmo-20261114-020000.dump"]


def _sinais(**backup_):
    return {"janela_minutos": 60, "log": sinais.resumir([]), "coletor": {}, "saude": {"ok": True},
            "migracao": {"em_dia": True}, "integridade": [], "seguranca": {"eventos": {}, "ips_distintos_com_falha": 0},
            "higiene": {"refresh_tokens_para_limpar": 0, "admins_sem_2fa": 0},
            "recursos": {"disco_livre_percentual": 60.0, "disco_livre_gb": 300}, "backup": backup_}


@pytest.mark.parametrize("estado, codigos", [
    ({"configurado": True, "horas_desde_o_ultimo": None, "copia_externa": True}, ["sem_backup"]),
    ({"configurado": True, "horas_desde_o_ultimo": 50.0, "ultimo": "x", "copia_externa": True}, ["backup_atrasado"]),
    ({"configurado": True, "horas_desde_o_ultimo": 3.0, "copia_externa": False}, ["backup_so_local"]),
    ({"configurado": True, "horas_desde_o_ultimo": 3.0, "copia_externa": True}, []),
    ({"configurado": False}, []),
])
def test_vigia_cobra_o_backup(estado, codigos):
    assert [a["codigo"] for a in regras.avaliar(_sinais(**estado))] == codigos


def test_sonda_le_a_idade_do_ultimo_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "backup_dir", str(tmp_path))
    assert sondas.backup()["horas_desde_o_ultimo"] is None
    arquivo = tmp_path / "pmo-20261114-020000.dump"
    arquivo.write_text("x")
    tres_horas = time.time() - 3 * 3600
    os.utime(arquivo, (tres_horas, tres_horas))
    estado = sondas.backup()
    assert estado["quantidade"] == 1 and 2.9 < estado["horas_desde_o_ultimo"] < 3.1


# ---------- Com Postgres de verdade ----------


@so_postgres
def test_backup_real_conferido_e_restauravel(tmp_path, monkeypatch, criar_usuario, client, login):
    from app.models import PapelUsuario

    criar_usuario("dono@empresa.com.br", PapelUsuario.GERENTE)
    client.post("/projetos/manual", json={"nome_projeto": "Para o backup"}, headers=login("dono@empresa.com.br"))
    with engine.begin() as c:  # o banco de teste é criado pelo create_all: o backup exige alembic_version
        c.exec_driver_sql("CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) PRIMARY KEY)")
        c.exec_driver_sql("INSERT INTO alembic_version VALUES ('teste') ON CONFLICT DO NOTHING")
    copia = tmp_path / "copia"
    monkeypatch.setattr(settings, "backup_copia_dir", str(copia))
    try:
        arquivo = backup.fazer_backup(tmp_path / "backups")
        assert stat.S_IMODE(arquivo.stat().st_mode) == 0o600  # só o dono lê
        assert stat.S_IMODE(arquivo.parent.stat().st_mode) == 0o700
        assert not list(arquivo.parent.glob("*.parcial"))
        assert (copia / arquivo.name).exists()
        if os.environ.get("BACKUP_ADMIN_URL_TESTE") or os.environ.get("CI"):
            monkeypatch.setattr(settings, "backup_admin_url", os.environ.get("BACKUP_ADMIN_URL_TESTE"))
            contagens = backup.testar_restauracao(arquivo)
            assert contagens["projetos"] == 1 and contagens["fases"] == 5 and contagens["usuarios"] == 1
    finally:
        with engine.begin() as c:
            c.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")


@so_postgres
def test_senha_errada_falha_sem_deixar_arquivo_e_sem_vazar_a_senha(tmp_path, monkeypatch):
    from sqlalchemy.engine import make_url

    errada = make_url(settings.database_url).set(password="senha-errada-123")
    monkeypatch.setattr(settings, "database_url", errada.render_as_string(hide_password=False))
    monkeypatch.setenv("PGHOST", "")  # sem atalhos do ambiente
    with pytest.raises(backup.FalhaBackup) as erro:
        backup.fazer_backup(tmp_path)
    assert "senha-errada-123" not in str(erro.value)
    assert list(tmp_path.iterdir()) == []


def test_linha_de_comando_sem_pasta_configurada(monkeypatch, capsys):
    monkeypatch.setattr(settings, "backup_dir", "")
    assert backup.main([]) == 1
    assert "BACKUP_DIR" in capsys.readouterr().out


def test_proxima_execucao_agendada(monkeypatch):
    from datetime import datetime

    monkeypatch.setattr(settings, "backup_hora", "02:00")
    assert backup._proxima_execucao(datetime(2026, 11, 14, 1, 0)) == datetime(2026, 11, 14, 2, 0)
    assert backup._proxima_execucao(datetime(2026, 11, 14, 3, 0)) == datetime(2026, 11, 15, 2, 0)


def test_limpeza_nunca_apaga_backups():
    from app import limpeza

    assert not any("backup" in str(p).lower() for p in limpeza.CACHES_PERMITIDOS.values())
    assert Path(settings.backup_dir or "backups").name == "backups"
