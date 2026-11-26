# PMO Waterfall

MVP de PMO para projetos Waterfall.

- `app/` — API: FastAPI + SQLAlchemy (PostgreSQL) + Pandas
- `frontend/` — React + TypeScript (Vite, Tailwind, shadcn/ui, TanStack Query)

- Projetos criados manualmente, por planilha (.xlsx/.csv) ou com sugestão de escopo por IA (Groq).
- **Fases editáveis**: o projeto nasce com as 5 do Waterfall (Iniciação... Encerramento) ou em branco; o editor renomeia,
  reordena, exclui (escolhendo para onde vão as tarefas) e cria fases, e decide se o projeto segue a ordem delas.
  A planilha aceita fases próprias (criadas na ordem em que aparecem).
- **Dependências entre tarefas**: "B só começa depois que A terminar" ou "C só termina depois de A". O sistema bloqueia
  o avanço que quebraria a regra, recusa ciclos e empurra as datas das sucessoras (dias úteis, em cascata).
- Login próprio (JWT + refresh token em cookie httpOnly) ou Clerk; papéis admin/gerente/membro e, em cada projeto, editor ou visualizador (vê, exporta e comenta).
- Importação de planilhas de até 100 mil linhas em segundo plano (fila Redis + RQ), com progresso ao vivo.
- Auditoria, limite de requisições, exportação para Excel, cálculo de prazos em dias úteis (Brasil API) e e-mails de projeto criado e de redefinição de senha (Resend).
- **Pilares** (TI, Suprimentos, CAPEX, Pessoas... e outros criados pelo editor): cada tarefa pode pertencer a um pilar;
  o andamento do pilar vem das tarefas, com farol (verde/amarelo/vermelho), texto de situação e orçado x realizado.
- **Linha do tempo com marcos**: pins, bandeiras, formas geométricas e períodos (ex.: pré-entrega, go-live,
  congelamento), com cor e descrição; o editor arrasta o marco para mudar a data.
- **Orçamento**: itens (orçado, CAPEX/OPEX, pilar) e lançamentos (realizado, com data, documento e estorno); saldo,
  execução, previsão de fechamento no ritmo atual, alertas de estouro, gráficos (curva acumulada x planejado e orçado x
  realizado por item, categoria ou pilar) e exportação para Excel. Pilar com itens passa a usar esse financeiro.
- **Andamento por botões** (0, 25, 50, 75 e 100%, com campo manual para outro valor) no diálogo da tarefa e direto
  na linha do cronograma, clicando na barra.
- **Anexos nas tarefas** (até 25 MB cada, 2 GB por projeto; programas e scripts recusados) e a aba **Arquivos** com o
  histórico de todos os arquivos do projeto, inclusive de tarefas já excluídas. Os arquivos entram no backup.
- **Gargalos**: dias de atraso acumulados por pessoa, área (pilar) e fase, pontualidade e as tarefas mais atrasadas.
- **Curva S** (aba própria): planejado x realizado acumulados semana a semana, **física** (% do cronograma, com as
  tarefas longas pesando mais; o realizado sai do histórico de andamento) e **financeira** (o orçado distribuído pela
  curva planejada x os lançamentos), com desvio em pontos e IDP (índice de desempenho de prazo).
- **Feedback de pessoas e áreas** (aba visível só para o dono do projeto e administradores): pontos fortes e pontos a
  melhorar calculados por regras a partir do cronograma (pontualidade, entregas antecipadas, atrasos em aberto,
  retrabalho, tarefas sem prazo, sobrecarga), sempre comparando com a média do projeto e só julgando com pelo menos
  3 entregas. Botão opcional "Sugerir texto com IA" (Groq), que recebe só os números, sem nomes nem títulos de tarefas.
- **Apresentação em PowerPoint** (.pptx) com **montador**: escolha os slides (capa, resumo, curva S, linha do tempo com
  marcos e bandeiras, marcos, pilares, orçamento, gargalos, pontos de atenção), a ordem e os títulos, escreva notas do
  apresentador e slides de texto próprios. Gráficos nativos (editáveis no PowerPoint). O admin pode enviar o **modelo
  da empresa** (.pptx ou .potx, sem macros, até 20 MB): a apresentação sai com o tema, as cores, as fontes e o tamanho
  de slide dele (16:9 ou 4:3).
- **Lembrete por e-mail** para quem tem tarefa atrasada: um e-mail por pessoa com todas as atrasadas, a cada 4 h,
  só em dias úteis das 8h às 18h. Cada pessoa pode silenciar por 24 h no menu do seu nome.
- **Relatório em PDF** do projeto: resumo e indicadores, pilares, orçamento, linha do tempo (Gantt com marcos),
  histórico e comentários recentes.

## Como rodar (passo a passo)

### 1. Pré-requisitos

| Ferramenta | Versão | macOS (Homebrew) |
|---|---|---|
| Python | 3.11 ou mais nova (o projeto usa **3.14**, ver `.python-version`) | `brew install python@3.14` |
| Node.js | 22 LTS ou mais nova | `brew install node` |
| PostgreSQL | 17 | `brew install postgresql@17 && brew services start postgresql@17` |
| Redis | 7 ou mais nova (fila de importação de planilhas) | `brew install redis && brew services start redis` |
| App autenticador | Google Authenticator, Microsoft Authenticator, 1Password… | no celular |

O app autenticador é necessário porque o admin é obrigado a ativar a verificação em duas etapas no primeiro login.

A API se recusa a subir em Python mais antigo que o 3.11.

### 2. Banco de dados (só na primeira vez)

O Postgres do Homebrew instala os comandos em `/opt/homebrew/opt/postgresql@17/bin`. Se `createdb`
não for encontrado, coloque essa pasta no PATH:
`echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc` e abra um terminal novo.

```bash
createuser -P pmo          # cria o usuário "pmo" e pede uma senha para ele
createdb -O pmo pmo        # cria o banco "pmo" com DONO "pmo"
```

O `-O pmo` é importante. Sem ele, o banco fica com outro dono e as migrations falham com
`permission denied for schema public`.

### 3. API (back-end)

Na pasta raiz do projeto:

```bash
python3.14 -m venv .venv
source .venv/bin/activate          # o prompt passa a mostrar (.venv)
pip install -r requirements-dev.lock   # versões exatas, conferidas por hash
cp .env.example .env
```

Abra o `.env` e preencha:

| Variável | O que colocar |
|---|---|
| `DATABASE_URL` | `postgresql+psycopg2://pmo:SENHA_DO_PASSO_2@localhost:5432/pmo` |
| `SECRET_KEY` | o resultado de `python -c "import secrets; print(secrets.token_urlsafe(64))"` |
| `CHAVE_2FA` | o resultado de `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"` |

O resto já vem pronto para desenvolvimento. As integrações (Groq, Resend, Clerk) são opcionais (veja
[Integrações](#integrações-opcionais)). O `.env` guarda segredos: nunca o envie para o git (ele já
está no `.gitignore`).

Depois:

```bash
alembic upgrade head                 # cria as tabelas no banco
python -m app.cli criar-admin --email admin@empresa.com.br --nome "Seu Nome"   # pede a senha (mín. 12 caracteres)
python -m uvicorn app.main:app --reload
```

A API fica em http://localhost:8000. Em `AMBIENTE=dev`, a documentação interativa fica em http://localhost:8000/docs.

**Worker da fila.** A importação de planilhas responde na hora e é processada em segundo plano.
Em **outro terminal**, deixe o worker rodando:

```bash
source .venv/bin/activate
python -m app.trabalhador      # processa as planilhas da fila (precisa do Redis no ar)
```

Sem Redis no seu computador, ponha `FILA_SINCRONA=true` no `.env`: a planilha é processada dentro da
própria API, sem worker. Use isso só em desenvolvimento, porque uma planilha grande volta a prender a requisição.

### 4. Front-end

Em **outro terminal** (a API continua rodando no primeiro):

```bash
cd frontend
npm install
cp .env.example .env      # já vem com VITE_API_URL=http://localhost:8000
npm run dev
```

### 5. Primeiro acesso

1. Abra **http://localhost:5173**. Use `localhost`, não `127.0.0.1`: o cookie de sessão só funciona com front e API no mesmo endereço.
2. Entre com o e-mail e a senha do admin criado no passo 3.
3. O sistema pede para ativar a verificação em duas etapas:
   - leia o QR code com o app autenticador e digite o código de 6 dígitos;
   - guarde os 10 códigos de recuperação (servem se você perder o celular).
4. Entre de novo, agora com senha e código.
5. Em **Usuários**, cadastre as outras pessoas como gerente ou membro. Cada uma troca a senha temporária no primeiro login.
6. Gerentes criam projetos em **Projetos → Novo projeto**: manualmente, por planilha ou com sugestão da IA.

### Dia a dia

Com tudo já instalado (Postgres e Redis ligados pelo `brew services`), abra três terminais:

```bash
# terminal 1 — API (na raiz do projeto)
source .venv/bin/activate
alembic upgrade head                     # só faz algo se vier migration nova do git
python -m uvicorn app.main:app --reload

# terminal 2 — worker da fila (importação de planilhas)
source .venv/bin/activate
python -m app.trabalhador

# terminal 3 — front
cd frontend && npm run dev
```

Depois de um `git pull`, rode também `pip install -r requirements-dev.lock` (API) e `npm install`
(front), caso alguma dependência tenha mudado.

### Ligar tudo sozinho no Mac (recomendado)

Em vez de abrir um Terminal para cada parte, instale a API, o worker, o vigia e os lembretes como
serviços do macOS: ligam sozinhos quando você entra no Mac e voltam sozinhos se caírem.

```bash
scripts/instalar-servicos.sh             # instala (rode de novo depois de atualizar o código)
scripts/instalar-servicos.sh --status    # mostra se cada parte está rodando
scripts/instalar-servicos.sh --remover   # desliga e remove
```

O site (front) continua com `cd frontend && npm run dev`. Registros em `logs/servico-*.log`.

### Lembretes de tarefas atrasadas

Outro processo separado (como o vigia). Precisa de `RESEND_API_KEY`; sem domínio verificado no Resend,
os e-mails só chegam no endereço da sua conta do Resend.

```bash
source .venv/bin/activate
python -m app.lembretes                              # fica ligado: confere a cada 15 min
python -m app.lembretes --uma-vez --ignorar-horario  # manda agora, para testar
```

### Vigia (agente que procura erros e falhas)

Processo separado que observa o sistema **rodando** e, a cada hora, gera um relatório. Ele só
identifica problemas: **nunca altera código nem dados**. Num terceiro terminal:

```bash
source .venv/bin/activate
python -m app.vigia            # fica ligado: uma rodada na partida e depois a cada 60 min
python -m app.vigia --uma-vez  # uma rodada agora e sai (para testar)
```

- **O que ele olha:**
  - o log da API (`logs/pmo.log`), acompanhado em tempo real com o watchdog: erros com o arquivo e a linha do código, respostas 5xx por rota, rotas lentas, picos de 429;
  - a saúde da API e se o banco está na migration mais nova;
  - a integridade dos dados, como fases fora de ordem, tarefa ligada a pilar de outro projeto ou dependência entre projetos;
  - alarmes de segurança (reuso de token, muitas senhas erradas), admins sem 2FA e espaço em disco.
- **IA (opcional):** com `GROQ_API_KEY` no `.env`, a Groq correlaciona os sinais e sugere a causa
  provável e como investigar. Antes, e-mails, IPs e tokens são trocados por marcadores. Sem a chave, o
  relatório sai só com as regras fixas.
- **Quem vê:** só os admins com e-mail em `VIGIA_EMAILS` (no `.env`, em JSON:
  `VIGIA_EMAILS=["voce@empresa.com.br"]`), no menu **Vigia**. Para qualquer outra pessoa a página
  não existe.
- **Uma instância só:** um segundo vigia no mesmo banco se recusa a subir (trava no Postgres). Se a
  página mostrar "Ele parece parado", o processo caiu.
- **Em produção:** rode como serviço (systemd, launchd, supervisor) ao lado da API, com o mesmo `.env`.

### Problemas comuns

| Sintoma | Causa e solução |
|---|---|
| `command not found: createdb` | O Postgres do Homebrew não está no PATH (passo 2). |
| `permission denied for schema public` | O banco foi criado sem `-O pmo`. Rode `dropdb pmo && createdb -O pmo pmo` e repita o `alembic upgrade head`. |
| `connection refused` na porta 5432 | O Postgres não está rodando: `brew services start postgresql@17`. |
| A API não sobe e reclama da versão do Python | O `.venv` foi criado com um Python antigo. Apague a pasta `.venv` e refaça com `python3.14`. |
| `Address already in use` (porta 8000 ou 5173) | Ainda tem uma API ou um front rodando em outro terminal. Feche-o, ou veja quem usa a porta com `lsof -i :8000`. |
| A página Vigia não aparece | Seu e-mail não está em `VIGIA_EMAILS` no `.env`, ou você não é admin. Reinicie a API depois de mudar o `.env`. |
| Importação fica "Na fila" e não anda | O worker não está rodando: `python -m app.trabalhador`. A página Vigia também avisa. |
| Importação responde 503 ("fila fora do ar") | O Redis não está rodando: `brew services start redis` (ou use `FILA_SINCRONA=true` em desenvolvimento). |
| Faz login e volta para a tela de login | Você abriu `127.0.0.1` em vez de `localhost`, ou o `CORS_ORIGENS` do `.env` não tem o endereço do front. |
| Erro de banco depois de um `git pull` | Chegou migration nova: rode `alembic upgrade head`. |
| Perdi o celular do 2FA | Use um código de recuperação ("Perdi o celular" na tela de login), ou peça a outro admin para redefinir o seu 2FA em **Usuários**. |
| Quero testar sem 2FA no meu computador | No `.env`, `EXIGIR_2FA_ADMIN=false` (só em desenvolvimento). |

## Testes

```bash
python -m pytest -q                                   # SQLite em memória (chaves estrangeiras ligadas)
python -m pytest tests/criticos -q                    # só os cenários críticos
TEST_DATABASE_URL=postgresql+psycopg2://... pytest -q  # Postgres descartável (as tabelas são apagadas!)
# Os testes de concorrência (tests/criticos/test_concorrencia.py) só rodam no Postgres; no SQLite são pulados.
```

As APIs externas são simuladas nos testes; nenhum teste acessa a rede.

Front-end:

```bash
cd frontend
npm run lint        # oxlint: inclui as regras de hooks do React (o ESLint não roda com o TypeScript 7)
npm test            # Vitest: funções e componentes
cd .. && PW_CHANNEL=chrome scripts/e2e.sh   # Playwright no seu Chrome: API num banco descartável
```

### Dependências

- **O que o projeto pede:** `requirements.txt` e `requirements-dev.txt`, com versões mínimas.
- **O que é instalado de verdade:** `requirements.lock` e `requirements-dev.lock`, com versões exatas e
  hash de cada pacote, válidos para macOS e Linux. O Docker e o CI instalam por eles.
- **Para atualizar:** `scripts/travar-dependencias.sh`. Ele só aceita versões publicadas há pelo menos
  14 dias, porque um pacote comprometido costuma ser descoberto em poucos dias. A exceção são as
  correções de segurança apontadas pelo `pip-audit`, listadas no próprio script.
- **No front,** as ferramentas novas foram fixadas em versões exatas com a mesma regra dos 14 dias.

```
tests/
├── conftest.py                      # banco limpo por teste, client, criar_usuario, login
├── criticos/                        # cenários críticos (casos limite)
│   ├── conftest.py                  # mocks de Brasil API / Groq / Clerk, helpers de upload e de banco
│   ├── test_importacao_resiliencia.py
│   ├── test_apis_externas.py
│   ├── test_autenticacao_estrita.py # inclui o inventário: toda rota nova nasce exigindo token
│   ├── test_waterfall.py            # confere as 5 fases direto no banco
│   ├── test_idor.py                 # token válido, projeto alheio: 404 em toda rota, banco intacto
│   ├── test_concorrencia.py         # 5 requisições simultâneas (Postgres): versão, idempotência, 2FA
│   ├── test_consultas_n_mais_1.py   # nº de queries não cresce com a quantidade de dados
│   ├── test_fuso_horario.py         # datas civis sem fuso, "hoje" em São Paulo, instantes em UTC
│   ├── test_caractere_nul.py        # NUL no texto (o Postgres recusa) vira 422, não erro 500
│   └── test_fuzz_rotas.py           # fuzzing de todas as rotas: nenhuma entrada absurda vira 500
├── test_pilares.py                  # pilares: andamento, farol, financeiro, planilha, permissões
├── test_relatorio_pdf.py            # PDF: conteúdo, segurança da marcação, projetos grandes
├── test_vigia.py                    # vigia: log, watchdog, regras, sondas, IA, quem vê os relatórios
├── test_importacao_fila.py          # fila: 202 na hora, worker, progresso, falhas, 100 mil linhas
├── test_projeto_grande.py           # projeto grande: detalhe paginado, indicadores no SQL
├── test_bordas.py                   # casos de borda encontrados na caça a bugs
├── test_seguranca.py, test_contas.py, test_dois_fatores.py, test_robustez.py
├── test_permissoes.py, test_cronograma.py, test_operacao.py
└── test_integracoes.py
```

## Integrações (opcionais)

Sem a variável, a integração fica desligada. Detalhes em `.env.example`.

| Variável | Para quê |
|---|---|
| `CLERK_EMISSOR` | Aceitar o token do Clerk (exige o claim `primaryEmail` no session token) |
| `GROQ_API_KEY`, `GROQ_MODELO` | `POST /projetos/ia` |
| `RESEND_API_KEY`, `EMAIL_REMETENTE` | E-mails de projeto criado e de "Esqueci minha senha" |
| `FRONTEND_URL` | Endereço do site nos links dos e-mails (padrão: a 1ª de `CORS_ORIGENS`; em produção, `https://`) |
| `REDEFINICAO_SENHA_MINUTOS` | Validade do link de redefinição de senha (padrão: 30) |

## Segurança

- Senhas: Argon2id; mínimo de 12 caracteres; recusa senhas previsíveis, com nome/e-mail ou vazadas (HIBP).
- Sessão: JWT de 15 min + refresh token rotativo em cookie httpOnly; trocar a senha ou "encerrar sessões" invalida na hora até os tokens já emitidos.
- Senha definida pelo admin (cadastro ou redefinição) obriga a troca no próximo login.
- "Esqueci minha senha": link por e-mail, válido por 30 min e uma única vez (morre também se a senha mudar
  por outro caminho). A resposta é igual para qualquer e-mail, no máximo 3 links por hora para o mesmo endereço.
  Redefinir encerra todas as sessões e desbloqueia a conta; o 2FA continua valendo. Sem `RESEND_API_KEY`,
  em desenvolvimento, o link aparece no log da API (em produção, nunca).
- Verificação em duas etapas (TOTP, app autenticador) com 10 códigos de recuperação de uso único.
  Obrigatória para admin com login local; opcional para os demais. O segredo fica cifrado no banco
  (`CHAVE_2FA`, obrigatória em produção — não a troque depois que alguém ativar o 2FA).
- Login: mesma resposta para qualquer falha; bloqueio por e-mail + IP, por IP (muitas contas) e por conta.
  Logins certos não contam como falha: um escritório inteiro atrás do mesmo IP entra normalmente.
- Limites de IA e importação são por usuário (colegas no mesmo IP não dividem a cota).
- Várias abas abertas: a renovação de sessão é serializada entre abas e uma corrida não é confundida com roubo de token.
- Requisições limitadas a 1 MB (upload de planilha: 5 MB), inclusive envios em partes.
- Verifique CVEs nas dependências: `python -m pip_audit` e `cd frontend && npm audit`.

## Backup do banco

```bash
python -m app.backup                         # um backup agora (backups/pmo-AAAAMMDD-HHMMSS.dump)
python -m app.backup --testar-restauracao    # e restaura num banco temporário para conferir
python -m app.backup --listar
scripts/instalar-backup.sh                   # macOS: todo dia às 02:00, com teste de restauração
```

- **Como é feito:** `pg_dump` em formato compactado. O arquivo só vale depois de conferido, fica legível
  só pelo dono e os 14 mais recentes são mantidos (`BACKUP_RETENCAO`).
- **Cópia fora do disco:** um backup no mesmo disco do banco não sobrevive ao disco. Configure
  `BACKUP_COPIA_DIR` com outro disco ou uma pasta sincronizada com a nuvem.
- **Vigia:** avisa se o último backup tiver mais de 26 h ou se não houver nenhum. A limpeza do disco
  **nunca** apaga backups.
- **Restaurar de verdade** (substitui os dados; pare a API antes):
  `pg_restore --clean --if-exists --no-owner -d "<banco>" backups/pmo-AAAAMMDD-HHMMSS.dump`

## Produção (servidor com Docker)

Tudo sobe com um comando: Postgres, Redis, API, worker da fila, vigia, backup diário e o proxy Caddy,
que cuida do **HTTPS automático** (Let's Encrypt).

```bash
cp deploy/.env.producao.exemplo deploy/.env.producao    # preencha: DOMINIO, senhas e chaves
docker compose --env-file deploy/.env.producao -f deploy/docker-compose.yml up -d --build
docker compose --env-file deploy/.env.producao -f deploy/docker-compose.yml exec api \
  python -m app.cli criar-admin --email voce@empresa.com.br --nome "Seu Nome"
```

- **Pré-requisitos:** o DNS de `DOMINIO` aponta para o servidor, e as portas 80 e 443 estão abertas.
- **Endereços:** o front fica em `https://DOMINIO` e a API em `https://DOMINIO/api`. Por isso
  `COOKIE_PATH=/api/auth` (já está no exemplo).
- **Atualizar:** `git pull` e o mesmo `up -d --build`. As migrations rodam sozinhas antes da API subir.
- **Volumes que guardam dados:** `pgdata` (banco), `backups`, `caddy_data` (certificados). Não apague.
- **O CI confere o deploy:** a cada push, ele constrói as imagens, sobe tudo com HTTPS local e testa
  login, cookie, worker, vigia e backup.

## CI

`.github/workflows/ci.yml` roda a cada push e pull request:
- **Back-end:** ruff, bandit, pip-audit, testes no SQLite (cobertura mínima de 85%) e no Postgres
  (incluindo concorrência e backup), e todas as migrations descendo e subindo do zero.
- **Front-end:** tipos, build e npm audit.
- **Deploy:** imagens Docker e subida completa.

## Manutenção

### Limpeza do disco

Quando o disco fica abaixo de `LIMPEZA_MIN_GB` livres (padrão 10 GB), `python -m app.limpeza` apaga só
o que é **recriável**:

- logs rotacionados antigos;
- relatórios do vigia e registros de importação com mais de 90 dias;
- refresh tokens vencidos;
- com `--caches-de-desenvolvimento`, os caches do pip, do npm e do Homebrew.

Ela **nunca** toca na Lixeira, em Downloads ou em arquivos pessoais.

```bash
python -m app.limpeza --simular   # mostra o que apagaria, sem apagar
scripts/instalar-limpeza.sh       # macOS: confere o disco a cada hora (launchd) e avisa por notificação
scripts/instalar-limpeza.sh --remover
```

Cada limpeza aparece também na página **Vigia**. Se o disco continuar baixo depois dela, o que está
enchendo não é recriável: veja a Lixeira e a pasta Downloads.


Agende diariamente: `python -m app.cli limpar-tokens` (remove refresh tokens expirados).
