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


def _enviar(destinatario: str, assunto: str, corpo_html: str, corpo_texto: str, tipo: str) -> bool:
    if not settings.resend_api_key:
        logger.info("RESEND_API_KEY não configurada: e-mail de %s não enviado", tipo)
        return False
    resend.api_key = settings.resend_api_key
    try:
        resend.Emails.send({
            "from": settings.email_remetente,
            "to": [destinatario],
            "subject": _linha_unica(assunto),
            "html": corpo_html,
            "text": corpo_texto,
        })
    except resend.exceptions.ResendError as exc:
        # Inclui falhas de rede: o SDK converte erros do requests em ResendError.
        logger.error("Resend recusou o e-mail de %s (%s): %s", tipo, exc.code, exc.message)
        return False
    except Exception:
        logger.exception("Falha inesperada ao enviar e-mail de %s pelo Resend", tipo)
        return False
    return True


def enviar_projeto_iniciado(destinatario: str, nome_destinatario: str, nome_projeto: str) -> bool:
    nome = _linha_unica(nome_projeto)
    # O nome do projeto é digitado pelo usuário: escapa antes de pôr no HTML.
    nome_html, pessoa_html = html.escape(nome), html.escape(_linha_unica(nome_destinatario))
    return _enviar(
        destinatario,
        f"O Projeto {nome} foi iniciado com sucesso",
        f"<p>Olá, {pessoa_html}.</p>"
        f"<p>O Projeto <strong>{nome_html}</strong> foi iniciado com sucesso, "
        "já com as 5 fases do Waterfall.</p>",
        f"Olá, {nome_destinatario}.\n\nO Projeto {nome} foi iniciado com sucesso.",
        "projeto iniciado",
    )


def enviar_redefinicao_senha(destinatario: str, nome_destinatario: str, link: str) -> bool:
    """O link vale por pouco tempo e uma vez só; quem não pediu pode ignorar (a senha não muda)."""
    if not settings.resend_api_key and settings.ambiente == "dev":
        # Sem e-mail configurado, em desenvolvimento, o link sai no log para dar para testar.
        # Em produção, NUNCA: o log não pode virar um jeito de entrar na conta de alguém.
        logger.warning("RESEND_API_KEY não configurada (dev): link de redefinição de senha: %s", link)
        return False
    minutos = settings.redefinicao_senha_minutos
    pessoa_html, link_html = html.escape(_linha_unica(nome_destinatario)), html.escape(link, quote=True)
    return _enviar(
        destinatario,
        "Redefinição de senha do OMPY",
        f"<p>Olá, {pessoa_html}.</p>"
        "<p>Recebemos um pedido para redefinir a sua senha do OMPY.</p>"
        f'<p><a href="{link_html}">Criar uma nova senha</a></p>'
        f"<p>O link vale por {minutos} minutos e só pode ser usado uma vez. "
        "Se você não pediu, ignore este e-mail: a sua senha continua a mesma.</p>",
        f"Olá, {nome_destinatario}.\n\nRecebemos um pedido para redefinir a sua senha do OMPY.\n"
        f"Crie uma nova senha em: {link}\n\nO link vale por {minutos} minutos e só pode ser usado uma vez. "
        "Se você não pediu, ignore este e-mail: a sua senha continua a mesma.",
        "redefinição de senha",
    )


def enviar_lembrete_atrasadas(destinatario: str, nome_destinatario: str, tarefas: list) -> bool:
    """
    Um e-mail com TODAS as tarefas atrasadas da pessoa (lista de dicts: tarefa, projeto, prazo, dias,
    percentual). Textos digitados por usuários (tarefa, projeto) vão escapados no HTML.
    """
    from app.config import settings as _s

    quantidade = len(tarefas)
    link = f"{_s.url_do_front}/minhas-tarefas"
    pessoa = _linha_unica(nome_destinatario)
    linhas_html = "".join(
        f"<tr><td>{html.escape(_linha_unica(t['tarefa']))}</td><td>{html.escape(_linha_unica(t['projeto']))}</td>"
        f"<td>{t['prazo']:%d/%m/%Y}</td><td style='color:#b91c1c'>{t['dias']} dia(s)</td><td>{t['percentual']}%</td></tr>"
        for t in tarefas[:50]
    )
    linhas_texto = "\n".join(
        f"- {_linha_unica(t['tarefa'])} ({_linha_unica(t['projeto'])}): prazo {t['prazo']:%d/%m/%Y}, "
        f"{t['dias']} dia(s) de atraso, {t['percentual']}%"
        for t in tarefas[:50]
    )
    mais = f"<p>…e mais {quantidade - 50} tarefa(s).</p>" if quantidade > 50 else ""
    rodape = ("Você recebe este lembrete a cada 4 horas em dias úteis enquanto houver tarefas atrasadas. "
              "Para pausar por um dia: no OMPY, menu do seu nome → Silenciar lembretes.")
    return _enviar(
        destinatario,
        f"OMPY: você tem {quantidade} tarefa(s) atrasada(s)",
        f"<p>Olá, {html.escape(pessoa)}.</p><p>Estas tarefas sob sua responsabilidade passaram do prazo:</p>"
        "<table cellpadding='6' style='border-collapse:collapse'><tr><th align='left'>Tarefa</th>"
        "<th align='left'>Projeto</th><th align='left'>Prazo</th><th align='left'>Atraso</th><th align='left'>Feito</th></tr>"
        f"{linhas_html}</table>{mais}"
        f'<p><a href="{html.escape(link, quote=True)}">Abrir minhas tarefas</a></p><p style="color:#64748b">{rodape}</p>',
        f"Olá, {pessoa}.\n\nEstas tarefas sob sua responsabilidade passaram do prazo:\n{linhas_texto}\n\n"
        f"Abrir minhas tarefas: {link}\n\n{rodape}",
        "lembrete de tarefas atrasadas",
    )
