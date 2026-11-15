#!/bin/bash
# Testes no navegador (Playwright): sobe uma API num banco SQLite DESCARTÁVEL e roda frontend/e2e.
#
#   scripts/e2e.sh                 # usa o Chromium do Playwright (npx playwright install chromium)
#   PW_CHANNEL=chrome scripts/e2e.sh   # usa o Google Chrome instalado (sem baixar navegador)
#   scripts/e2e.sh --headed        # argumentos extras vão para o playwright test
#
# Portas próprias (API 8100, front 5180): não colide com a API/front que você estiver rodando.
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
PY="${PYTHON:-$RAIZ/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"
TEMP="$(mktemp -d)"
API_PID=""
encerrar() { [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true; rm -rf "$TEMP"; }
trap encerrar EXIT

export DATABASE_URL="sqlite:///$TEMP/pmo-e2e.db" SECRET_KEY="chave-so-para-o-e2e-$(date +%s)-xxxxxxxxxxxxxxxxxxxxxxxx"
export AMBIENTE=dev COOKIE_SECURE=false COOKIE_PATH=/auth CORS_ORIGENS='["http://localhost:5180"]'
export FILA_SINCRONA=true LIMITES_BACKEND=memoria EXIGIR_2FA_ADMIN=false VERIFICAR_SENHAS_VAZADAS=false
export LOG_ARQUIVO= LOG_ARQUIVO_TRABALHADOR= LOG_ARQUIVO_BACKUP= LOG_ARQUIVO_LIMPEZA= BACKUP_DIR=
export GROQ_API_KEY= RESEND_API_KEY= VIGIA_EMAILS='[]'
export PYTHONPATH="$RAIZ${PYTHONPATH:+:$PYTHONPATH}"

cd "$RAIZ"
"$PY" scripts/semear_e2e.py
"$PY" -m uvicorn app.main:app --port 8100 --log-level warning > "$TEMP/api.log" 2>&1 &
API_PID=$!
for _ in $(seq 1 60); do curl -sf http://localhost:8100/saude >/dev/null && break; sleep 0.5; done
curl -sf http://localhost:8100/saude >/dev/null || { cat "$TEMP/api.log"; echo "A API do E2E não subiu"; exit 1; }

cd frontend
# Sem Resend, em dev, o link de "Esqueci minha senha" sai no log da API: o teste o lê dali.
E2E_API_LOG="$TEMP/api.log" VITE_API_URL=http://localhost:8100 npx playwright test "$@"
