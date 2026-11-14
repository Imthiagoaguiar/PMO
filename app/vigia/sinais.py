"""Resume os eventos do log da última hora em sinais: erros agrupados, 5xx, lentidão, 429, avisos."""

import re
from collections import Counter, defaultdict
from statistics import quantiles
from typing import Any, Dict, List

LIMITE_LENTA_MS = 2000

_NUMEROS = re.compile(r"\d+")
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _molde(texto: str) -> str:
    """Mensagens que só mudam nos números/e-mails viram a mesma assinatura."""
    return _NUMEROS.sub("N", _EMAIL.sub("<email>", texto))[:200]


def _p95(valores: List[float]) -> float:
    if len(valores) < 2:
        return valores[0] if valores else 0.0
    # "inclusive": o padrão ("exclusive") extrapola além do maior valor com poucas amostras
    # (dava p95 de 12 s numa rota cujo máximo foi 7,7 s).
    return round(quantiles(valores, n=20, method="inclusive")[-1], 1)


def resumir(eventos: List[Dict[str, Any]]) -> Dict[str, Any]:
    acessos = [e for e in eventos if e.get("tipo") == "acesso"]
    por_classe: Counter = Counter()
    por_5xx: Counter = Counter()
    por_429: Counter = Counter()
    duracoes: Dict[str, List[float]] = defaultdict(list)
    for a in acessos:
        status, rota = int(a.get("status", 0)), f"{a.get('metodo')} {a.get('rota')}"
        por_classe[f"{status // 100}xx"] += 1
        if status >= 500:
            por_5xx[(rota, status)] += 1
        if status == 429:
            por_429[rota] += 1
        if isinstance(a.get("duracao_ms"), (int, float)):
            duracoes[rota].append(float(a["duracao_ms"]))

    excecoes: Dict[str, Dict[str, Any]] = {}
    avisos: Counter = Counter()
    limpezas = [
        {k: e.get(k) for k in ("ts", "livre_antes_gb", "livre_depois_gb", "liberado_gb", "itens")}
        for e in eventos if e.get("tipo") == "limpeza"
    ]
    for e in eventos:
        if e.get("tipo") in ("acesso", "limpeza"):
            continue
        nivel = e.get("nivel")
        if nivel in ("ERROR", "CRITICAL"):
            local = _ultima_linha_do_app(e.get("stack", ""))
            assinatura = f"{e.get('excecao') or nivel}|{_molde(str(e.get('msg', '')))}|{local}"
            item = excecoes.setdefault(assinatura, {
                "excecao": e.get("excecao"), "mensagem": _molde(str(e.get("msg", ""))), "logger": e.get("logger"),
                "local": local, "quantidade": 0, "primeira_vez": str(e.get("ts")), "exemplo_stack": (e.get("stack") or "")[-1500:],
            })
            item["quantidade"] += 1
            item["ultima_vez"] = str(e.get("ts"))
        elif nivel == "WARNING":
            avisos[f"{e.get('logger')}: {_molde(str(e.get('msg', '')))}"] += 1

    lentas = []
    for rota, valores in duracoes.items():
        p95 = _p95(valores)
        if p95 >= LIMITE_LENTA_MS or max(valores) >= 3 * LIMITE_LENTA_MS:
            lentas.append({"rota": rota, "requisicoes": len(valores), "p95_ms": p95, "max_ms": max(valores)})

    todas = [d for v in duracoes.values() for d in v]
    return {
        "requisicoes": len(acessos),
        "por_classe": dict(por_classe),
        "p95_geral_ms": _p95(todas) if todas else None,
        "respostas_5xx": [{"rota": r, "status": s, "quantidade": q} for (r, s), q in por_5xx.most_common(20)],
        "respostas_429": [{"rota": r, "quantidade": q} for r, q in por_429.most_common(10)],
        "erros": sorted(excecoes.values(), key=lambda x: -x["quantidade"])[:20],
        "avisos": [{"mensagem": m, "quantidade": q} for m, q in avisos.most_common(15)],
        "rotas_lentas": sorted(lentas, key=lambda x: -x["p95_ms"])[:10],
        "limpezas": limpezas[-5:],
    }


def _ultima_linha_do_app(stack: str) -> str:
    """O ponto do NOSSO código onde o erro aconteceu (ignora bibliotecas): 'app/routers/x.py:123'."""
    locais = re.findall(r'File "[^"]*?/(app/[^"]+)", line (\d+)', stack or "")
    return f"{locais[-1][0]}:{locais[-1][1]}" if locais else ""
