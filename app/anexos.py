"""
Armazenamento dos anexos das tarefas no disco do servidor.

- O nome no disco é aleatório (ANEXOS_DIR/<projeto_id>/<chave>): o nome que a pessoa deu fica só no
  banco, então "../../etc/passwd" ou "nota.pdf.exe" nunca viram caminho nem extensão no servidor.
- O arquivo é lido em pedaços, contando o tamanho (o limite vale mesmo sem Content-Length) e
  calculando o SHA-256 (integridade e auditoria).
- Executáveis e scripts são recusados pela extensão; o download sai SEMPRE como anexo, com nosniff
  e sandbox: o navegador não executa nem renderiza o conteúdo na origem do PMO.
"""

import hashlib
import logging
import mimetypes
import os
import re
import secrets
import shutil
import time
import unicodedata
from pathlib import Path
from typing import BinaryIO, Iterable, List, Set, Tuple

from app.config import settings

logger = logging.getLogger(__name__)

PEDACO = 1024 * 1024
EXTENSOES_BLOQUEADAS = {
    "exe", "msi", "bat", "cmd", "com", "scr", "pif", "cpl", "dll", "sys", "ps1", "psm1", "vbs", "vbe", "js", "jse",
    "wsf", "wsh", "hta", "jar", "sh", "bash", "zsh", "app", "dmg", "pkg", "apk", "deb", "rpm", "so", "dylib",
    "lnk", "reg", "iso", "msp", "gadget",
}


class AnexoInvalido(Exception):
    """Mensagem para a pessoa (vira 413/415/422)."""

    def __init__(self, mensagem: str, status: int = 422) -> None:
        super().__init__(mensagem)
        self.status = status


def pasta_base() -> Path:
    return Path(settings.anexos_dir)


def caminho(projeto_id: int, chave: str) -> Path:
    if not re.fullmatch(r"[0-9a-f]{32}", chave):  # a chave é sempre nossa; isto é só cinto e suspensório
        raise ValueError("chave de anexo inválida")
    return pasta_base() / str(int(projeto_id)) / chave


def nome_seguro(nome: str) -> str:
    """Só o nome do arquivo (sem pastas), sem caracteres de controle, até 255 caracteres."""
    nome = unicodedata.normalize("NFC", nome or "").replace("\\", "/").rsplit("/", 1)[-1]
    nome = "".join(ch for ch in nome if unicodedata.category(ch)[0] != "C").strip().strip(".")
    if not nome:
        return "arquivo"
    base, ponto, ext = nome.rpartition(".")
    if len(nome) > 255:
        nome = (base[: 254 - len(ext)] + "." + ext) if ponto else nome[:255]
    return nome


def extensoes(nome: str) -> List[str]:
    """Todas as extensões ("relatorio.pdf.exe" -> ["pdf", "exe"])."""
    partes = nome.lower().split(".")[1:]
    return [p.strip() for p in partes if p.strip()]


def validar_nome(nome: str) -> None:
    proibidas = [e for e in extensoes(nome) if e in EXTENSOES_BLOQUEADAS]
    if proibidas:
        raise AnexoInvalido(
            f"Arquivos .{proibidas[-1]} não são aceitos (programas e scripts). Compacte em .zip se precisar enviar.", 415,
        )


def tipo_mime(nome: str) -> str:
    """Pelo nome (não pelo que o navegador declarou)."""
    return mimetypes.guess_type(nome)[0] or "application/octet-stream"


def gravar(projeto_id: int, origem: BinaryIO, limite_bytes: int) -> Tuple[str, int, str]:
    """Grava o conteúdo e devolve (chave, tamanho, sha256). Passou do limite ou vazio: apaga e recusa."""
    chave = secrets.token_hex(16)
    destino = caminho(projeto_id, chave)
    destino.parent.mkdir(parents=True, exist_ok=True)
    parcial = destino.with_name(chave + ".parcial")
    resumo, total = hashlib.sha256(), 0
    try:
        with open(parcial, "xb") as saida:
            while pedaco := origem.read(PEDACO):
                total += len(pedaco)
                if total > limite_bytes:
                    raise AnexoInvalido(f"Arquivo maior que o permitido ({limite_bytes // (1024 * 1024)} MB).", 413)
                resumo.update(pedaco)
                saida.write(pedaco)
        if total == 0:
            raise AnexoInvalido("O arquivo está vazio.")
        os.chmod(parcial, 0o600)
        parcial.rename(destino)
    except BaseException:
        parcial.unlink(missing_ok=True)
        raise
    return chave, total, resumo.hexdigest()


def apagar(projeto_id: int, chaves: Iterable[str]) -> None:
    """Depois do commit: o arquivo some do disco (o registro já saiu do banco)."""
    for chave in chaves:
        try:
            caminho(projeto_id, chave).unlink(missing_ok=True)
        except (OSError, ValueError):
            logger.warning("Não foi possível apagar o anexo %s do projeto %s", chave, projeto_id, exc_info=True)


def orfaos(chaves_no_banco: Set[str], idade_minima_s: int = 3600) -> List[Path]:
    """Arquivos sem registro no banco (projeto excluído, envio interrompido) e com mais de 1 h."""
    base = pasta_base()
    if not base.is_dir():
        return []
    limite = time.time() - idade_minima_s
    achados = []
    for arquivo in base.glob("*/*"):
        if not arquivo.parent.name.isdigit():  # só as pastas de projeto (a de modelos de slides é outra coisa)
            continue
        chave = arquivo.name.removesuffix(".parcial")
        if arquivo.is_file() and chave not in chaves_no_banco and arquivo.stat().st_mtime < limite:
            achados.append(arquivo)
    return achados


def espelhar(destino: Path) -> int:
    """
    Backup dos anexos: copia para `destino` os arquivos que ainda não estão lá. Como cada anexo tem
    nome único e nunca muda, a cópia incremental é suficiente (e barata). Devolve quantos copiou.
    """
    base = pasta_base()
    if not base.is_dir():
        return 0
    copiados = 0
    for arquivo in base.glob("*/*"):
        if not arquivo.is_file() or arquivo.name.endswith(".parcial"):
            continue
        alvo = destino / arquivo.parent.name / arquivo.name
        if not alvo.exists():
            alvo.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(arquivo, alvo)
            copiados += 1
    return copiados
