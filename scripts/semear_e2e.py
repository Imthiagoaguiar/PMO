"""
Dados dos testes de navegador (frontend/e2e). Roda num banco DESCARTÁVEL criado pelo scripts/e2e.sh.
Recusa rodar se o banco não for o SQLite temporário do E2E: nunca semeia o banco de verdade.
"""

import os
import sys

from app.database import Base, SessionLocal, engine
from app.models import PapelProjeto, PapelUsuario, ProjetoMembro, Tarefa, Usuario
from app.security import gerar_hash_senha
from app import services

SENHA = "Ponte-Azul-Gaveta-2919"

if not os.environ.get("DATABASE_URL", "").startswith("sqlite:///") or "e2e" not in os.environ["DATABASE_URL"]:
    sys.exit("semear_e2e.py só roda no banco temporário do E2E (scripts/e2e.sh)")

Base.metadata.create_all(engine)
with SessionLocal() as db:
    hash_ = gerar_hash_senha(SENHA)
    gil = Usuario(email="gerente@e2e.com", nome="Gil Gerente", senha_hash=hash_, papel=PapelUsuario.GERENTE)
    eva = Usuario(email="editora@e2e.com", nome="Eva Editora", senha_hash=hash_, papel=PapelUsuario.MEMBRO)
    lia = Usuario(email="leitora@e2e.com", nome="Lia Leitora", senha_hash=hash_, papel=PapelUsuario.MEMBRO)
    # Só para o teste de "Esqueci minha senha" (ele troca a senha dela; os outros testes não a usam).
    rui = Usuario(email="esqueceu@e2e.com", nome="Rui Esquecido", senha_hash=hash_, papel=PapelUsuario.MEMBRO)
    # Admin só para a varredura das telas de administração (Usuários, Auditoria, Vigia).
    ana = Usuario(email="admin@e2e.com", nome="Ana Admin", senha_hash=hash_, papel=PapelUsuario.ADMIN)
    db.add_all([gil, eva, lia, rui, ana])
    db.flush()
    projeto = services.criar_projeto_com_fases(db, gil, "Implantação ERP")
    db.flush()
    projeto.membros += [ProjetoMembro(usuario=eva, papel=PapelProjeto.EDITOR),
                        ProjetoMembro(usuario=lia, papel=PapelProjeto.VISUALIZADOR)]
    projeto.fases[0].tarefas += [Tarefa(descricao="Termo de Abertura"), Tarefa(descricao="Tarefa de concorrência")]
    # Segundo projeto do Gil: para trocar de projeto pelo painel lateral.
    services.criar_projeto_com_fases(db, gil, "Expansão da Filial")
    db.commit()
print("E2E: banco semeado (Gil gerente, Eva editora, Lia leitora; projetos Implantação ERP e Expansão da Filial)")
