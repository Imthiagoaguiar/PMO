#!/bin/bash
# Teste de carga completo num ambiente DESCARTÁVEL (não toca no seu banco nem na API da porta 8000):
#   - banco Postgres "pmo_carga" (criado e apagado aqui), Redis no banco 2, API na porta 8200
#   - API com WEB_CONCURRENCY processos (padrão 2, como em produção) + 1 worker da fila
#   - monitora conexões do Postgres e CPU; no fim, uma rodada do vigia olhando este ambiente
#
#   scripts/carga.sh                                   # 400 usuários, 2000 projetos, fases 50/150/300
#   USUARIOS=1000 PROJETOS=5000 FASES=100:60,500:60 WEB_CONCURRENCY=4 scripts/carga.sh
#
# Precisa de: Postgres e Redis no ar (brew services) e o .env com DATABASE_URL (usa o mesmo usuário).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
PY="$RAIZ/.venv/bin/python"
PGB="${PG_BIN_DIR:-/opt/homebrew/opt/postgresql@17/bin}"
USUARIOS="${USUARIOS:-400}"; PROJETOS="${PROJETOS:-2000}"; TAREFAS="${TAREFAS:-25}"
FASES="${FASES:-50:60,150:60,300:60}"; WEB_CONCURRENCY="${WEB_CONCURRENCY:-2}"
SAIDA="${SAIDA:-$RAIZ/logs/carga-$(date +%Y%m%d-%H%M%S)}"
mkdir -p "$SAIDA"

BASE_URL="$(grep '^DATABASE_URL=' .env | cut -d= -f2-)"
DONO="$(echo "$BASE_URL" | sed -E 's#^[^:]+://([^:@/]+).*#\1#')"
export DATABASE_URL="$(echo "$BASE_URL" | sed -E 's#/[^/]+$#/pmo_carga#')"
export REDIS_URL="redis://localhost:6379/2" LIMITES_BACKEND=redis FILA_SINCRONA=false
export LOG_ARQUIVO="$SAIDA/api.log" LOG_ARQUIVO_TRABALHADOR="$SAIDA/trabalhador.log" LOG_ARQUIVO_LIMPEZA= LOG_ARQUIVO_BACKUP=
export BACKUP_DIR= RESEND_API_KEY= CLERK_EMISSOR= VERIFICAR_SENHAS_VAZADAS=false EXIGIR_2FA_ADMIN=false
export VIGIA_API_URL="http://localhost:8200" PYTHONPATH="$RAIZ"
PIDS=()

limpar() {
  for p in "${PIDS[@]:-}"; do [ -n "$p" ] && kill "$p" 2>/dev/null || true; done
  sleep 2
  "$PGB/dropdb" -h localhost --if-exists pmo_carga 2>/dev/null && echo "pmo_carga apagado" || true
  redis-cli -n 2 FLUSHDB >/dev/null 2>&1 || true
}
trap limpar EXIT

echo "== Banco descartável pmo_carga (resultados em $SAIDA)"
"$PGB/dropdb" -h localhost --if-exists pmo_carga
"$PGB/createdb" -h localhost -O "$DONO" pmo_carga
"$PGB/psql" -h localhost -d pmo_carga -qc "ALTER SCHEMA public OWNER TO \"$DONO\""
redis-cli -n 2 FLUSHDB >/dev/null
"$PY" -m alembic upgrade head 2>&1 | tail -1
"$PY" scripts/carga.py semear --usuarios "$USUARIOS" --projetos "$PROJETOS" --tarefas "$TAREFAS"

echo "== API ($WEB_CONCURRENCY processos) na porta 8200 + worker"
"$PY" -m uvicorn app.main:app --port 8200 --workers "$WEB_CONCURRENCY" --log-level warning \
  --proxy-headers --forwarded-allow-ips 127.0.0.1 > "$SAIDA/uvicorn.txt" 2>&1 &
PIDS+=($!)
"$PY" -m app.trabalhador > "$SAIDA/trabalhador.txt" 2>&1 &
PIDS+=($!)
for _ in $(seq 1 60); do curl -sf http://localhost:8200/saude >/dev/null && break; sleep 0.5; done
curl -sf http://localhost:8200/saude || { cat "$SAIDA/uvicorn.txt"; exit 1; }
echo

# Monitor: conexões do Postgres (por estado) e CPU/memória dos processos da API, a cada 2 s.
(
  echo "hora conexoes_ativas conexoes_ociosas aguardando_lock cpu_api_% mem_api_MB"
  while true; do
    linha=$("$PGB/psql" -h localhost -d pmo_carga -Atc "select count(*) filter (where state='active'), count(*) filter (where state like 'idle%'), count(*) filter (where wait_event_type='Lock') from pg_stat_activity where datname='pmo_carga'" 2>/dev/null | tr '|' ' ')
    uso=$(ps -A -o pid=,ppid=,%cpu=,rss=,command= | awk '/uvicorn app.main:app --port 8200|multiprocessing.spawn|resource_tracker/ {c+=$3; m+=$4} END {printf "%.0f %.0f", c, m/1024}')
    echo "$(date +%H:%M:%S) $linha $uso"
    sleep 2
  done
) > "$SAIDA/monitor.txt" &
PIDS+=($!)

echo "== Carga: fases $FASES (cada usuário virtual com o próprio IP)"
"$PY" scripts/carga.py rodar --url http://localhost:8200 --fases "$FASES" --usuarios "$USUARIOS" | tee "$SAIDA/resultado.txt"

echo "== Escritório atrás de um IP só: 120 logins juntos"
"$PY" scripts/carga.py login-mesmo-ip --url http://localhost:8200 --quantos 120 --usuarios "$USUARIOS" | tee -a "$SAIDA/resultado.txt"

echo "== Pico do monitor"
sort -k2 -n "$SAIDA/monitor.txt" | tail -1 | sed 's/^/conexões ativas no pico: /'
awk 'NR>1 && $5>m {m=$5; l=$0} END {print "CPU da API no pico: " l}' "$SAIDA/monitor.txt"

echo "== Vigia olhando este ambiente"
"$PY" -m app.vigia --uma-vez 2>&1 | tail -3 | tee -a "$SAIDA/resultado.txt"
"$PY" -c "
from app.database import SessionLocal
from app.models import VigiaRodada
from sqlalchemy import select
with SessionLocal() as db:
    r = db.scalar(select(VigiaRodada).order_by(VigiaRodada.id.desc()))
    for a in r.achados: print(' -', a['gravidade'], a['origem'], '|', a['titulo'], '|', '; '.join(a['evidencias'])[:200])
" | tee -a "$SAIDA/resultado.txt"
