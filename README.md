# PMO Waterfall

MVP de PMO para projetos Waterfall.

- `app/` — API: FastAPI + SQLAlchemy (PostgreSQL) + Pandas
- `frontend/` — React + TypeScript (Vite, Tailwind, shadcn/ui, TanStack Query)

- Projetos com as 5 fases (Iniciação, Planejamento, Execução, Monitoramento, Encerramento), criados manualmente, por planilha (.xlsx/.csv) ou com sugestão de escopo por IA (Groq).
- Login próprio (JWT + refresh token em cookie httpOnly) ou Clerk; papéis admin/gerente/membro e, em cada projeto, editor ou visualizador (vê, exporta e comenta).
- Auditoria, limite de requisições, exportação para Excel, cálculo de prazos em dias úteis (Brasil API) e e-mail de projeto criado (Resend).

## Rodando localmente

### API

```bash
pip install -r requirements-dev.txt
cp .env.example .env            # preencha SECRET_KEY e DATABASE_URL
alembic upgrade head            # cria/atualiza as tabelas
python3 -m app.cli criar-admin --email admin@empresa.com.br --nome "Admin"
python3 -m uvicorn app.main:app --reload
```

Documentação interativa (só em `AMBIENTE=dev`): http://localhost:8000/docs

### Front

```bash
cd frontend
npm install
cp .env.example .env            # VITE_API_URL=http://localhost:8000
npm run dev                     # http://localhost:5173
```

Acesse por `localhost` (não `127.0.0.1`): o cookie de sessão depende de front e API estarem no mesmo site.

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
