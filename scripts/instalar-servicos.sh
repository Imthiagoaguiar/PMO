#!/bin/bash
# Instala no macOS (launchd) as quatro partes do PMO como serviços automáticos do seu usuário:
#   API (porta 8000), worker da fila, vigia e lembretes de tarefas atrasadas.
# Cada uma liga sozinha quando você entra no Mac e volta sozinha se cair (ou travar e sair).
# O Postgres e o Redis já ligam sozinhos pelo "brew services".
#
#   scripts/instalar-servicos.sh             # instala (ou reinstala depois de atualizar o código)
#   scripts/instalar-servicos.sh --status    # mostra se cada um está rodando
#   scripts/instalar-servicos.sh --remover   # desliga e remove os quatro
#
# Registros: logs/servico-<nome>.log (além dos logs de sempre: logs/pmo.log, logs/vigia-agente.log...).
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
ALVO="gui/$(id -u)"
PASTA="$HOME/Library/LaunchAgents"
SERVICOS=(api trabalhador vigia lembretes)

argumentos() {
  case "$1" in
    # --no-access-log: cada requisição já vai para logs/pmo.log (com rotação); sem isto ela era gravada
    # de novo em logs/servico-api.log, que cresce sem limite.
    api) echo "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log" ;;
    trabalhador) echo "-m app.trabalhador" ;;
    vigia) echo "-m app.vigia" ;;
    lembretes) echo "-m app.lembretes" ;;
  esac
}

if [ "${1:-}" = "--status" ]; then
  for nome in "${SERVICOS[@]}"; do
    linha=$(launchctl list | awk -v r="com.pmo.$nome" '$3 == r')
    if [ -z "$linha" ]; then echo "$nome: não instalado"
    else
      pid=$(echo "$linha" | awk '{print $1}'); saida=$(echo "$linha" | awk '{print $2}')
      [ "$pid" != "-" ] && echo "$nome: rodando (pid $pid)" || echo "$nome: parado (última saída: $saida) — veja logs/servico-$nome.log"
    fi
  done
  curl -sf -m 3 http://localhost:8000/saude >/dev/null && echo "API respondendo em http://localhost:8000" || echo "API ainda não responde"
  exit 0
fi

if [ "${1:-}" = "--remover" ]; then
  for nome in "${SERVICOS[@]}"; do
    launchctl bootout "$ALVO/com.pmo.$nome" 2>/dev/null || true
    rm -f "$PASTA/com.pmo.$nome.plist"
  done
  echo "Serviços do PMO removidos. Para usar de novo: scripts/instalar-servicos.sh (ou os comandos do README)."
  exit 0
fi

[ -x "$RAIZ/.venv/bin/python" ] || { echo "Crie o .venv antes (veja o README)."; exit 1; }
[ -f "$RAIZ/.env" ] || { echo "Falta o .env na raiz do projeto (veja o README)."; exit 1; }
mkdir -p "$RAIZ/logs" "$RAIZ/anexos" "$PASTA"

# Processos ligados à mão (Terminal, nohup) ocupariam a porta 8000 ou a trava do vigia: saem antes.
for padrao in "uvicorn app.main:app --port 8000" "uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log" \
              "-m app.trabalhador" "-m app.vigia" "-m app.lembretes"; do
  for pid in $(pgrep -f -- "$padrao" || true); do
    if ! launchctl list | awk '{print $1}' | grep -qx "$pid"; then kill -TERM "$pid" 2>/dev/null || true; fi
  done
done
sleep 2

for nome in "${SERVICOS[@]}"; do
  rotulo="com.pmo.$nome"
  plist="$PASTA/$rotulo.plist"
  args=""
  for a in $(argumentos "$nome"); do args="$args<string>$a</string>"; done
  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$rotulo</string>
  <key>ProgramArguments</key>
  <array><string>$RAIZ/.venv/bin/python</string>$args</array>
  <key>WorkingDirectory</key><string>$RAIZ</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>ProcessType</key><string>Background</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/opt/postgresql@17/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
  </dict>
  <key>StandardOutPath</key><string>$RAIZ/logs/servico-$nome.log</string>
  <key>StandardErrorPath</key><string>$RAIZ/logs/servico-$nome.log</string>
</dict>
</plist>
PLIST
  launchctl bootout "$ALVO/$rotulo" 2>/dev/null || true
  launchctl bootstrap "$ALVO" "$plist"
done

for _ in $(seq 1 30); do curl -sf -m 2 http://localhost:8000/saude >/dev/null && break; sleep 1; done
echo "Serviços instalados: ${SERVICOS[*]} (ligam sozinhos ao entrar no Mac e voltam se caírem)."
"$0" --status
