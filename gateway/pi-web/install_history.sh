#!/usr/bin/env bash
set -euo pipefail
ml_stage="$(cd "$(dirname "$0")" && pwd)"
ml_web="$(systemctl show modulinkr-web.service --property=WorkingDirectory --value)"
test -f "$ml_web/web_service.py"
ml_files=(dataapi.py static/app.js static/components.js static/index.html static/style.css)
python3 - "$ml_stage/gateway/pi-web/dataapi.py" <<'PY'
import ast
from pathlib import Path
import sys
path = Path(sys.argv[1])
ast.parse(path.read_text(), filename=str(path))
print(f'Sintaxis: {path.name}')
PY
ml_backup="$(mktemp -d)"
for ml_file in "${ml_files[@]}"; do
  test -f "$ml_stage/gateway/pi-web/$ml_file"
  test -f "$ml_web/$ml_file"
  mkdir -p "$ml_backup/$(dirname "$ml_file")"
  cp -p "$ml_web/$ml_file" "$ml_backup/$ml_file"
done
ml_installed=0
restore_history() {
  ml_exit=$?
  trap - EXIT
  if [ "$ml_installed" -eq 0 ]; then
    for ml_file in "${ml_files[@]}"; do
      sudo install -v -m 0644 "$ml_backup/$ml_file" "$ml_web/$ml_file"
    done
    sudo systemctl start modulinkr-web.service || true
  fi
  exit "$ml_exit"
}
trap restore_history EXIT
sudo systemctl stop modulinkr-web.service
for ml_file in "${ml_files[@]}"; do
  sudo install -v -m 0644 "$ml_stage/gateway/pi-web/$ml_file" "$ml_web/$ml_file"
done
sudo systemctl start modulinkr-web.service
sleep 3
sudo systemctl is-active --quiet modulinkr-web.service
ml_installed=1
printf 'Servicio activo: modulinkr-web.service\nCopia previa: %s\n' "$ml_backup"
