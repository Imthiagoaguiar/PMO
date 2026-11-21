"""
Features de 20/11/2026: anexos nas tarefas, conclusão registrada, gargalos, apresentação em
PowerPoint e lembrete por e-mail de tarefas atrasadas.
"""

import io
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pptx import Presentation

from app import anexos, lembretes, tempo
from app.config import settings
from app.database import SessionLocal
from app.integracoes import feriados, notificacoes
from app.models import AnexoTarefa, PapelUsuario, Tarefa, Usuario

GERENTE, EDITORA, LEITOR = "gerente@empresa.com.br", "editora@empresa.com.br", "leitor@empresa.com.br"
# Sexta-feira, 20/03/2026, 10h em São Paulo (13h UTC): dia útil, horário comercial.
AGORA = datetime(2026, 3, 20, 13, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "anexos_dir", str(tmp_path / "anexos"))  # cada teste com a própria pasta
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())
    relogio = {"agora": AGORA}
    monkeypatch.setattr(tempo, "agora_utc", lambda: relogio["agora"])
    return relogio


@pytest.fixture
def c(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    editora, leitor = criar_usuario(EDITORA), criar_usuario(LEITOR)
    h = {"gerente": login(GERENTE), "editora": login(EDITORA), "leitor": login(LEITOR)}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP", "data_inicio": "2026-01-05"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": editora.id, "papel": "editor"}, headers=h["gerente"])
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    fases = {f["titulo"]: f["id"] for f in p["fases"]}
    pilares = {x["nome"]: x["id"] for x in p["pilares"]}

    def tarefa(descricao, fase="Execução", quem="gerente", **dados):
        r = client.post(f"/projetos/{p['id']}/fases/{fases[fase]}/tarefas", json={"descricao": descricao, **dados}, headers=h[quem])
        assert r.status_code == 201, r.text
        return r.json()

    def pct(t, valor, quem="gerente"):
        r = client.patch(f"/projetos/{p['id']}/tarefas/{t['id']}", json={"percentual_conclusao": valor}, headers=h[quem])
        assert r.status_code == 200, r.text
        return r.json()

    return {"h": h, "id": p["id"], "fases": fases, "pilares": pilares, "tarefa": tarefa, "pct": pct,
            "ids": {"editora": editora.id, "leitor": leitor.id}}


# ---------- Conclusão registrada ----------


def test_concluida_em_marca_e_desmarca(client, c, ambiente):
    t = c["tarefa"]("A")
    assert t["concluida_em"] is None
    assert c["pct"](t, 100)["concluida_em"] == "2026-03-20T13:00:00Z"
    ambiente["agora"] = AGORA + timedelta(days=2)
    assert c["pct"](t, 100)["concluida_em"] == "2026-03-20T13:00:00Z"  # sem mudança, não "reconclui"
    assert c["pct"](t, 75)["concluida_em"] is None
    assert c["tarefa"]("B", percentual_conclusao=100)["concluida_em"] is not None


# ---------- Anexos ----------


def _enviar(client, c, t, nome="ata.pdf", conteudo=b"%PDF-1.4 conteudo", quem="gerente"):
    return client.post(f"/projetos/{c['id']}/tarefas/{t['id']}/anexos", files={"arquivo": (nome, conteudo)}, headers=c["h"][quem])


def test_enviar_listar_baixar_e_excluir(client, c):
    t = c["tarefa"]("Assinar contrato")
    r = _enviar(client, c, t, "Contrato São Paulo.pdf", b"%PDF-1.4 " + b"x" * 5000, quem="editora")
    assert r.status_code == 201, r.text
    a = r.json()
    assert (a["nome_original"], a["tamanho"], a["tipo_mime"], a["enviado_por"]["nome"]) == (
        "Contrato São Paulo.pdf", 5009, "application/pdf", "editora")
    with SessionLocal() as db:
        chave = db.get(AnexoTarefa, a["id"]).chave
    assert Path(settings.anexos_dir, str(c["id"]), chave).read_bytes().startswith(b"%PDF")  # nome aleatório no disco

    lista = client.get(f"/projetos/{c['id']}/anexos?tarefa_id={t['id']}", headers=c["h"]["leitor"]).json()
    assert [x["id"] for x in lista["itens"]] == [a["id"]]
    r = client.get(f"/projetos/{c['id']}/anexos/{a['id']}/download", headers=c["h"]["leitor"])
    assert r.status_code == 200 and r.content.startswith(b"%PDF")
    assert r.headers["content-disposition"].startswith("attachment;") and "S%C3%A3o" in r.headers["content-disposition"]
    assert r.headers["x-content-type-options"] == "nosniff" and "sandbox" in r.headers["content-security-policy"]

    assert client.delete(f"/projetos/{c['id']}/anexos/{a['id']}", headers=c["h"]["leitor"]).status_code == 403
    assert client.delete(f"/projetos/{c['id']}/anexos/{a['id']}", headers=c["h"]["editora"]).status_code == 204
    assert not Path(settings.anexos_dir, str(c["id"]), chave).exists()
    acoes = [e["acao"] for e in client.get(f"/projetos/{c['id']}/historico", headers=c["h"]["gerente"]).json()["itens"]]
    assert {"anexo.enviado", "anexo.baixado", "anexo.excluido"} <= set(acoes)


@pytest.mark.parametrize("nome", ["virus.exe", "relatorio.pdf.exe", "script.PS1", "instalar.sh", "app.dmg"])
def test_programas_e_scripts_sao_recusados(client, c, nome):
    r = _enviar(client, c, c["tarefa"]("T"), nome, b"MZ...")
    assert r.status_code == 415 and ".zip" in r.json()["detail"]


def test_nome_malicioso_vira_so_o_nome(client, c):
    r = _enviar(client, c, c["tarefa"]("T"), "../../../etc/passwd.txt", b"conteudo")
    assert r.status_code == 201 and r.json()["nome_original"] == "passwd.txt"
    assert list(Path(settings.anexos_dir).rglob("passwd*")) == []  # no disco, só o nome aleatório


@pytest.mark.parametrize("nome, esperado", [
    ("..\\..\\windows\\system32\\a.txt", "a.txt"), ("ata\x00.pdf", "ata.pdf"), ("\x1b[31mvermelho.txt", "[31mvermelho.txt"),
    ("...", "arquivo"), ("", "arquivo"), ("a" * 300 + ".pdf", "a" * 251 + ".pdf"),
])
def test_nome_seguro(nome, esperado):
    assert anexos.nome_seguro(nome) == esperado


def test_vazio_grande_demais_e_cota(client, c, monkeypatch):
    t = c["tarefa"]("T")
    assert _enviar(client, c, t, "vazio.txt", b"").status_code == 422
    monkeypatch.setattr(settings, "anexos_max_mb", 1)
    r = _enviar(client, c, t, "grande.bin", b"x" * (1024 * 1024 + 1))
    assert r.status_code == 413
    assert list(Path(settings.anexos_dir).glob(f"{c['id']}/*")) == []  # o pedaço gravado foi apagado
    monkeypatch.setattr(settings, "anexos_max_projeto_mb", 10)
    with SessionLocal() as db:  # o projeto já está no limite
        db.add(AnexoTarefa(projeto_id=c["id"], tarefa_id=t["id"], tarefa_descricao="T", nome_original="x",
                           chave="0" * 32, tamanho=10 * 1024 * 1024, tipo_mime="x", sha256="0" * 64))
        db.commit()
    r = _enviar(client, c, t)
    assert r.status_code == 413 and "limite" in r.json()["detail"]


def test_anexo_fica_no_historico_quando_a_tarefa_e_excluida(client, c):
    t = c["tarefa"]("Relatório final")
    a = _enviar(client, c, t).json()
    assert client.delete(f"/projetos/{c['id']}/tarefas/{t['id']}", headers=c["h"]["gerente"]).status_code == 204
    item = client.get(f"/projetos/{c['id']}/anexos", headers=c["h"]["leitor"]).json()["itens"][0]
    assert (item["id"], item["tarefa_id"], item["tarefa_descricao"]) == (a["id"], None, "Relatório final")
    assert client.get(f"/projetos/{c['id']}/anexos/{a['id']}/download", headers=c["h"]["leitor"]).status_code == 200


def test_excluir_projeto_apaga_os_arquivos(client, c):
    a = _enviar(client, c, c["tarefa"]("T")).json()
    with SessionLocal() as db:
        chave = db.get(AnexoTarefa, a["id"]).chave
    assert client.delete(f"/projetos/{c['id']}", headers=c["h"]["gerente"]).status_code == 204
    assert not Path(settings.anexos_dir, str(c["id"]), chave).exists()


def test_anexo_de_outro_projeto_e_404(client, c):
    a = _enviar(client, c, c["tarefa"]("T")).json()
    outro = client.post("/projetos/manual", json={"nome_projeto": "Outro"}, headers=c["h"]["gerente"]).json()
    assert client.get(f"/projetos/{outro['id']}/anexos/{a['id']}/download", headers=c["h"]["gerente"]).status_code == 404
    assert client.delete(f"/projetos/{outro['id']}/anexos/{a['id']}", headers=c["h"]["gerente"]).status_code == 404


def test_orfaos_e_espelho_do_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "anexos_dir", str(tmp_path / "anexos"))
    vivo, orfao = (tmp_path / "anexos" / "7" / ("a" * 32)), (tmp_path / "anexos" / "7" / ("b" * 32))
    for f in (vivo, orfao):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"x")
    assert anexos.orfaos({"a" * 32}, idade_minima_s=0) == [orfao]
    assert anexos.espelhar(tmp_path / "copia") == 2
    assert anexos.espelhar(tmp_path / "copia") == 0  # incremental: nada novo
    assert (tmp_path / "copia" / "7" / ("a" * 32)).read_bytes() == b"x"


# ---------- Gargalos ----------


def test_gargalos_por_fase_pilar_e_pessoa(client, c, ambiente):
    ti, pessoas = c["pilares"]["TI"], c["pilares"]["Pessoas"]
    editora = c["ids"]["editora"]
    # Entregue 5 dias depois do prazo (15/03 -> concluída em 20/03), pela editora, no pilar TI.
    a = c["tarefa"]("Migrar dados", responsavel_id=editora, pilar_id=ti, data_inicio="2026-03-02", data_fim="2026-03-15")
    c["pct"](a, 100)
    # Aberta e vencida há 10 dias (prazo 10/03), sem responsável, pilar Pessoas, fase Planejamento.
    c["tarefa"]("Treinar equipe", fase="Planejamento", pilar_id=pessoas, data_inicio="2026-03-02", data_fim="2026-03-10")
    # Entregue no prazo (prazo 31/03), pela editora.
    b = c["tarefa"]("Configurar", responsavel_id=editora, pilar_id=ti, data_inicio="2026-03-02", data_fim="2026-03-31")
    c["pct"](b, 100)
    # Concluída na importação/sem data: fica fora das contas.
    with SessionLocal() as db:
        t = db.get(Tarefa, c["tarefa"]("Antiga", data_inicio="2026-01-05", data_fim="2026-01-10")["id"])
        t.percentual_conclusao = 100
        db.commit()

    g = client.get(f"/projetos/{c['id']}/gargalos", headers=c["h"]["leitor"]).json()
    assert g["dias_atraso_total"] == 15 and g["entregues_sem_data"] == 1
    assert g["pontualidade"] == 50.0
    pessoa = {x["nome"]: x for x in g["por_responsavel"]}
    assert pessoa["editora"]["dias_atraso_entregues"] == 5 and pessoa["editora"]["pontualidade"] == 50.0
    assert pessoa["Sem responsável"]["abertas_atrasadas"] == 1 and pessoa["Sem responsável"]["dias_atraso_abertas"] == 10
    assert [x["nome"] for x in g["por_pilar"]][:2] == ["Pessoas", "TI"]  # quem acumula mais atraso primeiro
    assert [x["nome"] for x in g["por_fase"]][0] == "Planejamento"
    assert [(t["descricao"], t["dias_atraso"], t["situacao"]) for t in g["piores"]] == [
        ("Treinar equipe", 10, "aberta_atrasada"), ("Migrar dados", 5, "entregue_atrasada")]


# ---------- PowerPoint ----------


def test_apresentacao_pptx_completa(client, c):
    ti = c["pilares"]["TI"]
    a = c["tarefa"]("Kickoff", fase="Iniciação", pilar_id=ti, data_inicio="2026-01-05", data_fim="2026-01-20")
    c["pct"](a, 100)
    c["tarefa"]("Atrasada", responsavel_id=c["ids"]["editora"], data_inicio="2026-02-02", data_fim="2026-03-01")
    for forma in ["bandeira", "pin", "estrela", "losango"]:
        client.post(f"/projetos/{c['id']}/marcos", json={"titulo": f"Marco {forma}", "data": "2026-04-10", "forma": forma},
                    headers=c["h"]["gerente"])
    client.post(f"/projetos/{c['id']}/marcos", json={"titulo": "Congelamento", "data": "2026-05-04", "data_fim": "2026-05-15",
                                                     "forma": "faixa"}, headers=c["h"]["gerente"])
    item = client.post(f"/projetos/{c['id']}/orcamento/itens", json={"descricao": "Licenças", "valor_orcado": "1000.00"},
                       headers=c["h"]["gerente"]).json()
    client.post(f"/projetos/{c['id']}/orcamento/lancamentos",
                json={"item_id": item["id"], "valor": "1500.00", "data": "2026-02-01", "descricao": "NF"}, headers=c["h"]["gerente"])

    r = client.get(f"/projetos/{c['id']}/apresentacao", headers=c["h"]["leitor"])
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.presentationml")
    prs = Presentation(io.BytesIO(r.content))
    titulos = [next((s.text_frame.text for s in slide.shapes if s.has_text_frame and s.text_frame.text), "") for slide in prs.slides]
    texto = " ".join(s.text_frame.text for slide in prs.slides for s in slide.shapes if s.has_text_frame)
    assert titulos[0] == "ERP"
    for esperado in ["Resumo", "Linha do tempo", "Marcos", "Pilares", "Orçamento", "Gargalos: onde o prazo escapou", "Pontos de atenção"]:
        assert esperado in texto, esperado
    graficos = [s for slide in prs.slides for s in slide.shapes if s.has_chart]
    assert len(graficos) >= 3  # andamento por fase, orçamento, gargalos (nativos, editáveis)


def test_apresentacao_de_projeto_vazio_e_texto_estranho(client, c):
    client.post(f"/projetos/{c['id']}/marcos", json={"titulo": "Título\x07com controle <b>", "data": "2026-04-10"},
                headers=c["h"]["gerente"])
    r = client.get(f"/projetos/{c['id']}/apresentacao", headers=c["h"]["gerente"])
    assert r.status_code == 200 and len(Presentation(io.BytesIO(r.content)).slides) >= 3


# ---------- Lembretes ----------


@pytest.fixture
def emails(monkeypatch):
    enviados = []

    def _falso(destinatario, nome, tarefas):
        enviados.append((destinatario, [t["tarefa"] for t in tarefas]))
        return True

    monkeypatch.setattr(notificacoes, "enviar_lembrete_atrasadas", _falso)
    return enviados


def _atrasadas(c):
    ed = c["ids"]["editora"]
    c["tarefa"]("Atrasada 1", responsavel_id=ed, data_inicio="2026-03-02", data_fim="2026-03-10")
    c["tarefa"]("Atrasada 2", responsavel_id=ed, data_inicio="2026-03-02", data_fim="2026-03-12")
    c["tarefa"]("No prazo", responsavel_id=ed, data_inicio="2026-03-02", data_fim="2026-04-30")
    c["tarefa"]("Pronta", responsavel_id=ed, data_inicio="2026-03-02", data_fim="2026-03-05", percentual_conclusao=100)


def test_um_email_por_pessoa_com_todas_as_atrasadas_a_cada_4h(client, c, emails):
    _atrasadas(c)
    assert lembretes.executar(AGORA) == 1
    assert emails == [(EDITORA, ["Atrasada 1", "Atrasada 2"])]
    assert lembretes.executar(AGORA + timedelta(hours=3, minutes=59)) == 0  # ainda não deu 4 h
    assert lembretes.executar(AGORA + timedelta(hours=4)) == 1
    assert len(emails) == 2


@pytest.mark.parametrize("agora", [
    datetime(2026, 3, 20, 22, tzinfo=timezone.utc),  # 19h em SP: fora do horário
    datetime(2026, 3, 21, 13, tzinfo=timezone.utc),  # sábado
    datetime(2026, 3, 20, 10, 59, tzinfo=timezone.utc),  # 7h59 em SP
])
def test_so_em_horario_comercial_de_dia_util(client, c, emails, agora):
    _atrasadas(c)
    assert lembretes.executar(agora) == 0 and emails == []


def test_feriado_nao_tem_lembrete(client, c, emails, monkeypatch):
    _atrasadas(c)
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset({date(2026, 3, 20)}))
    assert lembretes.executar(AGORA) == 0


def test_silenciar_e_reativar(client, c, emails):
    _atrasadas(c)
    r = client.post("/auth/lembretes/silenciar", json={"horas": 24}, headers=c["h"]["editora"])
    assert r.status_code == 200 and r.json()["lembretes_silenciados_ate"] is not None
    assert lembretes.executar(AGORA) == 0
    assert client.delete("/auth/lembretes/silenciar", headers=c["h"]["editora"]).json()["lembretes_silenciados_ate"] is None
    assert lembretes.executar(AGORA) == 1


def test_projeto_encerrado_ex_membro_e_desativado_nao_recebem(client, c, emails):
    _atrasadas(c)
    url = f"/projetos/{c['id']}"
    v = client.get(url, headers=c["h"]["gerente"]).json()["versao"]
    client.patch(url, json={"status": "Concluído", "versao": v}, headers=c["h"]["gerente"])
    assert lembretes.executar(AGORA) == 0
    v = client.get(url, headers=c["h"]["gerente"]).json()["versao"]
    client.patch(url, json={"status": "Em andamento", "versao": v}, headers=c["h"]["gerente"])
    with SessionLocal() as db:
        db.get(Usuario, c["ids"]["editora"]).ativo = False
        db.commit()
    assert lembretes.executar(AGORA) == 0


def test_falha_no_envio_tenta_de_novo_na_proxima(client, c, monkeypatch):
    _atrasadas(c)
    tentativas = []
    monkeypatch.setattr(notificacoes, "enviar_lembrete_atrasadas", lambda *a: tentativas.append(a) or False)
    assert lembretes.executar(AGORA) == 0
    assert lembretes.executar(AGORA + timedelta(minutes=15)) == 0
    assert len(tentativas) == 2  # não ficou "reservado" sem ter enviado
    with SessionLocal() as db:
        assert db.get(Usuario, c["ids"]["editora"]).lembrete_enviado_em is None


def test_email_do_lembrete_escapa_textos(monkeypatch):
    enviados = []

    class Emails:
        @staticmethod
        def send(dados):
            enviados.append(dados)

    monkeypatch.setattr(settings, "resend_api_key", "re_teste")
    monkeypatch.setattr(notificacoes.resend, "Emails", Emails)
    assert notificacoes.enviar_lembrete_atrasadas("a@b.com", "Ana", [
        {"tarefa": "<script>x</script>", "projeto": "P & Q", "prazo": date(2026, 3, 10), "dias": 10, "percentual": 0}])
    corpo = enviados[0]
    assert "<script>" not in corpo["html"] and "&lt;script&gt;" in corpo["html"] and "P &amp; Q" in corpo["html"]
    assert corpo["subject"] == "PMO: você tem 1 tarefa(s) atrasada(s)" and "/minhas-tarefas" in corpo["text"]
