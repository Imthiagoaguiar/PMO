"""
Casos de borda: entradas estranhas que não podem virar erro 500, perda de dados ou dado inválido no banco.
"""

import io
from datetime import date

import openpyxl
import pytest

from app.integracoes import feriados
from app.models import PapelUsuario
from tests.conftest import postar_importacao

GERENTE, MEMBRO, ADMIN = "gerente@empresa.com.br", "membro@empresa.com.br", "admin@empresa.com.br"


@pytest.fixture(autouse=True)
def sem_internet(monkeypatch):
    """Feriados fixos: nenhum teste daqui acessa a Brasil API."""
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset({date(2026, 5, 1)}))


@pytest.fixture
def h(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    criar_usuario(MEMBRO)
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    return {"gerente": login(GERENTE), "membro": login(MEMBRO), "admin": login(ADMIN)}


def _projeto(client, h, **extra):
    r = client.post("/projetos/manual", json={"nome_projeto": "ERP", **extra}, headers=h["gerente"])
    assert r.status_code == 201, r.text
    return r.json()


def _importar(client, h, conteudo: bytes, nome_arquivo="plano.csv", **form):
    return postar_importacao(client,
        "/projetos/importar", data={"nome_projeto": "Importado", **form},
        files={"arquivo": (nome_arquivo, conteudo)}, headers=h["gerente"],
    )


def _xlsx(linhas, formatos=None) -> bytes:
    livro = openpyxl.Workbook()
    planilha = livro.active
    for linha in linhas:
        planilha.append(linha)
    for coluna, formato in (formatos or {}).items():
        for celula in planilha[coluna][1:]:
            celula.number_format = formato
    buffer = io.BytesIO()
    livro.save(buffer)
    return buffer.getvalue()


# ---------- Usuários ----------


@pytest.mark.parametrize("campo", ["papel", "ativo", "nome"])
def test_admin_nao_grava_nulo_em_campo_obrigatorio_do_usuario(client, h, campo):
    membro_id = client.get("/usuarios?busca=membro", headers=h["admin"]).json()["itens"][0]["id"]
    r = client.patch(f"/usuarios/{membro_id}", json={campo: None}, headers=h["admin"])
    assert r.status_code == 422, r.text


def test_nome_do_usuario_e_aparado_na_edicao(client, h):
    membro_id = client.get("/usuarios?busca=membro", headers=h["admin"]).json()["itens"][0]["id"]
    assert client.patch(f"/usuarios/{membro_id}", json={"nome": "   "}, headers=h["admin"]).status_code == 422
    r = client.patch(f"/usuarios/{membro_id}", json={"nome": "  Maria Souza  "}, headers=h["admin"])
    assert r.json()["nome"] == "Maria Souza"


def test_diretorio_nao_lista_todo_mundo_com_busca_em_branco(client, h):
    assert client.get("/usuarios/diretorio?busca=%20%20", headers=h["gerente"]).status_code == 422


def test_login_ignora_maiusculas_no_email(client, h):
    r = client.post("/auth/login", data={"username": "  Membro@Empresa.com.BR ", "password": "SenhaForte!2026"})
    assert r.status_code == 200


# ---------- Importação ----------


@pytest.mark.parametrize("nome", ["     ", "  ab  "])
def test_importacao_recusa_nome_de_projeto_vazio_ou_curto(client, h, nome):
    r = _importar(client, h, "Fase;Descrição\nIniciação;TAP\n".encode(), nome_projeto=nome)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("orcamento", ["1e20", "100.12345", "-1"])
def test_importacao_valida_orcamento_como_na_criacao_manual(client, h, orcamento):
    r = _importar(client, h, "Fase;Descrição\nIniciação;TAP\n".encode(), orcamento_aprovado=orcamento)
    assert r.status_code == 422, r.text


@pytest.mark.parametrize("percentual", ["inf", "-inf", "nan", "1e400"])
def test_percentual_absurdo_na_planilha_vira_erro_de_validacao(client, h, percentual):
    r = _importar(client, h, f"Fase;Descrição;% Conclusão\nIniciação;TAP;{percentual}\n".encode())
    assert r.status_code == 422, r.text
    assert "Linha 2" in r.json()["detail"]["erros"][0]


def test_percentual_formatado_como_porcentagem_no_excel(client, h):
    """No Excel, 50% numa célula com formato de porcentagem é guardado como 0,5."""
    conteudo = _xlsx(
        [["Fase", "Descrição", "% Conclusão"], ["Iniciação", "TAP", 0.5], ["Iniciação", "Kickoff", 1]],
        formatos={"C": "0%"},
    )
    r = _importar(client, h, conteudo, "plano.xlsx")
    assert r.status_code == 201, r.text
    tarefas = r.json()["projeto"]["fases"][0]["tarefas"]
    assert [t["percentual_conclusao"] for t in tarefas] == [50, 100]


def test_csv_latin1_com_ponto_e_virgula(client, h):
    r = _importar(client, h, "Fase;Descrição;Responsável\nIniciação;Reunião;João\n".encode("latin-1"))
    assert r.status_code == 201, r.text
    tarefa = r.json()["projeto"]["fases"][0]["tarefas"][0]
    assert (tarefa["descricao"], tarefa["responsavel"]) == ("Reunião", "João")


@pytest.mark.parametrize(
    "conteudo",
    [b"", b"Fase\n", b"Fase;Descricao\n", b"\xff\xfe\x00", "Fase;Tarefa;Descrição\nIniciação;A;B\n".encode()],
    ids=["vazio", "uma-coluna", "so-cabecalho", "binario", "colunas-duplicadas"],
)
def test_csv_estranho_nunca_vira_erro_500(client, h, conteudo):
    assert _importar(client, h, conteudo).status_code in (201, 422)


def test_colunas_que_significam_o_mesmo_sao_recusadas(client, h):
    r = _importar(client, h, "Fase;Tarefa;Descrição\nIniciação;A;B\n".encode())
    assert r.status_code == 422
    assert "'Tarefa' e 'Descrição'" in r.json()["detail"]["mensagem"]


def test_tarefa_chamada_na_nao_some(client, h):
    """O pandas trata "NA", "null", "None" como célula vazia por padrão."""
    r = _importar(client, h, "Fase;Descrição;Responsável\nIniciação;NA;None\n".encode())
    assert r.status_code == 201, r.text
    tarefa = r.json()["projeto"]["fases"][0]["tarefas"][0]
    assert (tarefa["descricao"], tarefa["responsavel"]) == ("NA", "None")


# ---------- Exportação ----------


@pytest.mark.parametrize("texto", ["- revisar contrato", "+55 11 99999-0000", "@equipe financeiro", "=SOMA(A1:A2)"])
def test_exportar_e_reimportar_preserva_o_texto(client, h, texto):
    projeto = _projeto(client, h)
    fase_id = projeto["fases"][0]["id"]
    r = client.post(f"/projetos/{projeto['id']}/fases/{fase_id}/tarefas",
                    json={"descricao": texto, "responsavel": texto}, headers=h["gerente"])
    assert r.status_code == 201

    exportado = client.get(f"/projetos/{projeto['id']}/exportar", headers=h["gerente"]).content
    celula = openpyxl.load_workbook(io.BytesIO(exportado)).active["B2"]
    assert celula.data_type != "f", "virou fórmula no Excel"
    assert celula.value == texto, "o texto exportado não é o que a pessoa escreveu"

    r = _importar(client, h, exportado, "plano.xlsx")
    tarefa = r.json()["projeto"]["fases"][0]["tarefas"][0]
    assert (tarefa["descricao"], tarefa["responsavel"]) == (texto, texto)


def test_nome_do_arquivo_exportado_mantem_acentos(client, h):
    projeto = client.post("/projetos/manual", json={"nome_projeto": "Implantação São Paulo"}, headers=h["gerente"]).json()
    r = client.get(f"/projetos/{projeto['id']}/exportar", headers=h["gerente"])
    disposicao = r.headers["content-disposition"]
    assert 'filename="Implantacao_Sao_Paulo.xlsx"' in disposicao  # navegadores antigos
    assert "filename*=UTF-8''Implanta%C3%A7%C3%A3o%20S%C3%A3o%20Paulo.xlsx" in disposicao


# ---------- Datas ----------


def test_data_no_limite_do_calendario_nao_derruba_o_servidor(client, h):
    projeto = _projeto(client, h)
    fase_id = projeto["fases"][0]["id"]
    r = client.post(f"/projetos/{projeto['id']}/fases/{fase_id}/tarefas",
                    json={"descricao": "Fim dos tempos", "data_inicio": "9999-12-30", "duracao_dias_uteis": 10},
                    headers=h["gerente"])
    assert r.status_code == 422, r.text

    r = client.post("/projetos/manual", json={
        "nome_projeto": "Longo", "data_inicio": "9999-12-01",
        "fases": [{"fase": "Iniciação", "tarefas": [{"descricao": "TAP", "duracao_dias_uteis": 100}]}],
    }, headers=h["gerente"])
    assert r.status_code == 422, r.text


def test_tarefa_com_duracao_comecando_no_sabado_comeca_no_dia_util(client, h):
    """Igual ao cronograma encadeado: começar num sábado significa começar na segunda."""
    projeto = _projeto(client, h)
    fase_id = projeto["fases"][0]["id"]
    r = client.post(f"/projetos/{projeto['id']}/fases/{fase_id}/tarefas",
                    json={"descricao": "TAP", "data_inicio": "2026-03-07", "duracao_dias_uteis": 2},
                    headers=h["gerente"])
    assert (r.json()["data_inicio"], r.json()["data_fim"]) == ("2026-03-09", "2026-03-10")


# ---------- Exclusões em cascata ----------


def test_excluir_tarefa_e_projeto_com_comentarios(client, h):
    projeto = _projeto(client, h)
    pid, fase_id = projeto["id"], projeto["fases"][0]["id"]
    tarefa = client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas", json={"descricao": "TAP"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{pid}/comentarios", json={"texto": "na tarefa", "tarefa_id": tarefa["id"]}, headers=h["gerente"])
    client.post(f"/projetos/{pid}/comentarios", json={"texto": "no projeto"}, headers=h["gerente"])

    assert client.delete(f"/projetos/{pid}/tarefas/{tarefa['id']}", headers=h["gerente"]).status_code == 204
    restantes = client.get(f"/projetos/{pid}/comentarios", headers=h["gerente"]).json()
    assert [c["texto"] for c in restantes["itens"]] == ["no projeto"]

    assert client.delete(f"/projetos/{pid}", headers=h["gerente"]).status_code == 204
    acoes = [a["acao"] for a in client.get("/auditoria?limite=100", headers=h["admin"]).json()["itens"]]
    assert "projeto.excluido" in acoes  # o histórico fica


# ---------- Membros ----------


def test_membro_removido_perde_acesso_e_as_tarefas(client, h):
    projeto = _projeto(client, h)
    pid, fase_id = projeto["id"], projeto["fases"][0]["id"]
    membro_id = client.get("/usuarios/diretorio?busca=membro", headers=h["gerente"]).json()[0]["id"]
    client.post(f"/projetos/{pid}/membros", json={"usuario_id": membro_id, "papel": "editor"}, headers=h["gerente"])
    client.post(f"/projetos/{pid}/fases/{fase_id}/tarefas", json={"descricao": "TAP", "responsavel_id": membro_id},
                headers=h["gerente"])
    assert client.get("/tarefas/minhas", headers=h["membro"]).json()["total"] == 1

    assert client.delete(f"/projetos/{pid}/membros/{membro_id}", headers=h["gerente"]).status_code == 204
    assert client.get("/tarefas/minhas", headers=h["membro"]).json()["total"] == 0
    assert client.get(f"/projetos/{pid}", headers=h["membro"]).status_code == 404
    assert client.post(f"/projetos/{pid}/comentarios", json={"texto": "oi"}, headers=h["membro"]).status_code == 404
