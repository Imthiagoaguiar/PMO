"""
IDOR (Insecure Direct Object Reference): token VÁLIDO, objeto de OUTRA pessoa.

O PMO é de uma empresa só (não há "tenant"): o isolamento é por PROJETO. Só enxerga um projeto
quem é membro dele (ou admin). Regras verificadas aqui:
- quem não é membro recebe 404 em TODA rota de /projetos/{id}/..., com a MESMA resposta de um
  projeto inexistente (não dá para descobrir quais ids existem);
- nada muda no banco depois da tentativa;
- ids de objetos filhos (tarefa, fase, comentário, membro) só valem dentro do projeto da URL;
- visualizador lê, mas recebe 403 em tudo que altera.
"""

import re
from typing import Dict, List, Tuple

import pytest
from sqlalchemy import select

from app.main import app
from app.models import Auditoria, Comentario, Fase, PapelUsuario, Pilar, Projeto, ProjetoMembro, Tarefa
from tests.conftest import SENHA

DONO, INTRUSO, LEITOR, ADMIN = "dono@a.com.br", "intruso@b.com.br", "leitor@a.com.br", "admin@a.com.br"
NAO_ENCONTRADO = {"detail": "Projeto não encontrado."}


def _rotas_de_projeto() -> List[Tuple[str, str]]:
    metodos = {"get", "post", "put", "patch", "delete"}
    return sorted(
        (m.upper(), caminho)
        for caminho, ops in app.openapi()["paths"].items()
        if caminho.startswith("/projetos/{projeto_id}")
        for m in ops if m in metodos
    )


CORPOS = {  # corpos válidos: o 404 tem de vir da AUTORIZAÇÃO, não de um 422 por corpo inválido
    ("POST", "/projetos/{projeto_id}/fases/{fase_id}/tarefas"): {"descricao": "invasão"},
    ("PATCH", "/projetos/{projeto_id}/tarefas/{tarefa_id}"): {"percentual_conclusao": 100},
    ("PATCH", "/projetos/{projeto_id}"): {"status": "Cancelado"},
    ("POST", "/projetos/{projeto_id}/membros"): {"usuario_id": 0, "papel": "editor"},
    ("PATCH", "/projetos/{projeto_id}/membros/{usuario_id}"): {"papel": "editor"},
    ("POST", "/projetos/{projeto_id}/comentarios"): {"texto": "invasão"},
    ("PATCH", "/projetos/{projeto_id}/comentarios/{comentario_id}"): {"texto": "invasão"},
    ("POST", "/projetos/{projeto_id}/pilares"): {"nome": "Invasão"},
    ("PATCH", "/projetos/{projeto_id}/pilares/{pilar_id}"): {"farol": "vermelho", "status_texto": "invadido"},
}


@pytest.fixture
def cenario(client, criar_usuario, login) -> Dict:
    """Projeto do DONO com fase, tarefa, comentário e um LEITOR (visualizador)."""
    ids = {
        "dono": criar_usuario(DONO, PapelUsuario.GERENTE).id,
        "intruso": criar_usuario(INTRUSO, PapelUsuario.GERENTE).id,  # gerente de outra área: token válido
        "leitor": criar_usuario(LEITOR).id,
        "admin": criar_usuario(ADMIN, PapelUsuario.ADMIN).id,
    }
    h = {nome: login(email) for nome, email in
         [("dono", DONO), ("intruso", INTRUSO), ("leitor", LEITOR), ("admin", ADMIN)]}
    projeto = client.post("/projetos/manual", json={"nome_projeto": "Fusão confidencial"}, headers=h["dono"]).json()
    pid, fase_id = projeto["id"], projeto["fases"][0]["id"]
    tarefa = client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas", json={"descricao": "Due diligence"},
                         headers=h["dono"]).json()
    comentario = client.post(f"/projetos/{pid}/comentarios", json={"texto": "valor: R$ 80 mi"},
                             headers=h["dono"]).json()
    client.post(f"/projetos/{pid}/membros", json={"usuario_id": ids["leitor"]}, headers=h["dono"])
    return {"h": h, "ids": ids, "projeto_id": pid, "fase_id": fase_id, "tarefa_id": tarefa["id"],
            "comentario_id": comentario["id"], "pilar_id": projeto["pilares"][0]["id"]}


def _url(caminho: str, c: Dict, projeto_id: int) -> str:
    valores = {"projeto_id": projeto_id, "fase_id": c["fase_id"], "tarefa_id": c["tarefa_id"],
               "comentario_id": c["comentario_id"], "usuario_id": c["ids"]["leitor"], "pilar_id": c["pilar_id"]}
    return re.sub(r"\{(\w+)\}", lambda m: str(valores[m.group(1)]), caminho)


def _foto_do_banco(db) -> tuple:
    """Estado relevante do banco, para provar que a tentativa não mudou nada."""
    db.expire_all()
    return (
        [(p.id, p.nome_projeto, p.status) for p in db.scalars(select(Projeto))],
        [(t.id, t.descricao, t.percentual_conclusao, t.fase_id) for t in db.scalars(select(Tarefa))],
        [(c.id, c.texto) for c in db.scalars(select(Comentario))],
        sorted((m.projeto_id, m.usuario_id, m.papel) for m in db.scalars(select(ProjetoMembro))),
        db.query(Fase).count(),
        sorted((x.id, x.nome, x.farol, x.status_texto) for x in db.scalars(select(Pilar))),
    )


def _corpo(metodo, caminho, c):
    corpo = CORPOS.get((metodo, caminho))
    if corpo and "usuario_id" in corpo:
        corpo = {**corpo, "usuario_id": c["ids"]["intruso"]}  # tenta se adicionar ao projeto
    return corpo


def test_inventario_cobre_as_rotas_de_projeto():
    assert len(_rotas_de_projeto()) >= 15


@pytest.mark.parametrize("metodo, caminho", _rotas_de_projeto())
def test_quem_nao_e_membro_recebe_404_e_nada_muda(client, cenario, db, metodo, caminho):
    c = cenario
    antes = _foto_do_banco(db)
    r = client.request(metodo, _url(caminho, c, c["projeto_id"]), json=_corpo(metodo, caminho, c),
                       headers=c["h"]["intruso"])
    assert r.status_code == 404, f"{metodo} {caminho}: {r.status_code} {r.text[:200]}"
    assert r.json() == NAO_ENCONTRADO
    assert "Fusão" not in r.text and "80 mi" not in r.text
    assert _foto_do_banco(db) == antes


@pytest.mark.parametrize("metodo, caminho", _rotas_de_projeto())
def test_projeto_alheio_e_inexistente_sao_indistinguiveis(client, cenario, metodo, caminho):
    """Mesmo status e mesmo corpo: o intruso não consegue enumerar quais projetos existem."""
    c = cenario
    alheio = client.request(metodo, _url(caminho, c, c["projeto_id"]), json=_corpo(metodo, caminho, c),
                            headers=c["h"]["intruso"])
    inexistente = client.request(metodo, _url(caminho, c, 999_999), json=_corpo(metodo, caminho, c),
                                 headers=c["h"]["intruso"])
    assert (alheio.status_code, alheio.json()) == (inexistente.status_code, inexistente.json())


def test_listagens_nao_vazam_projeto_alheio(client, cenario):
    h = cenario["h"]["intruso"]
    assert client.get("/projetos", headers=h).json()["total"] == 0
    assert client.get("/projetos?busca=Fusão", headers=h).json()["total"] == 0
    assert client.get("/tarefas/minhas?pendentes=false", headers=h).json()["total"] == 0


def test_auditoria_global_so_admin(client, cenario):
    assert client.get("/auditoria", headers=cenario["h"]["intruso"]).status_code == 403
    assert client.get("/auditoria", headers=cenario["h"]["leitor"]).status_code == 403
    assert client.get("/auditoria", headers=cenario["h"]["admin"]).status_code == 200


# ---------- Objetos filhos de OUTRO projeto ----------


@pytest.fixture
def projeto_do_intruso(client, cenario) -> Dict:
    p = client.post("/projetos/manual", json={"nome_projeto": "Projeto do intruso"}, headers=cenario["h"]["intruso"]).json()
    return {"id": p["id"], "fase_id": p["fases"][0]["id"]}


def test_tarefa_alheia_pela_url_do_proprio_projeto(client, cenario, projeto_do_intruso, db):
    """O intruso é dono do PRÓPRIO projeto e tenta usar ids do projeto do outro na URL dele."""
    c, meu, h = cenario, projeto_do_intruso, cenario["h"]["intruso"]
    antes = _foto_do_banco(db)
    tarefa = f"/projetos/{meu['id']}/tarefas/{c['tarefa_id']}"
    assert client.patch(tarefa, json={"descricao": "roubada"}, headers=h).status_code == 404
    assert client.delete(tarefa, headers=h).status_code == 404
    # Criar tarefa numa fase de outro projeto.
    r = client.post(f"/projetos/{meu['id']}/fases/{c['fase_id']}/tarefas", json={"descricao": "x"}, headers=h)
    assert r.status_code == 404
    # Comentário de outro projeto.
    comentario = f"/projetos/{meu['id']}/comentarios/{c['comentario_id']}"
    assert client.patch(comentario, json={"texto": "x"}, headers=h).status_code == 404
    assert client.delete(comentario, headers=h).status_code == 404
    assert client.post(f"/projetos/{meu['id']}/comentarios", json={"texto": "x", "tarefa_id": c["tarefa_id"]},
                       headers=h).status_code == 404
    assert _foto_do_banco(db)[:3] == antes[:3]


def test_mover_tarefa_para_fase_de_outro_projeto(client, cenario, projeto_do_intruso, db):
    """O dono do projeto A não pode "despejar" uma tarefa dele numa fase do projeto B (nem o contrário)."""
    c = cenario
    url = f"/projetos/{c['projeto_id']}/tarefas/{c['tarefa_id']}"
    assert client.patch(url, json={"fase_id": projeto_do_intruso["fase_id"]}, headers=c["h"]["dono"]).status_code == 404
    db.expire_all()
    assert db.get(Tarefa, c["tarefa_id"]).fase_id == c["fase_id"]


def test_pilar_de_outro_projeto_na_tarefa(client, cenario, projeto_do_intruso):
    """O dono do projeto do intruso tenta pendurar uma tarefa dele num pilar do projeto alheio."""
    c, meu = cenario, projeto_do_intruso
    tarefa = client.post(f"/projetos/{meu['id']}/fases/{meu['fase_id']}/tarefas",
                         json={"descricao": "x", "pilar_id": c["pilar_id"]}, headers=c["h"]["intruso"])
    assert tarefa.status_code == 422


def test_responsavel_de_fora_do_projeto(client, cenario):
    """Atribuir tarefa a quem não é membro daria a essa pessoa a tarefa em /tarefas/minhas."""
    c = cenario
    url = f"/projetos/{c['projeto_id']}/tarefas/{c['tarefa_id']}"
    r = client.patch(url, json={"responsavel_id": c["ids"]["intruso"]}, headers=c["h"]["dono"])
    assert r.status_code == 422
    assert client.get("/tarefas/minhas", headers=c["h"]["intruso"]).json()["total"] == 0


def test_membro_alheio_pela_url(client, cenario, projeto_do_intruso):
    c, h = cenario, cenario["h"]["intruso"]
    url = f"/projetos/{projeto_do_intruso['id']}/membros/{c['ids']['leitor']}"
    assert client.patch(url, json={"papel": "editor"}, headers=h).status_code == 404
    assert client.delete(url, headers=h).status_code == 404


# ---------- Visualizador ----------

ESCRITAS = [(m, p) for m, p in _rotas_de_projeto() if m != "GET" and "/comentarios" not in p]


@pytest.mark.parametrize("metodo, caminho", ESCRITAS)
def test_visualizador_nao_altera_nada(client, cenario, db, metodo, caminho):
    c = cenario
    antes = _foto_do_banco(db)
    corpo = CORPOS.get((metodo, caminho))
    if corpo and "usuario_id" in corpo:
        corpo = {**corpo, "usuario_id": c["ids"]["intruso"]}
    r = client.request(metodo, _url(caminho, c, c["projeto_id"]), json=corpo, headers=c["h"]["leitor"])
    assert r.status_code == 403, f"{metodo} {caminho}: {r.status_code} {r.text[:200]}"
    assert _foto_do_banco(db) == antes


def test_visualizador_nao_edita_nem_apaga_comentario_alheio(client, cenario):
    c = cenario
    url = f"/projetos/{c['projeto_id']}/comentarios/{c['comentario_id']}"
    assert client.patch(url, json={"texto": "x"}, headers=c["h"]["leitor"]).status_code == 403
    assert client.delete(url, headers=c["h"]["leitor"]).status_code == 403


def test_admin_nao_reescreve_comentario_alheio(client, cenario):
    c = cenario
    url = f"/projetos/{c['projeto_id']}/comentarios/{c['comentario_id']}"
    assert client.patch(url, json={"texto": "editado pelo admin"}, headers=c["h"]["admin"]).status_code == 403


# ---------- Usuários ----------


def test_nao_admin_nao_mexe_em_usuarios(client, cenario):
    h, alvo = cenario["h"]["intruso"], cenario["ids"]["dono"]
    assert client.get("/usuarios", headers=h).status_code == 403
    assert client.patch(f"/usuarios/{alvo}", json={"papel": "admin"}, headers=h).status_code == 403
    assert client.patch(f"/usuarios/{cenario['ids']['intruso']}", json={"papel": "admin"}, headers=h).status_code == 403
    for acao in ("redefinir-senha", "encerrar-sessoes", "redefinir-2fa"):
        corpo = {"nova_senha": SENHA + "x"} if acao == "redefinir-senha" else None
        assert client.post(f"/usuarios/{alvo}/{acao}", json=corpo, headers=h).status_code == 403


def test_tentativas_ficam_sem_rastro_de_dados_mas_o_banco_nao_muda(client, cenario, db):
    """Nenhuma tentativa negada vira registro de 'alteração' na auditoria do projeto."""
    c = cenario
    for metodo, caminho in _rotas_de_projeto():
        client.request(metodo, _url(caminho, c, c["projeto_id"]), json=_corpo(metodo, caminho, c),
                       headers=c["h"]["intruso"])
    db.expire_all()
    autores = {a.usuario_id for a in db.scalars(select(Auditoria).where(Auditoria.projeto_id == c["projeto_id"]))}
    assert c["ids"]["intruso"] not in autores
