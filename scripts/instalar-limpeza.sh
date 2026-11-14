#!/bin/bash
# Agenda no macOS (launchd) a limpeza do disco a cada hora: python -m app.limpeza --caches-de-desenvolvimento
# Ela só age abaixo de LIMPEZA_MIN_GB livres (padrão 10 GB) e só apaga o que é recriável (veja app/limpeza.py).
#
#   scripts/instalar-limpeza.sh            # instala (ou reinstala) e já roda uma vez
#   scripts/instalar-limpeza.sh --remover  # desliga e remove o agendamento
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
ROTULO="com.pmo.limpeza-disco"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"
ALVO="gui/$(id -u)"

if [ "${1:-}" = "--remover" ]; then
  launchctl bootout "$ALVO/$ROTULO" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Limpeza automática removida."
  exit 0
fi

[ -x "$RAIZ/.venv/bin/python" ] || { echo "Crie o .venv antes (veja o README)."; exit 1; }
mkdir -p "$RAIZ/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key>
  <array>
    <string>$RAIZ/.venv/bin/python</string><string>-m</string><string>app.limpeza</string>
    <string>--caches-de-desenvolvimento</string>
  </array>
  <key>WorkingDirectory</key><string>$RAIZ</string>
  <key>StartInterval</key><integer>3600</integer>
  <key>RunAtLoad</key><true/>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string></dict>
  <key>StandardOutPath</key><string>$RAIZ/logs/limpeza-agendada.log</string>
  <key>StandardErrorPath</key><string>$RAIZ/logs/limpeza-agendada.log</string>
</dict>
</plist>
PLIST
launchctl bootout "$ALVO/$ROTULO" 2>/dev/null || true
launchctl bootstrap "$ALVO" "$PLIST"
echo "Limpeza agendada a cada hora ($ROTULO). Registro: $RAIZ/logs/limpeza-agendada.log"
