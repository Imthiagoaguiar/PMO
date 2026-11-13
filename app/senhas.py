"""
Política de senhas (NIST SP 800-63B): comprimento mínimo + recusar senhas previsíveis ou vazadas,
em vez de exigir "1 maiúscula, 1 símbolo" (o que só gera "Senha@2026").

Rodado ao DEFINIR uma senha (cadastro, troca, redefinição), nunca no login.
"""

import hashlib
import logging
import re
from typing import Optional

import httpx

from app.config import settings
from app.texto import normalizar

logger = logging.getLogger(__name__)

URL_PWNED = "https://api.pwnedpasswords.com/range/"
TIMEOUT_SEGUNDOS = 3.0

# Bases que aparecem com números/símbolos em volta ("Senha@123456", "Empresa2026!").
# Comparação exata com a parte só-letras da senha (ex.: "SenhaForte!2026" -> "senhaforte" passa).
PALAVRAS_COMUNS = {
    "senha", "password", "passw", "pass", "admin", "administrador", "root", "usuario", "user",
    "login", "acesso", "entrar", "mudar", "mudarsenha", "trocar", "trocarsenha", "teste", "test",
    "qwerty", "qwertyuiop", "asdf", "asdfgh", "zxcvbn", "abc", "abcdef", "abcd", "iloveyou",
    "teamo", "brasil", "brazil", "flamengo", "corinthians", "palmeiras", "saopaulo", "vasco",
    "gremio", "cruzeiro", "santos", "jesus", "deus", "deusefiel", "amor", "familia", "empresa",
    "pmo", "projeto", "projetos", "waterfall", "welcome", "bemvindo", "master", "dragon",
    "monkey", "letmein", "football", "baseball", "sunshine", "princess", "secret", "segredo",
}

SEQUENCIAS = ("0123456789", "abcdefghijklmnopqrstuvwxyz", "qwertyuiopasdfghjklzxcvbnm", "1qaz2wsx3edc4rfv")


class SenhaFraca(ValueError):
    pass


def _e_sequencia(texto: str) -> bool:
    t = texto.lower()
    return any(t in seq or t in seq[::-1] for seq in SEQUENCIAS) or len(set(t)) <= 2


def senha_vazada(senha: str) -> bool:
    """
    k-anonimato: envia só os 5 primeiros caracteres do SHA-1 e compara o resto localmente.
    Se o serviço estiver fora do ar, não bloqueia o usuário (as outras regras continuam valendo).
    """
    if not settings.verificar_senhas_vazadas:
        return False
    digest = hashlib.sha1(senha.encode(), usedforsecurity=False).hexdigest().upper()
    prefixo, sufixo = digest[:5], digest[5:]
    try:
        resposta = httpx.get(
            URL_PWNED + prefixo,
            # Padding: a resposta tem tamanho parecido para qualquer prefixo (não vaza qual foi consultado).
            headers={"Add-Padding": "true", "User-Agent": "pmo-api"},
            timeout=TIMEOUT_SEGUNDOS,
        )
        resposta.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("Have I Been Pwned indisponível (%s): senha não verificada em vazamentos", exc)
        return False
    for linha in resposta.text.splitlines():
        sufixo_linha, _, ocorrencias = linha.strip().partition(":")
        if sufixo_linha == sufixo and ocorrencias.strip() not in ("", "0"):  # padding vem com 0
            return True
    return False


def validar_senha(senha: str, email: Optional[str] = None, nome: Optional[str] = None) -> None:
    """Levanta SenhaFraca com uma mensagem para o usuário. O comprimento já é validado pelo schema."""
    letras = re.sub(r"[^a-z]", "", normalizar(senha))

    if _e_sequencia(senha) or _e_sequencia(re.sub(r"[^a-z0-9]", "", senha.lower())) or len(set(senha)) < 5:
        raise SenhaFraca("Senha previsível (sequência ou caracteres repetidos). Use uma frase ou palavras aleatórias.")
    if not letras or letras in PALAVRAS_COMUNS or _e_sequencia(letras):
        raise SenhaFraca("Senha muito comum. Evite variações de “senha”, “admin” e similares: prefira uma frase.")

    pessoais = set()
    if email:
        pessoais.update(p for p in re.split(r"[^a-z0-9]+", normalizar(email.split("@")[0])) if len(p) >= 4)
    if nome:
        pessoais.update(p for p in re.split(r"[^a-z0-9]+", normalizar(nome)) if len(p) >= 4)
    if any(p in normalizar(senha) for p in pessoais):
        raise SenhaFraca("Não use seu nome ou e-mail na senha.")

    if senha_vazada(senha):
        raise SenhaFraca(
            "Esta senha já apareceu em vazamentos de dados na internet e é testada por atacantes. Escolha outra."
        )
