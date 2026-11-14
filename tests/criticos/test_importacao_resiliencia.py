"""
Cenário 1 — Resiliência do upload (POST /projetos/importar, Pandas/openpyxl).

Regras que nenhum arquivo pode quebrar:
- arquivo ruim = 422 com mensagem útil, NUNCA 500;
- a importação é tudo-ou-nada: com qualquer linha inválida, nada é gravado;
- o número da linha no erro é o da planilha que a pessoa vê (para ela achar e corrigir).
"""

import io
import zipfile
from datetime import date, datetime

import pytest

from app.models import Projeto, Tarefa
from tests.criticos.conftest import contar, xlsx

CABECALHO = "Fase;Descrição;Data Início;Data Fim\n"


def _erros(resposta) -> list:
    assert resposta.status_code == 422, resposta.text
    return resposta.json()["detail"]["erros"]


# ---------- Datas quebradas ----------


@pytest.mark.parametrize("data", [
    "31/02/2026",   # dia que não existe
    "29/02/2025",   # 2025 não é bissexto
    "2026-13-01",   # mês 13
    "00/01/2026",
    "amanhã",
    "05.03.2026",   # separador não aceito
    "45678",        # número de série do Excel digitado como texto
    "1/1/1850",     # fora do intervalo aceito
    "01/01/2300",
])
def test_data_quebrada_vira_erro_na_linha_certa(importar, banco_vazio, data):
    r = importar(f"{CABECALHO}Iniciação;TAP;{data};\n".encode())
    erros = _erros(r)
    assert len(erros) == 1 and erros[0].startswith("Linha 2: data_inicio")
    banco_vazio()


@pytest.mark.parametrize("data", ["05/03/2026", "2026-03-05", "05-03-2026", "05/03/26", " 05/03/2026 "])
def test_formatos_de_data_aceitos(importar, data):
    r = importar(f"{CABECALHO}Iniciação;TAP;{data};\n".encode())
    assert r.status_code == 201, r.text
    assert r.json()["projeto"]["fases"][0]["tarefas"][0]["data_inicio"] == "2026-03-05"


def test_data_fim_antes_do_inicio(importar, banco_vazio):
    erros = _erros(importar(f"{CABECALHO}Iniciação;TAP;10/03/2026;09/03/2026\n".encode()))
    assert "anterior" in erros[0]
    banco_vazio()


def test_xlsx_com_celulas_de_data_e_data_hora(importar):
    conteudo = xlsx([
        ["Fase", "Descrição", "Data Início", "Data Fim"],
        ["Iniciação", "TAP", date(2026, 3, 5), datetime(2026, 3, 9, 17, 30)],
    ])
    tarefa = importar(conteudo, "plano.xlsx").json()["projeto"]["fases"][0]["tarefas"][0]
    assert (tarefa["data_inicio"], tarefa["data_fim"]) == ("2026-03-05", "2026-03-09")


def test_xlsx_com_numero_na_coluna_de_data(importar, banco_vazio):
    erros = _erros(importar(xlsx([["Fase", "Descrição", "Data Início"], ["Iniciação", "TAP", 45678]]), "p.xlsx"))
    assert erros[0].startswith("Linha 2: data_inicio")
    banco_vazio()


# ---------- Nulos em colunas obrigatórias ----------


@pytest.mark.parametrize("linha", [";TAP", "Iniciação;", "Iniciação;   ", "  ;  "], ids=["sem-fase", "sem-descricao", "descricao-so-espacos", "tudo-espacos"])
def test_nulo_em_coluna_obrigatoria(importar, banco_vazio, linha):
    erros = _erros(importar(f"Fase;Descrição\n{linha}\n".encode()))
    assert erros and all(e.startswith("Linha 2:") for e in erros)
    banco_vazio()


def test_nulos_no_xlsx(importar, banco_vazio):
    conteudo = xlsx([["Fase", "Descrição"], ["Iniciação", None], [None, "Kickoff"], ["Execução", "Construir"]])
    erros = _erros(importar(conteudo, "plano.xlsx"))
    assert [e.split(":")[0] for e in erros] == ["Linha 2", "Linha 3"]
    banco_vazio()


def test_todos_os_erros_vem_de_uma_vez_e_nada_e_gravado(importar, banco_vazio):
    """Uma linha ruim no meio de 200 boas: nenhuma tarefa entra (tudo-ou-nada)."""
    linhas = ["Iniciação;Tarefa {i}".format(i=i) for i in range(200)]
    linhas[50] = "Fase Inventada;Tarefa ruim"
    linhas[120] = "Execução;"
    erros = _erros(importar(("Fase;Descrição\n" + "\n".join(linhas) + "\n").encode()))
    assert [e.split(":")[0] for e in erros] == ["Linha 52", "Linha 122"]
    banco_vazio()


def test_linha_em_branco_no_meio_nao_desloca_o_numero_da_linha(importar):
    """A linha 4 é a que tem o erro, mesmo com a linha 3 em branco."""
    erros = _erros(importar("Fase;Descrição\nIniciação;TAP\n\nIniciação;\n".encode()))
    assert erros[0].startswith("Linha 4:"), erros


def test_linhas_totalmente_vazias_sao_ignoradas(importar):
    r = importar("Fase;Descrição\nIniciação;TAP\n;\n;\n".encode())
    assert r.status_code == 201, r.text
    assert r.json()["total_tarefas_importadas"] == 1


# ---------- Arquivos disfarçados ----------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def _xlsx_valido() -> bytes:
    return xlsx([["Fase", "Descrição"], ["Iniciação", "TAP"]])


def _zip_sem_planilha() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("leia-me.txt", "não sou uma planilha")
    return buffer.getvalue()


@pytest.mark.parametrize("nome, conteudo", [
    ("plano.csv", "Olá, segue o cronograma conforme combinado.\nAbraços,\nJoão".encode()),  # .txt disfarçado
    ("plano.csv", PNG),
    ("plano.csv", b"\x00\x00\x00\x00"),
    ("plano.csv", b""),
    ("plano.csv", "Fase;Descrição".encode("utf-16")),
    ("plano.xlsx", "Fase;Descrição\nIniciação;TAP\n".encode()),  # CSV com extensão de Excel
    ("plano.xlsx", PNG),
    ("plano.xlsx", _zip_sem_planilha()),
    ("plano.xlsx", b""),
    ("plano.txt", "Fase;Descrição\nIniciação;TAP\n".encode()),
    ("plano", "Fase;Descrição\nIniciação;TAP\n".encode()),
    ("plano.xls", b"\xd0\xcf\x11\xe0"),  # Excel 97 (formato antigo, não suportado)
    ("../../etc/passwd.csv", b"root:x:0:0"),
], ids=["txt-como-csv", "png-como-csv", "bytes-nulos", "vazio", "utf16", "csv-como-xlsx", "png-como-xlsx",
        "zip-sem-planilha", "xlsx-vazio", "extensao-txt", "sem-extensao", "xls-antigo", "nome-malicioso"])
def test_arquivo_disfarcado_e_recusado_sem_erro_500(importar, banco_vazio, nome, conteudo):
    r = importar(conteudo, nome)
    assert r.status_code == 422, r.text
    detalhe = r.json()["detail"]
    assert isinstance(detalhe, dict) and detalhe["mensagem"]
    assert "Traceback" not in r.text and "openpyxl" not in r.text  # sem detalhe interno
    banco_vazio()


def test_zip_bomb_e_recusado_antes_de_descompactar(importar, banco_vazio, monkeypatch):
    """~60 KB no upload que viram 60 MB ao abrir: recusado sem gastar memória lendo.
    (O teto real é 400 MB, para caber 100 mil linhas; aqui ele baixa para o teste não alocar 400 MB.)"""
    from app import services

    monkeypatch.setattr(services, "MAX_XLSX_DESCOMPACTADO", 50 * 1024 * 1024)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "<Types/>")
        z.writestr("xl/worksheets/sheet1.xml", b" " * (60 * 1024 * 1024))
    assert len(buffer.getvalue()) < 1024 * 1024
    r = importar(buffer.getvalue(), "bomba.xlsx")
    assert r.status_code == 422
    assert "grande demais" in r.json()["detail"]["mensagem"]
    banco_vazio()


def test_xlsx_de_verdade_com_nome_csv_e_recusado(importar, banco_vazio):
    """O conteúdo manda: um .xlsx renomeado para .csv não é lido como texto."""
    assert importar(_xlsx_valido(), "plano.csv").status_code == 422
    banco_vazio()


def test_tipo_mime_mentiroso_nao_importa(importar):
    """O navegador pode mandar qualquer Content-Type; quem decide é a extensão + o conteúdo."""
    r = importar("Fase;Descrição\nIniciação;TAP\n".encode(), "plano.csv", tipo="image/png")
    assert r.status_code == 201


def test_extensao_maiuscula(importar):
    assert importar(_xlsx_valido(), "PLANO.XLSX").status_code == 201


def test_csv_com_bom_e_virgula_e_aspas(importar):
    conteudo = '﻿Fase,Descrição\nIniciação,"Reunião; kickoff, com cliente"\n'.encode("utf-8")
    r = importar(conteudo)
    assert r.status_code == 201, r.text
    assert r.json()["projeto"]["fases"][0]["tarefas"][0]["descricao"] == "Reunião; kickoff, com cliente"


def test_xlsx_so_com_cabecalho(importar, banco_vazio):
    r = importar(xlsx([["Fase", "Descrição"]]), "plano.xlsx")
    assert r.status_code == 422 and "não contém tarefas" in r.json()["detail"]["mensagem"]
    banco_vazio()


def test_limite_de_linhas(importar, banco_vazio, monkeypatch):
    """O limite real é 100 mil linhas (IMPORTACAO_MAX_LINHAS); aqui baixa para 5 mil, para o teste ser rápido."""
    from app import services
    from app.config import settings

    assert settings.importacao_max_linhas == 100_000
    monkeypatch.setattr(services, "MAX_LINHAS", 5_000)
    conteudo = ("Fase;Descrição\n" + "Iniciação;T\n" * 5_001).encode()
    r = importar(conteudo)
    assert r.status_code == 422 and "5.000 linhas" in r.json()["detail"]["mensagem"]
    banco_vazio()


def test_importacao_valida_grava_no_projeto_certo(importar, db):
    r = importar("Fase;Descrição\nIniciação;A\nEncerramento;B\n".encode())
    projeto_id = r.json()["projeto"]["id"]
    assert contar(db, Projeto) == 1
    assert contar(db, Tarefa) == 2
    titulos = sorted(t.fase.titulo for t in db.query(Tarefa) if t.fase.projeto_id == projeto_id)
    assert titulos == ["Encerramento", "Iniciação"]
