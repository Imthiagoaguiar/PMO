# PMO Waterfall

MVP de PMO para projetos Waterfall.

- `app/` — API: FastAPI + SQLAlchemy (PostgreSQL) + Pandas
- `frontend/` — React + TypeScript (Vite, Tailwind, shadcn/ui, TanStack Query)

- Projetos com as 5 fases (Iniciação, Planejamento, Execução, Monitoramento, Encerramento), criados manualmente, por planilha (.xlsx/.csv) ou com sugestão de escopo por IA (Groq).
- Login próprio (JWT + refresh token em cookie httpOnly) ou Clerk; papéis admin/gerente/membro e, em cada projeto, editor ou visualizador (vê, exporta e comenta).
- Auditoria, limite de requisições, exportação para Excel, cálculo de prazos em dias úteis (Brasil API) e e-mail de projeto criado (Resend).

## Rodando localmente

### API

Requer **Python 3.11+** (o projeto usa 3.14, ver `.python-version`): as versões corrigidas de várias
dependências não existem para Python antigo, e a API se recusa a subir nele.

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # preencha SECRET_KEY e DATABASE_URL
alembic upgrade head            # cria/atualiza as tabelas
python -m app.cli criar-admin --email admin@empresa.com.br --nome "Admin"
python -m uvicorn app.main:app --reload
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
python -m pytest -q                                   # SQLite temporário
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

## Segurança

- Senhas: Argon2id; mínimo de 12 caracteres; recusa senhas previsíveis, com nome/e-mail ou vazadas (HIBP).
- Sessão: JWT de 15 min + refresh token rotativo em cookie httpOnly; trocar a senha ou "encerrar sessões" invalida na hora até os tokens já emitidos.
- Senha definida pelo admin (cadastro ou redefinição) obriga a troca no próximo login.
- Verificação em duas etapas (TOTP, app autenticador) com 10 códigos de recuperação de uso único.
  Obrigatória para admin com login local; opcional para os demais. O segredo fica cifrado no banco
  (`CHAVE_2FA`, obrigatória em produção — não a troque depois que alguém ativar o 2FA).
- Login: mesma resposta para qualquer falha; bloqueio por e-mail + IP, por IP (muitas contas) e por conta.
  Logins certos não contam como falha: um escritório inteiro atrás do mesmo IP entra normalmente.
- Limites de IA e importação são por usuário (colegas no mesmo IP não dividem a cota).
- Várias abas abertas: a renovação de sessão é serializada entre abas e uma corrida não é confundida com roubo de token.
- Requisições limitadas a 1 MB (upload de planilha: 5 MB), inclusive envios em partes.
- Verifique CVEs nas dependências: `python -m pip_audit` e `cd frontend && npm audit`.

## Manutenção

Agende diariamente: `python -m app.cli limpar-tokens` (remove refresh tokens expirados).
