"""
Fuso horário e virada do dia.

Desenho (e por que NÃO convertemos as datas do cronograma para UTC):
- Datas do cronograma (início/fim de tarefa, início do projeto) são DATAS CIVIS: "dia 15" é dia 15
  em São Paulo, sem hora. Converter para UTC é justamente o que causa o bug clássico: 22h do dia 14
  em Brasília é 01h do dia 15 em UTC. Por isso a API só aceita "AAAA-MM-DD" (sem hora, sem fuso)
  e grava em coluna DATE.
- Instantes (quando um comentário foi feito, a auditoria) são gravados em UTC e devolvidos COM o
  fuso (+00:00), para o navegador converter para a hora local.
- "Hoje" (o que define tarefa atrasada) é o dia em São Paulo, não o do relógio do servidor,
  que na nuvem costuma estar em UTC.
"""

from datetime import date, datetime, timezone

import pytest

from app import tempo
from app.models import Tarefa
from tests.criticos.conftest import contar  # noqa: F401  (mantém o padrão dos outros arquivos)
from tests.conftest import postar_importacao

UTC = timezone.utc


def _projeto(client, h):
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP"}, headers=h).json()
    return p["id"], p["fases"][0]["id"]


def _relogio(monkeypatch, instante_utc: datetime):
    monkeypatch.setattr(tempo, "agora_utc", lambda: instante_utc)


# ---------- Entrada: só datas civis ----------


DATAS_COM_HORA_OU_FUSO = [
    "2026-03-15T01:00:00-03:00",   # 01h do dia 15 em Brasília
    "2026-03-14T23:30:00-03:00",   # 23h30 do dia 14 em Brasília = 02h30 do dia 15 em UTC
    "2026-03-15T02:00:00Z",
    "2026-03-15T00:00:00Z",         # meia-noite UTC = 21h do dia 14 em Brasília
    "2026-03-15T00:00:00-03:00",
    "2026-03-15T00:00:00",
    "2026-03-15 10:00",
    1773532800,                     # timestamp Unix: o "dia" depende do fuso de quem converte
    "15/03/2026",                   # formato brasileiro só na planilha; na API, ISO
]


@pytest.mark.parametrize("valor", DATAS_COM_HORA_OU_FUSO)
def test_api_so_aceita_data_sem_hora_nem_fuso(client, gerente, brasil_api, valor):
    pid, fase_id = _projeto(client, gerente)
    respostas = [
        client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas", json={"descricao": "T", "data_inicio": valor}, headers=gerente),
        client.post("/projetos/manual", json={"nome_projeto": "Outro", "data_inicio": valor}, headers=gerente),
        client.patch(f"/projetos/{pid}", json={"data_inicio": valor}, headers=gerente),
    ]
    for r in respostas:
        assert r.status_code == 422, r.text
        assert "AAAA-MM-DD" in r.text  # mensagem em português dizendo o formato certo


def test_importacao_tambem_recusa_data_de_inicio_com_hora(client, gerente):
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Importado", "data_inicio": "2026-03-15T01:00:00-03:00"},
                    files={"arquivo": ("p.csv", "Fase;Descrição\nIniciação;TAP\n".encode())}, headers=gerente)
    assert r.status_code == 422, r.text
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Importado", "data_inicio": "1773532800"},
                    files={"arquivo": ("p.csv", "Fase;Descrição\nIniciação;TAP\n".encode())}, headers=gerente)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("dia", ["2026-01-01", "2026-03-14", "2026-03-15", "2026-12-31", "2028-02-29"])
def test_data_civil_e_gravada_e_devolvida_exatamente(client, gerente, brasil_api, db, dia):
    pid, fase_id = _projeto(client, gerente)
    r = client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas",
                    json={"descricao": "T", "data_inicio": dia, "data_fim": dia}, headers=gerente)
    assert r.status_code == 201, r.text
    assert (r.json()["data_inicio"], r.json()["data_fim"]) == (dia, dia)
    db.expire_all()
    tarefa = db.get(Tarefa, r.json()["id"])
    assert type(tarefa.data_fim) is date and tarefa.data_fim.isoformat() == dia  # DATE, sem hora


def test_planilha_com_data_e_hora_usa_o_dia_escrito(client, gerente):
    """Excel/CSV não têm fuso: "15/03/2026 23:00" é dia 15, como a pessoa escreveu."""
    r = postar_importacao(client,"/projetos/importar", data={"nome_projeto": "Importado"}, headers=gerente, files={
        "arquivo": ("p.csv", "Fase;Descrição;Data Fim\nIniciação;TAP;2026-03-15 23:59:59\n".encode())})
    assert r.status_code == 201, r.text
    assert r.json()["projeto"]["fases"][0]["tarefas"][0]["data_fim"] == "2026-03-15"


# ---------- "Hoje" é o dia em São Paulo ----------


@pytest.mark.parametrize("instante_utc, atrasada", [
    (datetime(2026, 3, 15, 1, 30, tzinfo=UTC), False),   # 22h30 do dia 14 em Brasília: ainda é dia 14
    (datetime(2026, 3, 15, 2, 59, tzinfo=UTC), False),   # 23h59 do dia 14 em Brasília
    (datetime(2026, 3, 15, 3, 0, tzinfo=UTC), True),     # meia-noite do dia 15 em Brasília
    (datetime(2026, 3, 14, 12, 0, tzinfo=UTC), False),
])
def test_tarefa_so_fica_atrasada_depois_da_meia_noite_de_brasilia(
    client, gerente, criar_usuario, login, monkeypatch, instante_utc, atrasada
):
    """Servidor em UTC: sem o fuso certo, a tarefa que vence hoje aparece atrasada às 21h."""
    pid, fase_id = _projeto(client, gerente)
    eu = client.get("/auth/me", headers=gerente).json()["id"]
    client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas", json={
        "descricao": "Entregar relatório", "data_inicio": "2026-03-10", "data_fim": "2026-03-14",
        "percentual_conclusao": 50, "responsavel_id": eu,
    }, headers=gerente)

    _relogio(monkeypatch, instante_utc)
    indicadores = client.get(f"/projetos/{pid}/indicadores", headers=gerente).json()
    assert indicadores["tarefas_atrasadas"] == int(atrasada)
    minhas = client.get("/tarefas/minhas", headers=gerente).json()["itens"]
    assert minhas[0]["atrasada"] is atrasada


def test_hoje_usa_o_fuso_configurado(monkeypatch):
    _relogio(monkeypatch, datetime(2026, 3, 15, 1, 30, tzinfo=UTC))
    assert tempo.hoje() == date(2026, 3, 14)
    monkeypatch.setattr(tempo.settings, "fuso_horario", "Asia/Tokyo")  # UTC+9
    assert tempo.hoje() == date(2026, 3, 15)


def test_fuso_invalido_e_recusado_na_configuracao():
    from pydantic import ValidationError

    from app.config import Settings

    with pytest.raises(ValidationError):
        Settings(fuso_horario="America/Brasilia")  # não existe na base IANA


# ---------- Dias úteis e feriados não dependem da hora do servidor ----------


@pytest.mark.parametrize("instante_utc", [
    datetime(2026, 4, 20, 2, 0, tzinfo=UTC),    # 23h do dia 19 em Brasília
    datetime(2026, 4, 21, 2, 30, tzinfo=UTC),   # 23h30 do dia 20 (véspera de Tiradentes)
    datetime(2026, 4, 21, 12, 0, tzinfo=UTC),   # meio-dia do feriado
])
@pytest.mark.parametrize("inicio, dias, fim", [
    ("2026-04-20", 1, "2026-04-20"),   # segunda: termina no mesmo dia
    ("2026-04-20", 2, "2026-04-22"),   # pula o 21 (Tiradentes)
    ("2026-04-21", 1, "2026-04-22"),   # começa no feriado: o 1º dia útil é o 22
    ("2026-04-17", 3, "2026-04-22"),   # sexta: pula fim de semana e feriado
])
def test_feriado_e_pulado_no_dia_certo_a_qualquer_hora(client, gerente, brasil_api, monkeypatch,
                                                      instante_utc, inicio, dias, fim):
    _relogio(monkeypatch, instante_utc)
    pid, fase_id = _projeto(client, gerente)
    r = client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas",
                    json={"descricao": "T", "data_inicio": inicio, "duracao_dias_uteis": dias}, headers=gerente)
    assert r.status_code == 201, r.text
    assert r.json()["data_fim"] == fim


def test_virada_de_ano_com_feriado(client, gerente, brasil_api):
    brasil_api.side_effect = lambda url, **_: __import__("httpx").Response(
        200, request=__import__("httpx").Request("GET", url),
        json=[{"date": "2027-01-01", "name": "Confraternização"}] if url.endswith("2027")
        else [{"date": "2026-12-25", "name": "Natal"}])
    pid, fase_id = _projeto(client, gerente)
    r = client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas",
                    json={"descricao": "T", "data_inicio": "2026-12-31", "duracao_dias_uteis": 2}, headers=gerente)
    assert r.json()["data_fim"] == "2027-01-04"  # 31/12 (qui), 01/01 feriado, 02-03 fim de semana, 04 (seg)


# ---------- Instantes saem com fuso ----------


def test_instantes_saem_em_utc_com_fuso_explicito(client, gerente):
    """Sem o "+00:00", o navegador lê "2026-03-15T01:30:00" como hora LOCAL e desloca 3 horas."""
    pid, _ = _projeto(client, gerente)
    comentario = client.post(f"/projetos/{pid}/comentarios", json={"texto": "oi"}, headers=gerente).json()
    historico = client.get(f"/projetos/{pid}/historico", headers=gerente).json()["itens"]
    for instante in [comentario["criado_em"], historico[0]["criado_em"]]:
        convertido = datetime.fromisoformat(instante.replace("Z", "+00:00"))
        assert convertido.utcoffset() is not None, f"sem fuso: {instante}"
        assert convertido.utcoffset().total_seconds() == 0, f"não está em UTC: {instante}"


def test_schema_recusa_datetime_vindo_do_python():
    """Pela API tudo chega como texto; isto cobre quem usa o schema direto (scripts, serviços internos)."""
    from pydantic import TypeAdapter, ValidationError

    from app.schemas import DataProjeto

    validar = TypeAdapter(DataProjeto).validate_python
    assert validar(date(2026, 3, 15)) == date(2026, 3, 15)
    with pytest.raises(ValidationError, match="AAAA-MM-DD"):
        validar(datetime(2026, 3, 15, 1, 0, tzinfo=UTC))
