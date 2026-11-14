"""
Fuzzing de TODAS as rotas (lidas do OpenAPI): IDs absurdos, tipos errados, textos enormes,
datas impossíveis, paginação gigante. Regra única: nenhuma resposta pode ser 5xx.

Um 500 aqui é sempre bug: a entrada é inválida e deveria virar 404/409/422, nunca um erro interno.
Roda como admin (enxerga todos os projetos), com um projeto real para os IDs válidos existirem.
"""

import re
from typing import Any, Dict, Iterator, List, Tuple

import pytest

from app.main import app
from app.models import PapelUsuario

IDS_ABSURDOS = [0, -1, 2**31, 2**63, 10**30]
VALORES_ABSURDOS: List[Any] = [
    None, "", " ", "x" * 20_000, -1, 0, 2**31, 2**63, 10**30, 1.5, True, [], {}, [1, 2], {"a": 1},
    "\u202e\u200b", "2026-02-30", "9999-12-31", "0000-00-00", "NaN", "Infinity", "-0", "1e999",
    "<script>alert(1)</script>", "' OR 1=1 --",
]
QUERIES_ABSURDAS = ["limite=0", "limite=-1", "limite=99999999999999999999", "offset=-1",
                    "offset=99999999999999999999", "offset=9223372036854775808", "busca=" + "x" * 500,
                    "status=Inexistente", "pendentes=talvez", "tarefa_id=99999999999999999999",
                    "somente_projeto=1", "papel=root", "ativo=sim"]
METODOS = {"get", "post", "put", "patch", "delete"}
ROTAS_PULADAS = {("POST", "/auth/logout")}  # derruba a sessão do próprio fuzzer


def _rotas() -> List[Tuple[str, str, Dict]]:
    especificacao = app.openapi()
    return [
        (m.upper(), caminho, operacao)
        for caminho, operacoes in especificacao["paths"].items()
        for m, operacao in operacoes.items()
        if m in METODOS and (m.upper(), caminho) not in ROTAS_PULADAS
    ]


def _resolver(esquema: Dict, componentes: Dict) -> Dict:
    while "$ref" in esquema:
        esquema = componentes[esquema["$ref"].rsplit("/", 1)[-1]]
    return esquema


def _campos_do_corpo(operacao: Dict) -> Tuple[str, List[str]]:
    corpo = operacao.get("requestBody", {}).get("content", {})
    componentes = app.openapi()["components"]["schemas"]
    for tipo in ("application/json", "application/x-www-form-urlencoded"):
        if tipo in corpo:
            esquema = _resolver(corpo[tipo]["schema"], componentes)
            return tipo, list(esquema.get("properties", {}))
    return "", []


@pytest.fixture
def alvo(client, criar_usuario, login) -> Dict:
    criar_usuario("admin@empresa.com.br", PapelUsuario.ADMIN)
    membro = criar_usuario("membro@empresa.com.br")
    h = login("admin@empresa.com.br")
    p = client.post("/projetos/manual", json={"nome_projeto": "Alvo"}, headers=h).json()
    tarefa = client.post(f"/projetos/{p['id']}/fases/{p['fases'][0]['id']}/tarefas", json={"descricao": "T"}, headers=h).json()
    comentario = client.post(f"/projetos/{p['id']}/comentarios", json={"texto": "c"}, headers=h).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": membro.id}, headers=h)
    ids = {"projeto_id": p["id"], "fase_id": p["fases"][0]["id"], "tarefa_id": tarefa["id"],
           "comentario_id": comentario["id"], "usuario_id": membro.id, "pilar_id": p["pilares"][0]["id"],
           "rodada_id": 1, "importacao_id": 1}
    return {"h": h, "ids": ids}


def _url(caminho: str, ids: Dict, trocar: str = "", valor: Any = None) -> str:
    return re.sub(r"\{(\w+)\}", lambda m: str(valor if m.group(1) == trocar else ids[m.group(1)]), caminho)


def _casos(alvo: Dict) -> Iterator[Tuple[str, str, Dict]]:
    """(método, url, kwargs da requisição) para cada combinação absurda."""
    ids = alvo["ids"]
    for metodo, caminho, operacao in _rotas():
        for parametro in re.findall(r"\{(\w+)\}", caminho):
            for valor in IDS_ABSURDOS:
                yield metodo, _url(caminho, ids, parametro, valor), {}
        if metodo == "GET":
            for q in QUERIES_ABSURDAS:
                yield metodo, _url(caminho, ids) + "?" + q, {}
        tipo, campos = _campos_do_corpo(operacao)
        for campo in campos:
            for valor in VALORES_ABSURDOS:
                if tipo == "application/json":
                    yield metodo, _url(caminho, ids), {"json": {campo: valor}}
                elif valor is not None and not isinstance(valor, (list, dict)):
                    yield metodo, _url(caminho, ids), {"data": {campo: str(valor)}}
        if tipo == "application/json":
            for corpo in ([], "texto", 123, None, {"x": "y"}):
                yield metodo, _url(caminho, ids), {"json": corpo}


def test_nenhuma_entrada_absurda_vira_erro_500(alvo, monkeypatch):
    from fastapi.testclient import TestClient

    from app import rate_limit

    monkeypatch.setattr(rate_limit, "resetar_todos", rate_limit.resetar_todos)
    falhas = []
    with TestClient(app, raise_server_exceptions=False) as cliente:
        for i, (metodo, url, kwargs) in enumerate(_casos(alvo)):
            if i % 200 == 0:
                rate_limit.resetar_todos()  # os limites por minuto não podem esconder o resultado
            r = cliente.request(metodo, url, headers=alvo["h"], **kwargs)
            if r.status_code >= 500:
                falhas.append(f"{r.status_code} {metodo} {url[:120]} {str(kwargs)[:120]} -> {r.text[:120]}")
    assert not falhas, f"{len(falhas)} entradas viraram erro 5xx:\n" + "\n".join(sorted(set(falhas))[:60])
