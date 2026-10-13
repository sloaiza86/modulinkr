#!/usr/bin/env bash
set -euo pipefail
ml_stage="$(cd "$(dirname "$0")" && pwd)"
ml_service="$(systemctl show modulinkr-gateway.service --property=WorkingDirectory --value)"
ml_web="$(systemctl show modulinkr-web.service --property=WorkingDirectory --value)"
test -f "$ml_service/gateway_service.py"
test -f "$ml_web/web_service.py"
test "$(realpath "$ml_service/..")" = "$(realpath "$ml_web/..")"
python3 - "$ml_stage" <<'PY'
import ast
from pathlib import Path
import sys
for path in Path(sys.argv[1]).glob('gateway/*/*.py'):
    ast.parse(path.read_text(),filename=str(path))
    print(f'Sintaxis: {path.name}')
PY
ml_backup="$(mktemp -d)"
ml_files=(
  pi-service/buffer.py pi-service/protocol.py pi-service/modbus_command.py
  pi-web/commandapi.py pi-web/static/app.js pi-web/static/components.js
  pi-web/static/index.html pi-web/static/style.css
)
for ml_file in "${ml_files[@]}"; do
  ml_relative="${ml_file#*/}"
  case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
  mkdir -p "$ml_backup/$(dirname "$ml_file")"
  if [ -f "$ml_target" ]; then cp -p "$ml_target" "$ml_backup/$ml_file"; fi
  test -f "$ml_stage/gateway/$ml_file"
done
ml_installed=0
restore_operations() {
  ml_exit=$?
  trap - EXIT
  if [ "$ml_installed" -eq 0 ]; then
    for ml_file in "${ml_files[@]}"; do
      ml_relative="${ml_file#*/}"
      case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
      if [ -f "$ml_backup/$ml_file" ]; then sudo install -v -m 0644 "$ml_backup/$ml_file" "$ml_target"; else sudo rm -f "$ml_target"; fi
    done
    sudo systemctl start modulinkr-gateway.service modulinkr-web.service || true
  fi
  exit "$ml_exit"
}
trap restore_operations EXIT
sudo systemctl stop modulinkr-web.service modulinkr-gateway.service
for ml_file in "${ml_files[@]}"; do
  ml_relative="${ml_file#*/}"
  case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
  sudo install -v -m 0644 "$ml_stage/gateway/$ml_file" "$ml_target"
done
sudo systemctl start modulinkr-gateway.service modulinkr-web.service
sleep 3
sudo systemctl is-active --quiet modulinkr-gateway.service
sudo systemctl is-active --quiet modulinkr-web.service
ml_installed=1
printf 'Servicios activos: modulinkr-gateway.service, modulinkr-web.service\nCopia previa: %s\n' "$ml_backup"
