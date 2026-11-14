"""
Backup do banco (PostgreSQL) com pg_dump, conferência e retenção.

    python -m app.backup                         # um backup agora (o agendador roda isto 1x por dia)
    python -m app.backup --testar-restauracao    # backup + restaura num banco temporário e confere
    python -m app.backup --listar                # backups existentes
    python -m app.backup --agendado              # fica rodando: 1 backup por dia em BACKUP_HORA (Docker)

- Formato custom do pg_dump (comprimido, restaurável tabela a tabela com pg_restore).
- Gravado como .parcial e só vira .dump depois de conferido (pg_restore --list): um backup
  pela metade nunca é confundido com um bom.
- A senha do banco vai por variável de ambiente (PGPASSWORD), nunca na linha de comando (visível no `ps`).
- Retenção: mantém os BACKUP_RETENCAO mais recentes. Com BACKUP_COPIA_DIR (outro disco, pasta
  sincronizada com a nuvem), cada backup também é copiado para lá: um backup no MESMO disco do banco
  não protege contra o disco morrer.
- O vigia avisa se o último backup tiver mais de BACKUP_MAX_HORAS (ou se não houver nenhum).

Restaurar de verdade (substitui os dados! faça com a API parada):
    pg_restore --clean --if-exists --no-owner -d "<banco>" backups/pmo-AAAAMMDD-HHMMSS.dump
"""

import argparse
import hashlib
import logging
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url

from app import registro
from app.config import settings

logger = logging.getLogger("pmo.backup")
PADRAO = "pmo-*.dump"
LOCAIS_CONHECIDOS = ["/opt/homebrew/opt/postgresql@17/bin", "/usr/local/opt/postgresql@17/bin",
                     "/usr/lib/postgresql/17/bin", "/usr/bin"]


class FalhaBackup(Exception):
    pass


def binario(nome: str) -> str:
    """pg_dump/pg_restore/createdb da MESMA versão do servidor (17): pg_dump antigo recusa servidor novo."""
    candidatos = [settings.pg_bin_dir] if settings.pg_bin_dir else []
    candidatos += LOCAIS_CONHECIDOS
    for pasta in candidatos:
        caminho = Path(pasta) / nome
        if caminho.is_file() and os.access(caminho, os.X_OK):
            return str(caminho)
    achado = shutil.which(nome)
    if achado:
        return achado
    raise FalhaBackup(f"{nome} não encontrado: instale o cliente do PostgreSQL 17 ou configure PG_BIN_DIR")


def conexao(url_texto: str) -> Tuple[List[str], Dict[str, str], str]:
    """(argumentos de conexão SEM senha, ambiente com PGPASSWORD, nome do banco)."""
    url: URL = make_url(url_texto)
    if not url.drivername.startswith("postgresql"):
        raise FalhaBackup("o backup com pg_dump só funciona com PostgreSQL")
    argumentos = []
    if url.host:
        argumentos += ["-h", url.host]
    if url.port:
        argumentos += ["-p", str(url.port)]
    if url.username:
        argumentos += ["-U", url.username]
    ambiente = {**os.environ, "PGCONNECT_TIMEOUT": "10"}
    if url.password:
        ambiente["PGPASSWORD"] = url.password
    return argumentos, ambiente, url.database or "postgres"


def _rodar(comando: List[str], ambiente: Dict[str, str]) -> subprocess.CompletedProcess:
    resultado = subprocess.run(comando, env=ambiente, capture_output=True, text=True, check=False)
    if resultado.returncode != 0:
        # O stderr do pg_dump não tem a senha (ela vai por PGPASSWORD); mesmo assim, só as últimas linhas.
        raise FalhaBackup(f"{Path(comando[0]).name} falhou: {resultado.stderr.strip()[-500:]}")
    return resultado


def listar(pasta: Path) -> List[Path]:
    return sorted(pasta.glob(PADRAO)) if pasta.exists() else []


def fazer_backup(pasta: Optional[Path] = None) -> Path:
    pasta = pasta or Path(settings.backup_dir)
    pasta.mkdir(parents=True, exist_ok=True)
    os.chmod(pasta, 0o700)  # o dump tem hashes de senha e segredos de 2FA (cifrados): só o dono lê
    argumentos, ambiente, banco = conexao(settings.database_url)
    carimbo = datetime.now(ZoneInfo(settings.fuso_horario)).strftime("%Y%m%d-%H%M%S")
    final = pasta / f"pmo-{carimbo}.dump"
    parcial = final.with_suffix(".parcial")
    inicio = time.perf_counter()
    try:
        _rodar([binario("pg_dump"), *argumentos, "-d", banco, "-Fc", "--no-owner", "--no-privileges",
                "-f", str(parcial)], ambiente)
        os.chmod(parcial, 0o600)
        conteudo = _rodar([binario("pg_restore"), "--list", str(parcial)], ambiente).stdout
        if "TABLE DATA public projetos" not in conteudo or "alembic_version" not in conteudo:
            raise FalhaBackup("o arquivo gerado não tem as tabelas esperadas (projetos, alembic_version)")
        parcial.rename(final)
    except Exception:
        parcial.unlink(missing_ok=True)
        raise

    resumo = hashlib.sha256(final.read_bytes()).hexdigest()
    removidos = aplicar_retencao(pasta, settings.backup_retencao)
    copia = None
    if settings.backup_copia_dir:
        destino = Path(settings.backup_copia_dir)
        destino.mkdir(parents=True, exist_ok=True)
        copia = shutil.copy2(final, destino / final.name)
        aplicar_retencao(destino, settings.backup_retencao)
    logger.info(
        "Backup concluído: %s (%.1f MB em %.1f s)", final.name, final.stat().st_size / 1e6, time.perf_counter() - inicio,
        extra={"tipo": "backup", "arquivo": final.name, "bytes": final.stat().st_size, "sha256": resumo,
               "copia": str(copia) if copia else None, "removidos": removidos},
    )
    return final


def aplicar_retencao(pasta: Path, manter: int) -> List[str]:
    """Apaga os mais antigos além dos `manter` mais recentes (só arquivos pmo-*.dump desta pasta)."""
    antigos = listar(pasta)[:-manter] if manter > 0 else []
    for arquivo in antigos:
        arquivo.unlink()
    return [a.name for a in antigos]


def testar_restauracao(arquivo: Path) -> Dict[str, int]:
    """
    Restaura o backup num banco TEMPORÁRIO, confere as tabelas principais e apaga o banco.
    Um backup que nunca foi restaurado é só uma esperança. Precisa de BACKUP_ADMIN_URL (um usuário que
    possa criar bancos): o usuário do app, de propósito, não pode.
    """
    url_admin = settings.backup_admin_url or settings.database_url
    argumentos, ambiente, _ = conexao(url_admin)
    temporario = f"pmo_restauracao_teste_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    try:
        _rodar([binario("createdb"), *argumentos, temporario], ambiente)
    except FalhaBackup as exc:
        raise FalhaBackup(f"não foi possível criar o banco temporário (configure BACKUP_ADMIN_URL): {exc}")
    try:
        _rodar([binario("pg_restore"), *argumentos, "-d", temporario, "--no-owner", "--no-privileges",
                "--exit-on-error", str(arquivo)], ambiente)
        motor = create_engine(make_url(url_admin).set(database=temporario))
        try:
            with motor.connect() as c:
                # Nomes de tabela fixos (não vêm do usuário).
                contagens = {t: c.execute(text(f"SELECT count(*) FROM {t}")).scalar()  # nosec B608
                             for t in ("usuarios", "projetos", "fases", "tarefas", "auditoria")}
                contagens["alembic_version"] = c.execute(text("SELECT count(*) FROM alembic_version")).scalar()
        finally:
            motor.dispose()
        if contagens["alembic_version"] != 1 or (contagens["projetos"] and contagens["fases"] < 5 * contagens["projetos"]):
            raise FalhaBackup(f"restauração inconsistente: {contagens}")
        logger.info("Restauração testada com sucesso: %s", arquivo.name,
                    extra={"tipo": "backup_restaurado", "arquivo": arquivo.name, "contagens": contagens})
        return contagens
    finally:
        subprocess.run([binario("dropdb"), *argumentos, "--if-exists", temporario], env=ambiente,
                       capture_output=True, check=False)


def idade_do_ultimo(pasta: Optional[Path] = None) -> Optional[timedelta]:
    backups = listar(pasta or Path(settings.backup_dir))
    if not backups:
        return None
    return timedelta(seconds=time.time() - backups[-1].stat().st_mtime)


def _proxima_execucao(agora: datetime) -> datetime:
    hora, minuto = (int(x) for x in settings.backup_hora.split(":"))
    alvo = agora.replace(hour=hora, minute=minuto, second=0, microsecond=0)
    return alvo if alvo > agora else alvo + timedelta(days=1)


def main(argv: Optional[list] = None) -> int:
    argumentos = argparse.ArgumentParser(description="Backup do banco do PMO")
    argumentos.add_argument("--testar-restauracao", action="store_true", help="restaura num banco temporário e confere")
    argumentos.add_argument("--listar", action="store_true", help="lista os backups existentes")
    argumentos.add_argument("--agendado", action="store_true", help="fica rodando: 1 backup por dia em BACKUP_HORA")
    args = argumentos.parse_args(argv)
    registro.configurar(settings.log_arquivo_backup)
    if not settings.backup_dir:
        print("BACKUP_DIR não configurado.")
        return 1

    if args.listar:
        for arquivo in listar(Path(settings.backup_dir)):
            print(f"{arquivo.name}  {arquivo.stat().st_size / 1e6:.1f} MB")
        return 0

    while True:
        try:
            feito = fazer_backup()
            print(f"Backup: {feito} ({feito.stat().st_size / 1e6:.1f} MB)")
            if args.testar_restauracao:
                print(f"Restauração conferida: {testar_restauracao(feito)}")
        except FalhaBackup as exc:
            logger.error("Backup falhou: %s", exc, extra={"tipo": "backup", "falhou": True})
            print(f"FALHOU: {exc}", file=sys.stderr)
            if not args.agendado:
                return 1
        if not args.agendado:
            return 0
        agora = datetime.now(ZoneInfo(settings.fuso_horario))
        espera = (_proxima_execucao(agora) - agora).total_seconds()
        logger.info("Próximo backup em %.1f h", espera / 3600)
        time.sleep(espera)


if __name__ == "__main__":
    sys.exit(main())
