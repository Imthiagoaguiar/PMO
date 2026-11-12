"""
E-mails transacionais com o Resend.

Roda como BackgroundTask DEPOIS da resposta: o e-mail nunca atrasa nem derruba a criação
do projeto. Falhas vão só para o log.
"""

import html
import logging

import resend

from app.config import settings

logger = logging.getLogger(__name__)


def _linha_unica(texto: str) -> str:
    # Quebras de linha no assunto não têm uso legítimo (e já foram vetor de injeção de cabeçalho).
    return " ".join(texto.split())


def enviar_projeto_iniciado(destinatario: str, nome_destinatario: str, nome_projeto: str) -> bool:
    if not settings.resend_api_key:
        logger.info("RESEND_API_KEY não configurada: e-mail de projeto iniciado não enviado")
        return False

    resend.api_key = settings.resend_api_key
    nome = _linha_unica(nome_projeto)
    # O nome do projeto é digitado pelo usuário: escapa antes de pôr no HTML.
    nome_html, pessoa_html = html.escape(nome), html.escape(_linha_unica(nome_destinatario))

    try:
        resend.Emails.send({
            "from": settings.email_remetente,
            "to": [destinatario],
            "subject": f"O Projeto {nome} foi iniciado com sucesso",
            "html": (
                f"<p>Olá, {pessoa_html}.</p>"
                f"<p>O Projeto <strong>{nome_html}</strong> foi iniciado com sucesso, "
                "já com as 5 fases do Waterfall.</p>"
            ),
            "text": f"Olá, {nome_destinatario}.\n\nO Projeto {nome} foi iniciado com sucesso.",
        })
    except resend.exceptions.ResendError as exc:
        # Inclui falhas de rede: o SDK converte erros do requests em ResendError.
        logger.error("Resend recusou o e-mail (%s): %s", exc.code, exc.message)
        return False
    except Exception:
        logger.exception("Falha inesperada ao enviar e-mail pelo Resend")
        return False
    return True
