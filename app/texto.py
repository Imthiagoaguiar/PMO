import unicodedata


def normalizar(texto: str) -> str:
    """'  Iniciação ' -> 'iniciacao'. Usado para casar fases e cabeçalhos sem depender de acento/caixa."""
    sem_acento = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode()
    return sem_acento.strip().lower()
