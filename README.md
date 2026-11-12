# PMO Waterfall — API

Backend do MVP de PMO para projetos Waterfall: FastAPI + SQLAlchemy (PostgreSQL) + Pandas.

- Projetos com as 5 fases (Iniciação, Planejamento, Execução, Monitoramento, Encerramento), criados manualmente, por planilha (.xlsx/.csv) ou com sugestão de escopo por IA (Groq).
- Login próprio (JWT + refresh token em cookie httpOnly) ou Clerk; papéis admin/gerente/membro e acesso por projeto.
- Auditoria, limite de requisições, exportação para Excel, cálculo de prazos em dias úteis (Brasil API) e e-mail de projeto criado (Resend).

## Rodando localmente

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # preencha SECRET_KEY e DATABASE_URL
alembic upgrade head            # cria/atualiza as tabelas
python3 -m app.cli criar-admin --email admin@empresa.com.br --nome "Admin"
python3 -m uvicorn app.main:app --reload
```

Documentação interativa (só em `AMBIENTE=dev`): http://localhost:8000/docs

## Testes

```bash
python3 -m pytest -q                                   # SQLite temporário
TEST_DATABASE_URL=postgresql+psycopg2://... pytest -q  # Postgres descartável (as tabelas são apagadas!)
```

As APIs externas são simuladas nos testes; nenhum teste acessa a rede.

## Integrações (opcionais)

Sem a variável, a integração fica desligada. Detalhes em `.env.example`.

| Variável | Para quê |
|---|---|
| `CLERK_EMISSOR` | Aceitar o token do Clerk (exige o claim `primaryEmail` no session token) |
| `GROQ_API_KEY`, `GROQ_MODELO` | `POST /projetos/ia` |
| `RESEND_API_KEY`, `EMAIL_REMETENTE` | E-mail ao criar projeto |

## Manutenção

Agende diariamente: `python3 -m app.cli limpar-tokens` (remove refresh tokens expirados).
