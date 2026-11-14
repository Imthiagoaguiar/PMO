#!/bin/bash
# Agenda no macOS (launchd) o backup diário do banco às 02:00 (se o Mac estiver dormindo, roda ao acordar):
#   python -m app.backup [--testar-restauracao]
# O teste de restauração usa o seu usuário do macOS pelo socket local do Postgres (Homebrew), só para
# criar e apagar um banco temporário: o usuário do app, de propósito, não pode criar bancos.
#
#   scripts/instalar-backup.sh            # instala (ou reinstala)
#   scripts/instalar-backup.sh --remover  # desliga e remove o agendamento
set -euo pipefail
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"
ROTULO="com.pmo.backup"
PLIST="$HOME/Library/LaunchAgents/$ROTULO.plist"
ALVO="gui/$(id -u)"

if [ "${1:-}" = "--remover" ]; then
  launchctl bootout "$ALVO/$ROTULO" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Backup automático removido (os arquivos em backups/ continuam lá)."
  exit 0
fi

[ -x "$RAIZ/.venv/bin/python" ] || { echo "Crie o .venv antes (veja o README)."; exit 1; }
PSQL="/opt/homebrew/opt/postgresql@17/bin/psql"
EXTRA="" ; AMBIENTE_ADMIN=""
if [ -x "$PSQL" ] && "$PSQL" -tAc "select 1" postgres >/dev/null 2>&1; then
  EXTRA="<string>--testar-restauracao</string>"
  AMBIENTE_ADMIN="<key>BACKUP_ADMIN_URL</key><string>postgresql+psycopg2:///postgres</string>"
fi
mkdir -p "$RAIZ/logs" "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$ROTULO</string>
  <key>ProgramArguments</key>
  <array>
    <string>$RAIZ/.venv/bin/python</string><string>-m</string><string>app.backup</string>
    $EXTRA
  </array>
  <key>WorkingDirectory</key><string>$RAIZ</string>
  <key>StartCalendarInterval</key>
  <dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>
  <key>Nice</key><integer>10</integer>
  <key>LowPriorityIO</key><true/>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key><string>/opt/homebrew/opt/postgresql@17/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    $AMBIENTE_ADMIN
  </dict>
  <key>StandardOutPath</key><string>$RAIZ/logs/backup-agendado.log</string>
  <key>StandardErrorPath</key><string>$RAIZ/logs/backup-agendado.log</string>
</dict>
</plist>
PLIST
launchctl bootout "$ALVO/$ROTULO" 2>/dev/null || true
launchctl bootstrap "$ALVO" "$PLIST"
echo "Backup agendado todo dia às 02:00 ($ROTULO)${EXTRA:+, com teste de restauração}. Registro: $RAIZ/logs/backup-agendado.log"
