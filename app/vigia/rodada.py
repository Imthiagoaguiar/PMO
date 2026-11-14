"""Uma rodada do vigia: coleta → regras → IA → grava o relatório. Uma sonda que falha não derruba a rodada."""

import logging
from datetime import datetime, timedelta
from typing import Any, Callable, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from app.config import settings
from app.models import GravidadeAchado, VigiaRodada
from app.security import agora_utc
from app.vigia import analista, regras, sinais, sondas
from app.vigia.coletor import ColetorDeLog

logger = logging.getLogger("pmo.vigia")


def _seguro(db: Session, nome: str, funcao: Callable[[], Any], padrao: Any) -> Any:
    try:
        return funcao()
    except Exception as exc:  # o vigia não pode cair por causa de uma sonda
        logger.exception("Vigia: a sonda %s falhou", nome)
        # No Postgres, um erro aborta a transação: sem o rollback, as sondas seguintes e a gravação
        # do relatório falhariam todas ("current transaction is aborted").
        db.rollback()
        return {**padrao, "erro_da_sonda": type(exc).__name__} if isinstance(padrao, dict) else padrao


def executar(db: Session, coletor: Union[ColetorDeLog, List[ColetorDeLog], None], janela_inicio: datetime,
             agora: Optional[datetime] = None) -> VigiaRodada:
    """`coletor`: um ou vários (log da API + log do worker da fila)."""
    iniciada = agora or agora_utc()
    coletores = coletor if isinstance(coletor, list) else ([coletor] if coletor else [])
    eventos = [e for c in coletores for e in c.retirar(iniciada)]
    coletados: Dict[str, Any] = {
        "janela_minutos": round((iniciada - janela_inicio).total_seconds() / 60),
        "log": sinais.resumir(eventos) if coletores else {**sinais.resumir([]), "indisponivel": "LOG_ARQUIVO não configurado"},
        "coletor": {"eventos": len(eventos), "arquivos": [str(c.arquivo) for c in coletores],
                    "linhas_ilegiveis": sum(c.linhas_ilegiveis for c in coletores),
                    "descartados": sum(c.descartados for c in coletores)},
        "saude": _seguro(db, "saude", lambda: sondas.saude_api(settings.vigia_api_url), {"ok": False}),
        "migracao": _seguro(db, "migracao", lambda: sondas.migracao(db), {"em_dia": None}),
        "integridade": _seguro(db, "integridade", lambda: sondas.integridade(db), []),
        "seguranca": _seguro(db, "seguranca", lambda: sondas.seguranca(db, janela_inicio), {"eventos": {}, "ips_distintos_com_falha": 0}),
        "higiene": _seguro(db, "higiene", lambda: sondas.higiene(db, iniciada), {"refresh_tokens_para_limpar": 0, "admins_sem_2fa": 0}),
        "fila": _seguro(db, "fila", lambda: sondas.fila_de_importacao(db, iniciada, janela_inicio),
                        {"redis_ok": None, "paradas_na_fila": 0, "travadas_processando": 0, "falhas_internas_na_janela": 0}),
        "recursos": _seguro(db, "recursos", lambda: sondas.recursos(settings.log_arquivo), {"disco_livre_percentual": 100.0}),
    }
    for c in coletores:  # zera os contadores: cada rodada mostra o que aconteceu nela
        c.linhas_ilegiveis = c.descartados = 0

    achados = regras.avaliar(coletados)
    resposta, situacao_ia = analista.analisar(coletados, achados)
    if resposta:
        achados += [{**a.model_dump(), "origem": "ia"} for a in resposta.achados]
    resumo = resposta.resumo if resposta else _resumo_sem_ia(coletados, achados)

    rodada = VigiaRodada(
        iniciada_em=iniciada, concluida_em=agora_utc(), janela_inicio=janela_inicio,
        pior_gravidade=GravidadeAchado(pior) if (pior := regras.pior(achados)) else None,
        resumo=resumo, achados=achados,
        sinais=analista.anonimizar(coletados), ia_modelo=settings.groq_modelo if resposta else None,
        ia_situacao=situacao_ia,
    )
    db.add(rodada)
    db.commit()
    logger.info("Vigia: rodada %s concluída (%s achado(s), pior: %s)", rodada.id, len(achados),
                rodada.pior_gravidade.value if rodada.pior_gravidade else "nenhum")
    return rodada


def _resumo_sem_ia(s: Dict[str, Any], achados: list) -> str:
    log = s["log"]
    base = (f"{log['requisicoes']} requisições em {s['janela_minutos']} min"
            + (f", p95 {log['p95_geral_ms']} ms" if log.get("p95_geral_ms") is not None else "")
            + f", {sum(r['quantidade'] for r in log['respostas_5xx'])} erro(s) 5xx.")
    if not achados:
        return base + " Nenhum problema encontrado pelas regras."
    contagem = {g: sum(a["gravidade"] == g for a in achados) for g in regras.GRAVIDADES}
    partes = [f"{n} {regras.NOME_GRAVIDADE[g]}" for g, n in contagem.items() if n]
    return base + f" {len(achados)} achado(s): " + ", ".join(partes) + "."


def janela_padrao(agora: datetime) -> datetime:
    return agora - timedelta(minutes=settings.vigia_intervalo_minutos)
