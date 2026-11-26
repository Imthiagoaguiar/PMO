"""
Limpeza quando o disco está quase cheio. Só apaga o que é RECRIÁVEL, de uma lista fechada:

  sistema (sempre):   logs rotacionados antigos (mantém o atual e o .1, que o vigia lê),
                      relatórios do vigia com mais de LIMPEZA_RETENCAO_DIAS, refresh tokens vencidos,
                      registros de importação já encerrados há mais de LIMPEZA_RETENCAO_DIAS;
  máquina (opcional): caches do pip, do npm e do Homebrew (--caches-de-desenvolvimento).

Nunca toca na Lixeira, em Downloads/Documentos ou em dados de projetos.

    python -m app.limpeza                    # só limpa se o disco tiver menos que LIMPEZA_MIN_GB livres
    python -m app.limpeza --agora            # limpa mesmo com espaço sobrando
    python -m app.limpeza --simular          # mostra o que faria, sem apagar nada
    python -m app.limpeza --caches-de-desenvolvimento   # inclui pip/npm/Homebrew (máquina de dev)

No Mac, `scripts/instalar-limpeza.sh` agenda isto a cada hora (launchd).
"""

import argparse
import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Callable, List, Optional

from sqlalchemy import delete, or_
from sqlalchemy.orm import Session

from app import registro
from app.config import settings
from app.database import SessionLocal
from app.models import Importacao, RefreshToken, StatusImportacao, VigiaRodada
from app.security import agora_utc

logger = logging.getLogger("pmo.limpeza")
HOME = Path.home()
# Os ÚNICOS caches de máquina que a limpeza pode esvaziar (recriados sozinhos quando preciso).
CACHES_PERMITIDOS = {
    "pip": HOME / "Library/Caches/pip" if sys.platform == "darwin" else HOME / ".cache/pip",
    "npm": HOME / ".npm/_cacache",
    "Homebrew": HOME / "Library/Caches/Homebrew",
    "uv": HOME / ".cache/uv",  # usado para gerar os locks (scripts/travar-dependencias.sh)
}


@dataclass
class Resultado:
    itens: List[str] = field(default_factory=list)
    bytes_estimados: int = 0

    def anotar(self, texto: str, tamanho: int = 0) -> None:
        self.itens.append(texto)
        self.bytes_estimados += tamanho


def livre_gb(caminho: Path = Path("/")) -> float:
    return round(shutil.disk_usage(caminho).free / 1024**3, 1)


def _tamanho(caminho: Path) -> int:
    if caminho.is_file():
        return caminho.stat().st_size
    return sum(f.stat().st_size for f in caminho.rglob("*") if f.is_file() and not f.is_symlink())


# ---------- Sistema ----------


def logs_rotacionados(arquivos: List[Optional[str]], resultado: Resultado, simular: bool) -> None:
    """pmo.log.2, .3... (o atual e o .1 ficam: o vigia lê a última hora deles)."""
    for arquivo in filter(None, arquivos):
        base = Path(arquivo)
        if not base.parent.exists():
            continue
        for antigo in sorted(base.parent.glob(base.name + ".*")):
            sufixo = antigo.name[len(base.name) + 1:]
            if sufixo.isdigit() and int(sufixo) >= 2:
                resultado.anotar(f"log antigo {antigo.name}", antigo.stat().st_size)
                if not simular:
                    antigo.unlink()


def registros_velhos(db: Session, resultado: Resultado, simular: bool) -> None:
    corte = agora_utc() - timedelta(days=settings.limpeza_retencao_dias)
    tarefas = [
        ("relatório(s) do vigia", delete(VigiaRodada).where(VigiaRodada.iniciada_em < corte)),
        ("refresh token(s) vencido(s)", delete(RefreshToken).where(
            or_(RefreshToken.expira_em < agora_utc() - timedelta(days=1),
                RefreshToken.revogado_em < agora_utc() - timedelta(days=1)))),
        ("importação(ões) encerrada(s)", delete(Importacao).where(
            Importacao.status.in_([StatusImportacao.CONCLUIDA, StatusImportacao.FALHOU]), Importacao.concluida_em < corte)),
    ]
    for descricao, comando in tarefas:
        quantidade = db.execute(comando).rowcount
        if quantidade:
            resultado.anotar(f"{quantidade} {descricao}")
    if simular:
        db.rollback()  # simulação: contou o que apagaria e desfaz
    else:
        db.commit()


# ---------- Máquina de desenvolvimento ----------


def anexos_orfaos(db: Session, resultado: Resultado, simular: bool) -> None:
    """Arquivos de anexo sem registro no banco (projeto excluído, envio interrompido) há mais de 1 h."""
    from sqlalchemy import select

    from app import anexos
    from app.models import AnexoTarefa

    sobras = anexos.orfaos(set(db.scalars(select(AnexoTarefa.chave))))
    if not sobras:
        return
    tamanho = sum(a.stat().st_size for a in sobras)
    if not simular:
        for arquivo in sobras:
            arquivo.unlink(missing_ok=True)
    resultado.itens.append(f"{len(sobras)} anexo(s) órfão(s) ({tamanho / 1e6:.0f} MB)")


def aparar_logs_de_servico(resultado: Resultado, simular: bool, pasta: Path = Path("logs"),
                           maximo_mb: Optional[int] = None, manter_mb: int = 1) -> None:
    """
    logs/servico-*.log (a saída dos serviços do launchd, scripts/instalar-servicos.sh) não têm rotação:
    acima de LIMPEZA_SERVICO_LOG_MAX_MB, ficam só com o último `manter_mb` (a partir de uma linha inteira).
    O launchd grava em modo "acrescentar", então cortar o arquivo com o serviço rodando é seguro.
    """
    limite = (maximo_mb or settings.limpeza_servico_log_max_mb) * 1024 * 1024
    manter = manter_mb * 1024 * 1024
    for arquivo in sorted(pasta.glob("servico-*.log")):
        tamanho = arquivo.stat().st_size
        if tamanho <= limite:
            continue
        if not simular:
            with open(arquivo, "rb") as origem:
                origem.seek(-manter, 2)
                final = origem.read()
            final = final[final.find(b"\n") + 1:]
            with open(arquivo, "r+b") as destino:
                destino.write(b"[... inicio cortado pela limpeza automatica ...]\n" + final)
                destino.truncate()
        resultado.itens.append(f"{arquivo.name} cortado ({tamanho / 1e6:.0f} MB -> {manter_mb} MB)")


def caches_de_desenvolvimento(resultado: Resultado, simular: bool,
                              executar: Callable[..., object] = subprocess.run) -> None:
    for nome, pasta in CACHES_PERMITIDOS.items():
        if not pasta.exists():
            continue
        tamanho = _tamanho(pasta)
        if tamanho < 50 * 1024**2:  # menos de 50 MB: não vale mexer
            continue
        resultado.anotar(f"cache do {nome} ({tamanho / 1024**2:.0f} MB)", tamanho)
        if simular:
            continue
        if nome == "pip":
            executar([sys.executable, "-m", "pip", "cache", "purge"], capture_output=True, check=False)
        elif nome == "npm" and shutil.which("npm"):
            executar([shutil.which("npm"), "cache", "clean", "--force"], capture_output=True, check=False)
        elif nome == "uv" and (uv := shutil.which("uv") or str(Path(sys.executable).with_name("uv"))) and Path(uv).exists():
            executar([uv, "cache", "clean"], capture_output=True, check=False)
        else:
            _esvaziar(pasta)


def _esvaziar(pasta: Path) -> None:
    """Apaga o CONTEÚDO de uma pasta da lista permitida (nunca a pasta em si nem nada fora dela)."""
    if pasta.resolve() not in {p.resolve() for p in CACHES_PERMITIDOS.values()}:
        raise ValueError(f"pasta fora da lista permitida: {pasta}")
    for item in pasta.iterdir():
        if item.is_dir() and not item.is_symlink():
            shutil.rmtree(item, ignore_errors=True)
        else:
            item.unlink(missing_ok=True)


# ---------- Execução ----------


def avisar_no_mac(titulo: str, texto: str, executar: Callable[..., object] = subprocess.run) -> None:
    if sys.platform != "darwin":
        return
    script = f'display notification "{texto.replace(chr(34), "")}" with title "{titulo.replace(chr(34), "")}"'
    executar(["osascript", "-e", script], capture_output=True, check=False)


def executar(agora: bool = False, simular: bool = False, caches: bool = False) -> Optional[Resultado]:
    # Sempre (é barato e evita que um log sem rotação encha o disco aos poucos), mesmo com espaço sobrando.
    aparados = Resultado()
    aparar_logs_de_servico(aparados, simular)
    if aparados.itens:
        logger.info("Limpeza: %s", "; ".join(aparados.itens), extra={"tipo": "limpeza", "itens": aparados.itens})
    antes = livre_gb()
    if not agora and antes >= settings.limpeza_min_gb:
        logger.info("Limpeza: %s GB livres (mínimo %s GB): nada a fazer", antes, settings.limpeza_min_gb)
        return None

    resultado = Resultado()
    logs_rotacionados([settings.log_arquivo, settings.log_arquivo_trabalhador], resultado, simular)
    with SessionLocal() as db:
        registros_velhos(db, resultado, simular)
        anexos_orfaos(db, resultado, simular)
    if caches:
        caches_de_desenvolvimento(resultado, simular)

    depois = livre_gb()
    liberado = round(max(depois - antes, 0), 1)
    if simular:
        logger.info("Limpeza (simulação): apagaria %s", "; ".join(resultado.itens) or "nada")
        return resultado
    logger.info(
        "Limpeza: %s GB livres antes, %s GB depois (%s GB liberados): %s", antes, depois, liberado,
        "; ".join(resultado.itens) or "nada para apagar",
        extra={"tipo": "limpeza", "livre_antes_gb": antes, "livre_depois_gb": depois, "liberado_gb": liberado,
               "itens": resultado.itens},
    )
    if resultado.itens:
        aviso = f"Liberados {liberado} GB; disco com {depois} GB livres."
        if depois < settings.limpeza_min_gb:
            aviso += " Ainda está baixo: veja a Lixeira e Downloads."
        avisar_no_mac("OMPY: limpeza do disco", aviso)
    return resultado


def main(argv: Optional[list] = None) -> int:
    argumentos = argparse.ArgumentParser(description="Limpeza do que é recriável quando o disco está quase cheio")
    argumentos.add_argument("--agora", action="store_true", help="limpa mesmo com espaço sobrando")
    argumentos.add_argument("--simular", action="store_true", help="só mostra o que faria")
    argumentos.add_argument("--caches-de-desenvolvimento", action="store_true", help="inclui pip, npm e Homebrew")
    args = argumentos.parse_args(argv)
    registro.configurar(settings.log_arquivo_limpeza)
    resultado = executar(agora=args.agora, simular=args.simular, caches=args.caches_de_desenvolvimento)
    if resultado is None:
        print(f"Disco com {livre_gb()} GB livres (mínimo {settings.limpeza_min_gb} GB): nada a fazer.")
    else:
        prefixo = "Apagaria" if args.simular else "Apagado"
        print(f"{prefixo}: " + ("; ".join(resultado.itens) or "nada"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
