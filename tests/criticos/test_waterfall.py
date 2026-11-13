"""
Cenário 4 — Lógica de negócio Waterfall (POST /projetos/manual).

Confere no BANCO (não só na resposta da API):
- cada projeto nasce com exatamente 5 fases, na ordem oficial, ligadas ao ID certo;
- as tarefas enviadas caem na fase certa do projeto certo;
- payload inválido ou erro no meio da gravação não deixa projeto/fase órfã (transação atômica);
- o banco garante sozinho o que a API promete (fase única por ordem, exclusão em cascata).
"""

from datetime import date

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import services
from app.main import app
from app.models import FASES_WATERFALL, Fase, PapelProjeto, Projeto, ProjetoMembro, Tarefa
from tests.criticos.conftest import SEGUNDA, contar

ORDEM_OFICIAL = ["Iniciação", "Planejamento", "Execução", "Monitoramento", "Encerramento"]
TAREFA = {"descricao": "TAP"}


@pytest.fixture(autouse=True)
def _sem_internet(brasil_api):
    """Todo teste deste arquivo usa a Brasil API simulada."""


def _criar(client, headers, **payload):
    return client.post("/projetos/manual", json={"nome_projeto": "ERP Financeiro", **payload}, headers=headers)


def _fases_no_banco(db, projeto_id):
    db.expire_all()
    return list(db.scalars(select(Fase).where(Fase.projeto_id == projeto_id).order_by(Fase.ordem_sequencia)))


# ---------- As 5 fases ----------


def test_constante_do_waterfall():
    assert list(FASES_WATERFALL) == ORDEM_OFICIAL


def test_projeto_nasce_com_as_5_fases_no_banco(client, gerente, db):
    r = _criar(client, gerente)
    assert r.status_code == 201, r.text
    projeto_id = r.json()["id"]

    fases = _fases_no_banco(db, projeto_id)
    assert [(f.ordem_sequencia, f.titulo) for f in fases] == list(enumerate(ORDEM_OFICIAL, start=1))
    assert all(f.projeto_id == projeto_id for f in fases)
    assert contar(db, Fase) == 5  # nenhuma fase solta fora do projeto
    assert [f["id"] for f in r.json()["fases"]] == [f.id for f in fases]  # resposta == banco


def test_cada_projeto_tem_as_suas_proprias_fases(client, gerente, db):
    ids = [_criar(client, gerente, nome_projeto=f"Projeto {i}").json()["id"] for i in range(3)]
    assert len(set(ids)) == 3
    vistos = set()
    for projeto_id in ids:
        fases = _fases_no_banco(db, projeto_id)
        assert [f.titulo for f in fases] == ORDEM_OFICIAL
        ids_fases = {f.id for f in fases}
        assert not ids_fases & vistos, "fase compartilhada entre projetos"
        vistos |= ids_fases
    assert contar(db, Fase) == 15


def test_dono_vira_editor_do_projeto(client, gerente, db):
    projeto = db.get(Projeto, _criar(client, gerente).json()["id"])
    membros = list(db.scalars(select(ProjetoMembro).where(ProjetoMembro.projeto_id == projeto.id)))
    assert [(m.usuario_id, m.papel) for m in membros] == [(projeto.dono_id, PapelProjeto.EDITOR)]


# ---------- Tarefas iniciais ----------


def test_tarefas_caem_na_fase_certa_do_projeto_certo(client, gerente, db):
    _criar(client, gerente, nome_projeto="Outro projeto")  # fases de outro projeto já existem no banco
    r = _criar(client, gerente, data_inicio=SEGUNDA.isoformat(), fases=[
        {"fase": "encerramento", "tarefas": [{"descricao": "Lições aprendidas", "duracao_dias_uteis": 1}]},
        {"fase": "EXECUCAO", "tarefas": [{"descricao": "Construir", "duracao_dias_uteis": 3}]},
        {"fase": "Iniciação", "tarefas": [{"descricao": "TAP", "duracao_dias_uteis": 2}, {"descricao": "Kickoff"}]},
    ])
    assert r.status_code == 201, r.text
    projeto_id = r.json()["id"]
    db.expire_all()
    tarefas = list(db.scalars(select(Tarefa).join(Fase).where(Fase.projeto_id == projeto_id)))
    assert {(t.fase.titulo, t.descricao) for t in tarefas} == {
        ("Iniciação", "TAP"), ("Iniciação", "Kickoff"), ("Execução", "Construir"), ("Encerramento", "Lições aprendidas"),
    }
    assert contar(db, Tarefa) == 4  # nada caiu no outro projeto

    # Datas encadeadas na ordem do Waterfall, mesmo com as fases fora de ordem no payload.
    datas = {t.descricao: (t.data_inicio, t.data_fim) for t in tarefas}
    assert datas["TAP"] == (date(2026, 3, 2), date(2026, 3, 3))
    assert datas["Kickoff"] == (None, None)  # sem duração: não empurra o cronograma
    assert datas["Construir"] == (date(2026, 3, 4), date(2026, 3, 6))
    assert datas["Lições aprendidas"] == (date(2026, 3, 9), date(2026, 3, 9))  # pula o fim de semana


def test_inicio_no_fim_de_semana_comeca_no_proximo_dia_util(client, gerente):
    r = _criar(client, gerente, data_inicio="2026-04-18", fases=[  # sábado; 21/04 é feriado
        {"fase": "Iniciação", "tarefas": [{"descricao": "TAP", "duracao_dias_uteis": 2}]},
    ])
    tarefa = r.json()["fases"][0]["tarefas"][0]
    assert (tarefa["data_inicio"], tarefa["data_fim"]) == ("2026-04-20", "2026-04-22")


# ---------- Payload inválido: nada é gravado ----------


@pytest.mark.parametrize("payload", [
    pytest.param({"nome_projeto": "ab"}, id="nome-curto"),
    pytest.param({"nome_projeto": "    "}, id="nome-so-espacos"),
    pytest.param({"nome_projeto": "x" * 201}, id="nome-longo"),
    pytest.param({"nome_projeto": None}, id="nome-nulo"),
    pytest.param({"orcamento_aprovado": -1}, id="orcamento-negativo"),
    pytest.param({"orcamento_aprovado": "1e20"}, id="orcamento-gigante"),
    pytest.param({"orcamento_aprovado": 10.123}, id="orcamento-3-casas"),
    pytest.param({"data_inicio": "2026-02-30"}, id="data-inexistente"),
    pytest.param({"data_inicio": "9999-12-31"}, id="data-absurda"),
    pytest.param({"fases": [{"fase": "Fase 6", "tarefas": [TAREFA]}]}, id="fase-inexistente"),
    pytest.param({"fases": [{"fase": "Iniciação", "tarefas": [TAREFA]}, {"fase": "iniciacao", "tarefas": [TAREFA]}]},
                 id="fase-repetida-com-outra-grafia"),
    pytest.param({"fases": [{"fase": f} for f in ORDEM_OFICIAL] + [{"fase": "Execução"}]}, id="6-fases"),
    pytest.param({"fases": [{"fase": "Iniciação", "tarefas": [{"descricao": "TAP", "duracao_dias_uteis": 2}]}]},
                 id="duracao-sem-data-inicio"),
    pytest.param({"data_inicio": "2026-03-02",
                  "fases": [{"fase": "Iniciação", "tarefas": [{"descricao": "TAP", "duracao_dias_uteis": 0}]}]},
                 id="duracao-zero"),
    pytest.param({"fases": [{"fase": "Iniciação", "tarefas": [{"descricao": "   "}]}]}, id="tarefa-sem-descricao"),
    pytest.param({"fases": [{"fase": f, "tarefas": [TAREFA] * 61} for f in ORDEM_OFICIAL]}, id="mais-de-300-tarefas"),
    pytest.param({"fases": [{"fase": "Iniciação", "tarefas": [TAREFA] * 101}]}, id="mais-de-100-por-fase"),
    pytest.param({"fases": "Iniciação"}, id="fases-nao-e-lista"),
])
def test_payload_invalido_nao_grava_nada(client, gerente, banco_vazio, payload):
    r = _criar(client, gerente, **payload)
    assert r.status_code == 422, r.text
    banco_vazio()


@pytest.mark.parametrize("nome", ["abc", "x" * 200, "Implantação ERP — Fase 2 🚀", "  ERP  "])
def test_nomes_no_limite_sao_aceitos(client, gerente, db, nome):
    r = _criar(client, gerente, nome_projeto=nome)
    assert r.status_code == 201, r.text
    assert db.get(Projeto, r.json()["id"]).nome_projeto == nome.strip()


@pytest.mark.parametrize("orcamento", [0, "999999999999.99"])
def test_orcamento_no_limite(client, gerente, orcamento):
    r = _criar(client, gerente, orcamento_aprovado=orcamento)
    assert r.status_code == 201, r.text
    assert float(r.json()["orcamento_aprovado"]) == float(orcamento)


def test_campos_extras_nao_sobrescrevem_dono_status_nem_id(client, gerente, criar_usuario, db):
    """Mass assignment: dono_id/status/id vindos do cliente são ignorados."""
    outro = criar_usuario("outro@empresa.com.br")
    r = _criar(client, gerente, dono_id=outro.id, status="Concluído", id=999)
    assert r.status_code == 201
    projeto = db.get(Projeto, r.json()["id"])
    assert projeto.dono_id != outro.id and projeto.id != 999 and projeto.status.value == "Planejado"


# ---------- Atomicidade ----------


def test_erro_no_meio_da_criacao_nao_deixa_projeto_orfao(gerente, banco_vazio, monkeypatch):
    """Se o cronograma falhar com o projeto e as fases já na sessão, nada é gravado."""

    def _explode(*_args, **_kwargs):
        raise RuntimeError("falha simulada no meio da criação")

    monkeypatch.setattr(services, "adicionar_cronograma", _explode)
    with TestClient(app, raise_server_exceptions=False) as cliente:
        r = cliente.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=gerente)
    assert r.status_code == 500
    assert "falha simulada" not in r.text  # sem detalhe interno para o cliente
    banco_vazio()


def test_erro_no_commit_vira_500_generico_e_nada_fica(client, gerente, banco_vazio, monkeypatch):
    original = Session.commit

    def _commit_falha(self):
        if any(isinstance(o, Projeto) for o in self.new) or any(isinstance(o, Projeto) for o in self.identity_map.values()):
            raise IntegrityError("INSERT", {}, Exception("disco cheio"))
        return original(self)

    monkeypatch.setattr(Session, "commit", _commit_falha)
    r = _criar(client, gerente)
    monkeypatch.setattr(Session, "commit", original)
    assert r.status_code == 500
    assert r.json() == {"detail": "Erro ao salvar. Tente novamente."}
    banco_vazio()


# ---------- Garantias do próprio banco ----------


def test_banco_recusa_duas_fases_com_a_mesma_ordem(client, gerente, db):
    projeto_id = _criar(client, gerente).json()["id"]
    db.add(Fase(projeto_id=projeto_id, titulo="Iniciação (cópia)", ordem_sequencia=1))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_banco_recusa_fase_de_projeto_inexistente(db):
    db.add(Fase(projeto_id=424242, titulo="Iniciação", ordem_sequencia=1))
    with pytest.raises(IntegrityError):  # chave estrangeira ligada também no SQLite
        db.commit()
    db.rollback()


def test_excluir_projeto_apaga_fases_e_tarefas_em_cascata(client, gerente, db):
    manter = _criar(client, gerente, nome_projeto="Fica").json()["id"]
    apagar = _criar(client, gerente, nome_projeto="Sai", fases=[
        {"fase": "Iniciação", "tarefas": [{"descricao": "TAP"}, {"descricao": "Kickoff"}]},
    ]).json()["id"]
    assert client.delete(f"/projetos/{apagar}", headers=gerente).status_code == 204

    assert _fases_no_banco(db, apagar) == []
    assert contar(db, Tarefa) == 0
    assert contar(db, ProjetoMembro, ProjetoMembro.projeto_id == apagar) == 0
    assert [f.titulo for f in _fases_no_banco(db, manter)] == ORDEM_OFICIAL  # o outro fica intacto


def test_exclusao_direta_no_banco_tambem_cascateia(client, gerente, db):
    """ON DELETE CASCADE no banco (não só no ORM): um DELETE feito fora da API não deixa órfãos."""
    projeto_id = _criar(client, gerente, fases=[{"fase": "Iniciação", "tarefas": [TAREFA]}]).json()["id"]
    db.execute(Projeto.__table__.delete().where(Projeto.id == projeto_id))
    db.commit()
    assert (contar(db, Fase), contar(db, Tarefa), contar(db, ProjetoMembro)) == (0, 0, 0)
