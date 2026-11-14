"""
Regras fixas: transformam os sinais em achados com gravidade. Funcionam sem IA (a IA só aprofunda).
Cada achado: codigo, titulo, gravidade, evidencias e como_investigar. Nunca há correção automática.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from app.config import settings

GRAVIDADES = ("critica", "alta", "media", "baixa")
NOME_GRAVIDADE = {"critica": "crítica", "alta": "alta", "media": "média", "baixa": "baixa"}
MUITAS_FALHAS_DE_LOGIN = 50
MUITOS_429 = 100
DISCO_CRITICO_PERCENTUAL = 5
DISCO_BAIXO_PERCENTUAL = 15
DISCO_CRITICO_GB = 2
DISCO_BAIXO_GB = 10


def _hora_local(instante: Optional[str]) -> str:
    """'2026-11-14T00:39:00+00:00' -> '13/11/2026 21:39:00' (no fuso de quem lê o relatório)."""
    try:
        return datetime.fromisoformat(str(instante)).astimezone(ZoneInfo(settings.fuso_horario)).strftime("%d/%m/%Y %H:%M:%S")
    except ValueError:
        return str(instante)


def _achado(codigo: str, titulo: str, gravidade: str, evidencias: List[str], como_investigar: List[str]) -> Dict[str, Any]:
    return {"codigo": codigo, "titulo": titulo, "gravidade": gravidade, "evidencias": evidencias,
            "como_investigar": como_investigar, "origem": "regra"}


def avaliar(s: Dict[str, Any]) -> List[Dict[str, Any]]:
    achados: List[Dict[str, Any]] = []
    log, saude, migracao = s["log"], s["saude"], s["migracao"]

    if not saude.get("ok"):
        achados.append(_achado("api_fora_do_ar", "A API não respondeu ao health check", "critica",
                               [f"GET /saude: status {saude.get('status')} {saude.get('erro', '')}".strip(),
                                f"banco: {saude.get('corpo', {}).get('banco', 'desconhecido')}"],
                               ["Veja se o processo do uvicorn está de pé", "Teste a conexão com o Postgres (DATABASE_URL)"]))

    if migracao.get("em_dia") is False:
        achados.append(_achado("migracao_pendente", "O banco não está na migração mais nova do código", "alta",
                               [f"banco em {migracao.get('banco')}, código em {migracao.get('codigo')}"],
                               ["Rode `alembic upgrade head` (colunas novas faltando causam erro 500)"]))

    for erro in log["erros"]:
        achados.append(_achado(
            "erro_no_log", f"{erro.get('excecao') or 'Erro'}: {erro['mensagem'][:90]}", "alta",
            [f"{erro['quantidade']} ocorrência(s) entre {_hora_local(erro['primeira_vez'])} e {_hora_local(erro.get('ultima_vez'))}",
             f"local: {erro['local'] or 'fora do código do app'} (logger {erro['logger']})"],
            ["Leia o stack trace de exemplo nos sinais", "Reproduza a requisição da mesma rota em ambiente de teste"],
        ))

    for r in log["respostas_5xx"]:
        achados.append(_achado("respostas_5xx", f"{r['quantidade']}x {r['status']} em {r['rota']}", "alta",
                               [f"{r['quantidade']} resposta(s) {r['status']} na última janela"],
                               ["Procure no log os erros da mesma rota e horário"]))

    for r in log["rotas_lentas"]:
        achados.append(_achado("rota_lenta", f"Rota lenta: {r['rota']}", "media",
                               [f"p95 {r['p95_ms']} ms, máximo {r['max_ms']} ms em {r['requisicoes']} requisições"],
                               ["Conte as queries da rota (N+1?)", "Veja se depende de API externa (Brasil API, Groq)"]))

    total_429 = sum(r["quantidade"] for r in log["respostas_429"])
    if total_429 >= MUITOS_429:
        achados.append(_achado("muitos_429", f"{total_429} requisições barradas por limite (429)", "media",
                               [f"{r['rota']}: {r['quantidade']}" for r in log["respostas_429"][:5]],
                               ["Ataque/robô ou limite apertado demais para o uso real?"]))

    for aviso in log["avisos"]:
        if "Brasil API" in aviso["mensagem"] or "Groq" in aviso["mensagem"] or "JWKS" in aviso["mensagem"]:
            achados.append(_achado("integracao_instavel", f"Integração externa com falhas: {aviso['mensagem'][:90]}", "media",
                                   [f"{aviso['quantidade']} aviso(s) na janela"],
                                   ["Verifique o status do serviço externo e a chave configurada"]))

    for problema in s["integridade"]:
        achados.append(_achado(
            f"integridade_{problema['codigo']}", f"Dado inconsistente: {problema['descricao']}", "critica" if "erro" not in problema else "media",
            [f"{problema.get('quantidade', '?')} registro(s); exemplos de id: {problema.get('exemplos', [])}"]
            if "erro" not in problema else [f"a verificação falhou: {problema['erro']}"],
            ["Descubra se foi alteração manual no banco ou um caminho da API sem validação"],
        ))

    copia = s.get("backup") or {}
    if copia.get("configurado"):
        horas = copia.get("horas_desde_o_ultimo")
        if horas is None:
            achados.append(_achado("sem_backup", "Nenhum backup do banco encontrado", "alta",
                                   ["sem backup, um disco que falhe perde todos os projetos"],
                                   ["Rode `python -m app.backup` e agende (scripts/instalar-backup.sh)"]))
        elif horas > settings.backup_max_horas:
            achados.append(_achado("backup_atrasado", f"Último backup tem {horas:.0f} h", "alta",
                                   [f"arquivo: {copia.get('ultimo')}", f"o limite é {settings.backup_max_horas} h"],
                                   ["O agendamento parou? Veja logs/backup.log", "Rode `python -m app.backup` agora"]))
        if not copia.get("copia_externa"):
            achados.append(_achado("backup_so_local", "Os backups ficam só no mesmo disco do banco", "baixa",
                                   ["se o disco morrer, o banco e os backups se perdem juntos"],
                                   ["Configure BACKUP_COPIA_DIR com outro disco ou uma pasta sincronizada com a nuvem"]))

    fila = s.get("fila") or {}
    if fila.get("redis_ok") is False:
        achados.append(_achado("fila_fora_do_ar", "Redis inacessível: a importação de planilhas não funciona", "alta",
                               [f"erro ao conectar: {fila.get('erro', 'desconhecido')}"],
                               ["Veja se o Redis está rodando (REDIS_URL)", "Uploads novos estão recebendo 503"]))
    if fila.get("paradas_na_fila"):
        sem_worker = fila.get("workers") == 0
        achados.append(_achado(
            "importacoes_paradas", f"{fila['paradas_na_fila']} importação(ões) esperando na fila há mais de 10 min",
            "alta" if sem_worker else "media",
            [f"workers ativos: {fila.get('workers')}", f"jobs no Redis: {fila.get('na_fila_no_redis')}"],
            ["Nenhum worker: rode `python -m app.trabalhador`" if sem_worker else "Fila maior que a capacidade dos workers?",
             "Se o Redis foi reiniciado, os jobs se perderam: peça para reenviarem a planilha"],
        ))
    if fila.get("travadas_processando"):
        achados.append(_achado("importacoes_travadas", f"{fila['travadas_processando']} importação(ões) presas em \"processando\"",
                               "alta", ["começaram há mais de 35 min (o limite do job é 30 min)"],
                               ["O worker caiu no meio? Veja logs/trabalhador.log"]))
    if fila.get("falhas_internas_na_janela"):
        achados.append(_achado("importacoes_com_erro_interno",
                               f"{fila['falhas_internas_na_janela']} importação(ões) falharam por erro interno", "alta",
                               ["não foi a planilha: foi exceção no worker"], ["Leia o stack trace em logs/trabalhador.log"]))

    eventos = s["seguranca"]["eventos"]
    if eventos.get("auth.reuso_refresh_token"):
        achados.append(_achado("reuso_de_token", "Refresh token reutilizado (possível roubo de sessão)", "alta",
                               [f"{eventos['auth.reuso_refresh_token']} alarme(s) na janela"],
                               ["Veja na Auditoria quais usuários e IPs", "Considere encerrar as sessões desses usuários"]))
    falhas = eventos.get("auth.login_falhou", 0)
    if falhas >= MUITAS_FALHAS_DE_LOGIN:
        achados.append(_achado("muitas_falhas_de_login", f"{falhas} logins com senha errada na janela", "media",
                               [f"de {s['seguranca']['ips_distintos_com_falha']} IP(s) distintos",
                                f"{eventos.get('auth.conta_bloqueada', 0)} conta(s) bloqueadas"],
                               ["Muitos IPs: ataque distribuído; poucos IPs: robô ou script mal configurado"]))

    higiene = s["higiene"]
    if higiene["admins_sem_2fa"]:
        achados.append(_achado("admin_sem_2fa", f"{higiene['admins_sem_2fa']} admin(s) sem verificação em duas etapas", "media",
                               ["o 2FA é obrigatório para admin (EXIGIR_2FA_ADMIN)"], ["Peça para ativarem no próximo login"]))
    if higiene["refresh_tokens_para_limpar"] > 10_000:
        achados.append(_achado("tokens_acumulados", "Muitos refresh tokens vencidos acumulados", "baixa",
                               [f"{higiene['refresh_tokens_para_limpar']} registros"],
                               ["Agende `python -m app.cli limpar-tokens` diariamente"]))

    # Porcentagem E espaço absoluto: 3% de um disco de 2 TB ainda são 60 GB.
    percentual = s["recursos"]["disco_livre_percentual"]
    gb = s["recursos"].get("disco_livre_gb", float("inf"))
    if gb < DISCO_CRITICO_GB or (percentual < DISCO_CRITICO_PERCENTUAL and gb < DISCO_BAIXO_GB / 2):
        gravidade_disco = "critica"
    elif percentual < DISCO_BAIXO_PERCENTUAL or gb < DISCO_BAIXO_GB:
        gravidade_disco = "media"
    else:
        gravidade_disco = None
    if gravidade_disco:
        achados.append(_achado("disco_baixo", f"Disco com {gb} GB livres ({percentual}%)", gravidade_disco,
                               [f"{gb} GB livres, {percentual}% do disco"],
                               ["Sem espaço, o Postgres e o log param de gravar: limpe logs antigos e backups"]))

    for limpeza in log.get("limpezas", []):
        if limpeza.get("itens"):
            achados.append(_achado(
                "limpeza_automatica", f"Disco quase cheio: a limpeza automática liberou {limpeza.get('liberado_gb')} GB", "baixa",
                [f"livre: {limpeza.get('livre_antes_gb')} GB antes, {limpeza.get('livre_depois_gb')} GB depois",
                 "apagou: " + "; ".join(limpeza.get("itens") or [])[:300]],
                ["Se o disco continua baixo, a limpeza não alcança o que encheu: veja Lixeira, Downloads e backups"],
            ))

    if s.get("coletor", {}).get("descartados"):
        achados.append(_achado("log_transbordou", "O vigia descartou eventos do log por excesso de volume", "media",
                               [f"{s['coletor']['descartados']} evento(s) descartados"],
                               ["Pico anormal de erros/requisições na janela: veja o log bruto"]))
    return achados


def pior(achados: List[Dict[str, Any]]):
    for gravidade in GRAVIDADES:
        if any(a["gravidade"] == gravidade for a in achados):
            return gravidade
    return None
