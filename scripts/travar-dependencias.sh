#!/bin/bash
# Gera requirements.lock e requirements-dev.lock: versões exatas + hashes, válidos para macOS e Linux.
#
# Regra: só versões publicadas há pelo menos 14 dias (um pacote comprometido costuma ser descoberto
# e retirado em poucos dias). EXCEÇÃO: correções de segurança apontadas pelo pip-audit entram na hora,
# listadas em EXCECOES_DE_SEGURANCA abaixo (pacote=data de hoje).
#
#   scripts/travar-dependencias.sh        # depois: pip install -r requirements-dev.lock
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
cd "$RAIZ"
UV="${UV:-.venv/bin/uv}"
CORTE="$(date -v-14d +%Y-%m-%d 2>/dev/null || date -d '14 days ago' +%Y-%m-%d)"
EXCECOES_DE_SEGURANCA=(
  "dnspython=2026-11-14"  # CVE-2026-128089, corrigida na 2.9.2
)
ARGS=(--universal --python-version 3.14 --generate-hashes --no-header --exclude-newer "$CORTE")
for excecao in "${EXCECOES_DE_SEGURANCA[@]}"; do
  ARGS+=(--exclude-newer-package "$excecao")
done
export UV_HTTP_TIMEOUT="${UV_HTTP_TIMEOUT:-120}"
"$UV" pip compile requirements.txt "${ARGS[@]}" -o requirements.lock
"$UV" pip compile requirements-dev.txt "${ARGS[@]}" -c requirements.lock -o requirements-dev.lock
echo "Locks gerados (corte: $CORTE). Confira as vulnerabilidades: pip-audit -r requirements.lock"
