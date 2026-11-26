"""
Features de 25/11/2026: Curva S (física + financeira), feedback de áreas e pessoas (só dono/admin) e o
montador de slides com o modelo .pptx da empresa.
"""

import io
import json
import zipfile
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pptx import Presentation
from pptx.util import Inches
from sqlalchemy import update

from app import tempo
from app.config import settings
from app.database import SessionLocal
from app.integracoes import feriados, ia
from app.models import Auditoria, PapelUsuario, Tarefa

GERENTE, EDITORA, LEITOR, ADMIN, FORA = ("gerente@empresa.com.br", "editora@empresa.com.br", "leitor@empresa.com.br",
                                         "admin@empresa.com.br", "fora@empresa.com.br")
HOJE = datetime(2026, 3, 20, 15, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def ambiente(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "anexos_dir", str(tmp_path / "anexos"))
    monkeypatch.setattr(feriados, "feriados_nacionais", lambda ano: frozenset())
    monkeypatch.setattr(tempo, "agora_utc", lambda: HOJE)


@pytest.fixture
def c(client, criar_usuario, login):
    criar_usuario(GERENTE, PapelUsuario.GERENTE)
    editora, leitor = criar_usuario(EDITORA), criar_usuario(LEITOR)
    criar_usuario(ADMIN, PapelUsuario.ADMIN)
    criar_usuario(FORA, PapelUsuario.GERENTE)
    h = {"gerente": login(GERENTE), "editora": login(EDITORA), "leitor": login(LEITOR), "admin": login(ADMIN), "fora": login(FORA)}
    p = client.post("/projetos/manual", json={"nome_projeto": "ERP", "data_inicio": "2026-03-02"}, headers=h["gerente"]).json()
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": editora.id, "papel": "editor"}, headers=h["gerente"])
    client.post(f"/projetos/{p['id']}/membros", json={"usuario_id": leitor.id}, headers=h["gerente"])
    fase = p["fases"][2]["id"]

    def tarefa(descricao, **dados):
        r = client.post(f"/projetos/{p['id']}/fases/{fase}/tarefas", json={"descricao": descricao, **dados}, headers=h["gerente"])
        assert r.status_code == 201, r.text
        return r.json()

    return {"h": h, "id": p["id"], "tarefa": tarefa, "editora": editora.id, "pilares": {x["nome"]: x["id"] for x in p["pilares"]}}


def _historico(projeto_id, tarefa_id, quando, antes, depois):
    with SessionLocal() as db:
        db.add(Auditoria(acao="tarefa.atualizada", entidade="tarefa", entidade_id=tarefa_id, projeto_id=projeto_id,
                         detalhes={"percentual_conclusao": [antes, depois]}, criado_em=quando))
        db.commit()


def _ajustar(tarefa_id, **valores):
    with SessionLocal() as db:
        db.execute(update(Tarefa).where(Tarefa.id == tarefa_id).values(**valores))
        db.commit()


# ---------- Curva S ----------


def test_curva_s_fisica_e_financeira(client, c):
    a = c["tarefa"]("A", data_inicio="2026-03-02", data_fim="2026-03-11")   # 10 dias
    b = c["tarefa"]("B", data_inicio="2026-03-12", data_fim="2026-03-31")   # 20 dias
    with SessionLocal() as db:  # as tarefas "existem" desde 01/03 (o histórico real é de hoje, fora da janela)
        db.execute(update(Auditoria).where(Auditoria.projeto_id == c["id"]).values(criado_em=datetime(2026, 3, 1, 12, tzinfo=timezone.utc)))
        db.commit()
    _historico(c["id"], a["id"], datetime(2026, 3, 11, 18, tzinfo=timezone.utc), 0, 100)
    _historico(c["id"], b["id"], datetime(2026, 3, 18, 18, tzinfo=timezone.utc), 0, 40)
    _ajustar(a["id"], percentual_conclusao=100)
    _ajustar(b["id"], percentual_conclusao=40)
    item = client.post(f"/projetos/{c['id']}/orcamento/itens", json={"descricao": "Obra", "valor_orcado": "30000.00"},
                       headers=c["h"]["gerente"]).json()
    for valor, dia in (("10000.00", "2026-03-05"), ("5000.00", "2026-03-15")):
        client.post(f"/projetos/{c['id']}/orcamento/lancamentos",
                    json={"item_id": item["id"], "valor": valor, "data": dia, "descricao": "NF"}, headers=c["h"]["gerente"])

    r = client.get(f"/projetos/{c['id']}/curva-s", headers=c["h"]["leitor"]).json()
    # Hoje (20/03): planejado = (10 de A + 9 dias de B) / 30 = 63,3%; realizado = (10x100 + 20x40) / 30 = 60%.
    assert (r["planejado_hoje"], r["realizado_hoje"], r["desvio_pp"], r["idp"]) == (63.3, 60.0, -3.3, 0.95)
    assert Decimal(r["realizado_rs_hoje"]) == Decimal("15000") and Decimal(r["planejado_rs_hoje"]) == Decimal("19000.00")
    pontos = {p["data"]: p for p in r["pontos"]}
    assert pontos["2026-03-02"]["realizado_pct"] == 0  # antes de qualquer avanço
    assert pontos["2026-03-31"]["realizado_pct"] is None and pontos["2026-03-31"]["planejado_pct"] == 100.0  # futuro
    assert r["com_orcamento"] is True and r["tarefas_sem_datas"] == 0


def test_curva_s_vazia_e_sem_datas(client, c):
    c["tarefa"]("Sem datas")
    r = client.get(f"/projetos/{c['id']}/curva-s", headers=c["h"]["gerente"]).json()
    assert r["pontos"] == [] and r["tarefas_sem_datas"] == 1 and r["idp"] is None


# ---------- Feedback ----------


def _cenario_feedback(c):
    ed, ti = c["editora"], c["pilares"]["TI"]
    # A editora: 3 entregas no prazo (2 antecipadas), nenhuma reaberta.
    for i, (prazo, entregue) in enumerate([("2026-03-10", "2026-03-08"), ("2026-03-12", "2026-03-11"), ("2026-03-15", "2026-03-15")]):
        t = c["tarefa"](f"Entrega {i}", responsavel_id=ed, pilar_id=ti, data_inicio="2026-03-02", data_fim=prazo)
        _ajustar(t["id"], percentual_conclusao=100, concluida_em=datetime.fromisoformat(entregue + "T15:00:00+00:00"))
    # "Fornecedor X" (sem conta): 3 entregas atrasadas, uma reaberta 2x, e uma aberta vencida há 10 dias.
    pessoas = c["pilares"]["Pessoas"]
    for i, (prazo, entregue) in enumerate([("2026-03-05", "2026-03-15"), ("2026-03-06", "2026-03-16"), ("2026-03-07", "2026-03-17")]):
        t = c["tarefa"](f"Compra {i}", responsavel="Fornecedor X", pilar_id=pessoas, data_inicio="2026-03-02", data_fim=prazo)
        _ajustar(t["id"], percentual_conclusao=100, concluida_em=datetime.fromisoformat(entregue + "T15:00:00+00:00"))
        if i == 0:
            for _ in range(2):
                _historico(c["id"], t["id"], HOJE, 100, 50)
    c["tarefa"]("Contrato sigiloso com a Fulana", responsavel="Fornecedor X", pilar_id=pessoas,
                data_inicio="2026-03-02", data_fim="2026-03-10")


def test_feedback_pontos_fortes_e_a_melhorar(client, c):
    _cenario_feedback(c)
    r = client.get(f"/projetos/{c['id']}/feedback", headers=c["h"]["gerente"])
    assert r.status_code == 200
    f = r.json()
    pessoas = {p["nome"]: p for p in f["pessoas"]}
    editora, fornecedor = pessoas["editora"], pessoas["Fornecedor X"]
    assert "Entrega no prazo: 3 de 3 tarefas (100%)." in editora["pontos_fortes"]
    assert "Entregou 2 tarefas antes do prazo." in editora["pontos_fortes"]
    assert "Nenhuma entrega precisou ser reaberta." in editora["pontos_fortes"]
    assert editora["pontos_a_melhorar"] == []
    melhorar = " ".join(fornecedor["pontos_a_melhorar"])
    assert "Pontualidade de 0% (0 de 3 entregas no prazo)." in fornecedor["pontos_a_melhorar"]
    assert "1 tarefa em aberto passou do prazo: “Contrato sigiloso com a Fulana”, há 10 dias." in melhorar
    assert "2 tarefas reabertas depois de concluídas (retrabalho)." in fornecedor["pontos_a_melhorar"]
    assert fornecedor["numeros"]["atraso_medio"] == 10.0 and f["pontualidade_projeto"] == 50
    assert {a["nome"] for a in f["areas"]} == {"TI", "Pessoas"}


def test_pontualidade_baixa_nunca_vira_ponto_forte():
    """67% com média 50%: está acima da média, mas abaixo de 70% é ponto a melhorar, e não pode aparecer nos dois."""
    from app.feedback import _Conta, _frases

    conta = _Conta(nome="Eva")
    conta.total, conta.entregues, conta.no_prazo = 3, 3, 2
    fortes, melhorar, _ = _frases(conta, {"pontualidade": 50.0, "entregues": 4 / 3, "atraso_medio": None}, True, 0)
    assert not any("Pontualidade" in f for f in fortes)
    assert "Pontualidade de 67% (2 de 3 entregas no prazo)." in melhorar
    assert "Volume de entregas alto: 3 tarefas concluídas (média do projeto: 1,3)." in fortes


def test_feedback_com_poucos_dados_nao_julga(client, c):
    t = c["tarefa"]("Uma só", responsavel_id=c["editora"], data_inicio="2026-03-02", data_fim="2026-03-05")
    _ajustar(t["id"], percentual_conclusao=100, concluida_em=datetime(2026, 3, 9, tzinfo=timezone.utc))
    editora = client.get(f"/projetos/{c['id']}/feedback", headers=c["h"]["gerente"]).json()["pessoas"][0]
    assert editora["pontos_fortes"] == [] and editora["pontos_a_melhorar"] == []
    assert "Poucos dados" in editora["observacao"]


@pytest.mark.parametrize("quem, esperado", [("gerente", 200), ("admin", 200), ("editora", 403), ("leitor", 403), ("fora", 404)])
def test_feedback_so_dono_e_admin(client, c, quem, esperado):
    assert client.get(f"/projetos/{c['id']}/feedback", headers=c["h"][quem]).status_code == esperado
    r = client.post(f"/projetos/{c['id']}/feedback/ia", json={"tipo": "pessoa", "chave": "x"}, headers=c["h"][quem])
    assert r.status_code == (404 if esperado == 200 else esperado)  # 404 da chave inexistente para quem pode


def test_texto_da_ia_recebe_so_numeros_sem_nomes(client, c, monkeypatch):
    _cenario_feedback(c)
    enviado = {}

    def _groq(texto, sistema=None, etiqueta="texto", max_tokens=4096):
        enviado.update(texto=texto, sistema=sistema)
        return json.dumps({"texto": "Fornecedor X precisa organizar as compras."})

    monkeypatch.setattr(settings, "groq_api_key", "gsk_teste")
    monkeypatch.setattr(ia, "_chamar_groq", _groq)
    f = client.get(f"/projetos/{c['id']}/feedback", headers=c["h"]["gerente"]).json()
    chave = next(p["chave"] for p in f["pessoas"] if p["nome"] == "Fornecedor X")
    r = client.post(f"/projetos/{c['id']}/feedback/ia", json={"tipo": "pessoa", "chave": chave}, headers=c["h"]["gerente"])
    assert r.status_code == 200, r.text
    assert "Fornecedor X" not in enviado["texto"] and "Fulana" not in enviado["texto"] and "uma tarefa" in enviado["texto"]
    assert "NÃO julgue" in enviado["sistema"]
    assert r.json()["texto"] == "a pessoa precisa organizar as compras."  # o nome não volta nem se a IA o usar


# ---------- Montador de slides ----------


def _textos(slide):
    return [s.text_frame.text for s in slide.shapes if s.has_text_frame and s.text_frame.text.strip()]


def _montar(client, c, slides, modelo_id=None, quem="leitor"):
    r = client.post(f"/projetos/{c['id']}/apresentacao", json={"slides": slides, "modelo_id": modelo_id}, headers=c["h"][quem])
    assert r.status_code == 200, r.text
    return Presentation(io.BytesIO(r.content))


def test_montador_ordem_titulos_notas_e_texto(client, c):
    c["tarefa"]("T", data_inicio="2026-03-02", data_fim="2026-03-20")
    prs = _montar(client, c, [
        {"tipo": "texto", "titulo": "Decisões da semana", "texto": "- Aprovar o escopo\n• Contratar fornecedor\n\n", "notas": "Falar 2 min"},
        {"tipo": "curva_s", "titulo": "Onde estamos"},
        {"tipo": "capa"},
        {"tipo": "orcamento"},  # projeto sem orçamento: pulado
    ])
    assert len(prs.slides) == 3
    assert "Decisões da semana" in _textos(prs.slides[0])
    assert "•  Aprovar o escopo\n•  Contratar fornecedor" in _textos(prs.slides[0])
    assert prs.slides[0].notes_slide.notes_text_frame.text == "Falar 2 min"
    assert "Onde estamos" in _textos(prs.slides[1]) and any(s.has_chart for s in prs.slides[1].shapes)
    assert _textos(prs.slides[2])[0] == "ERP"


@pytest.mark.parametrize("corpo", [{"slides": []}, {"slides": [{"tipo": "inventado"}]}, {"slides": [{"tipo": "capa"}] * 41}])
def test_montador_valida_o_pedido(client, c, corpo):
    assert client.post(f"/projetos/{c['id']}/apresentacao", json=corpo, headers=c["h"]["gerente"]).status_code == 422


def _modelo_pptx(largura=10, altura=7.5, potx=False, macro=False) -> bytes:
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(largura), Inches(altura)
    prs.slides.add_slide(prs.slide_layouts[0]).shapes.title.text = "Exemplo que deve sumir"
    buf = io.BytesIO()
    prs.save(buf)
    if not (potx or macro):
        return buf.getvalue()
    entrada, saida = zipfile.ZipFile(io.BytesIO(buf.getvalue())), io.BytesIO()
    with zipfile.ZipFile(saida, "w") as z:
        for info in entrada.infolist():
            dados = entrada.read(info.filename)
            if potx and info.filename == "[Content_Types].xml":
                dados = dados.replace(b"presentationml.presentation.main+xml", b"presentationml.template.main+xml")
            z.writestr(info, dados)
        if macro:
            z.writestr("ppt/vbaProject.bin", b"macro")
    return saida.getvalue()


def _enviar_modelo(client, c, conteudo, nome="Modelo da empresa", quem="admin", arquivo="modelo.pptx"):
    return client.post("/modelos-apresentacao", data={"nome": nome}, files={"arquivo": (arquivo, conteudo)}, headers=c["h"][quem])


def test_modelo_da_empresa_4x3(client, c):
    c["tarefa"]("Kickoff", data_inicio="2026-03-02", data_fim="2026-03-20", pilar_id=c["pilares"]["TI"])
    r = _enviar_modelo(client, c, _modelo_pptx(10, 7.5))
    assert r.status_code == 201, r.text
    modelo = r.json()
    assert [m["nome"] for m in client.get("/modelos-apresentacao", headers=c["h"]["leitor"]).json()] == ["Modelo da empresa"]
    prs = _montar(client, c, [{"tipo": "capa"}, {"tipo": "resumo", "titulo": "Visão geral"}, {"tipo": "pilares"},
                              {"tipo": "texto", "titulo": "Próximos passos", "texto": "Item 1\nItem 2"}], modelo["id"])
    assert (prs.slide_width, prs.slide_height) == (Inches(10), Inches(7.5))  # o tamanho do modelo
    assert len(prs.slides) == 4  # os slides de exemplo do modelo não vêm junto
    assert prs.slides[0].slide_layout.name == "Title Slide" and prs.slides[0].shapes.title.text == "ERP"
    assert prs.slides[1].shapes.title.text == "Visão geral" and prs.slides[1].slide_layout.name == "Title Only"
    assert prs.slides[3].slide_layout.name == "Title and Content"
    for slide in prs.slides:  # tudo dentro do slide 4:3 (o conteúdo 16:9 foi reposicionado)
        for forma in slide.shapes:
            assert forma.left >= 0 and forma.top >= 0, forma.name
            assert forma.left + forma.width <= prs.slide_width + 10 and forma.top + forma.height <= prs.slide_height + 10, forma.name


def test_modelo_potx_e_recusas(client, c):
    assert _enviar_modelo(client, c, _modelo_pptx(potx=True), arquivo="m.potx").status_code == 201
    r = _enviar_modelo(client, c, b"isto nao e um pptx")
    assert r.status_code == 422 and "não é um PowerPoint" in r.json()["detail"]
    r = _enviar_modelo(client, c, _modelo_pptx(macro=True))
    assert r.status_code == 422 and "macro" in r.json()["detail"]
    assert _enviar_modelo(client, c, _modelo_pptx(), quem="gerente").status_code == 403


def test_excluir_modelo_e_modelo_inexistente(client, c):
    modelo = _enviar_modelo(client, c, _modelo_pptx()).json()
    assert client.delete(f"/modelos-apresentacao/{modelo['id']}", headers=c["h"]["gerente"]).status_code == 403
    assert client.delete(f"/modelos-apresentacao/{modelo['id']}", headers=c["h"]["admin"]).status_code == 204
    r = client.post(f"/projetos/{c['id']}/apresentacao", json={"slides": [{"tipo": "capa"}], "modelo_id": modelo["id"]},
                    headers=c["h"]["gerente"])
    assert r.status_code == 404


def test_limpeza_nao_apaga_os_modelos(client, c):
    from app import anexos

    _enviar_modelo(client, c, _modelo_pptx())
    assert anexos.orfaos(set(), idade_minima_s=0) == []


def test_apresentacao_padrao_tem_curva_s(client, c):
    c["tarefa"]("T", data_inicio="2026-03-02", data_fim="2026-03-31")
    prs = Presentation(io.BytesIO(client.get(f"/projetos/{c['id']}/apresentacao", headers=c["h"]["leitor"]).content))
    assert any("Curva S: planejado x realizado" in _textos(s) for s in prs.slides)
    for slide in prs.slides:  # nada sai do slide (ex.: o % da fase que vai até a borda da linha do tempo)
        for forma in slide.shapes:
            assert forma.left >= 0 and forma.top >= 0, forma.name
            assert forma.left + forma.width <= prs.slide_width and forma.top + forma.height <= prs.slide_height, (forma.name, _textos(slide))
