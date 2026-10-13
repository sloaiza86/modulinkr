#!/usr/bin/env bash
# Actualiza el visor y el registro de capacidad del gateway.
set -euo pipefail
ml_stage="$(cd "$(dirname "$0")/../.." && pwd)"
ml_service="$(systemctl show modulinkr-gateway.service --property=WorkingDirectory --value)"
ml_web="$(systemctl show modulinkr-web.service --property=WorkingDirectory --value)"
test -f "$ml_service/gateway_service.py"
test -f "$ml_web/web_service.py"
ml_files=(
  pi-service/buffer.py
  pi-web/web_service.py pi-web/systemapi.py pi-web/supervision.py
  pi-web/static/index.html pi-web/static/style.css pi-web/static/app.js pi-web/static/supervision.js
)
python3 - "$ml_stage" <<'PY'
import ast
from pathlib import Path
import sys
for name in ('gateway/pi-service/buffer.py', 'gateway/pi-web/web_service.py',
             'gateway/pi-web/systemapi.py', 'gateway/pi-web/supervision.py'):
    path = Path(sys.argv[1])/name
    ast.parse(path.read_text(), filename=str(path))
    print('Sintaxis:', name)
PY
ml_backup="$(mktemp -d)"
for ml_file in "${ml_files[@]}"; do
  ml_relative="${ml_file#*/}"
  case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
  test -f "$ml_stage/gateway/$ml_file"
  mkdir -p "$ml_backup/$(dirname "$ml_file")"
  if [ -f "$ml_target" ]; then cp -p "$ml_target" "$ml_backup/$ml_file"; fi
done
ml_installed=0
restore_supervision() {
  ml_exit=$?
  trap - EXIT
  if [ "$ml_installed" -eq 0 ]; then
    sudo systemctl stop modulinkr-web.service modulinkr-gateway.service || true
    for ml_file in "${ml_files[@]}"; do
      ml_relative="${ml_file#*/}"
      case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
      if [ -f "$ml_backup/$ml_file" ]; then sudo install -v -m 0644 "$ml_backup/$ml_file" "$ml_target"; else sudo rm -f "$ml_target"; fi
    done
    sudo systemctl start modulinkr-gateway.service modulinkr-web.service || true
  fi
  exit "$ml_exit"
}
trap restore_supervision EXIT
sudo systemctl stop modulinkr-web.service modulinkr-gateway.service
for ml_file in "${ml_files[@]}"; do
  ml_relative="${ml_file#*/}"
  case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
  sudo install -v -m 0644 "$ml_stage/gateway/$ml_file" "$ml_target"
done
sudo systemctl start modulinkr-gateway.service modulinkr-web.service
sudo python3 - <<'PY'
from pathlib import Path
import ssl
import subprocess
import time
from urllib.request import urlopen

def ready(timeout=60):
    deadline = time.monotonic() + timeout
    previous = None
    successes = 0
    while time.monotonic() < deadline:
        pid = subprocess.check_output(['systemctl', 'show', 'modulinkr-web.service',
                                      '--property=MainPID', '--value'], text=True).strip()
        try:
            args = Path('/proc/' + pid + '/cmdline').read_bytes().decode().split('\0')
            port = int(args[args.index('--port') + 1])
            scheme = 'https' if '--ssl-certfile' in args else 'http'
            if not 1 <= port <= 65535:
                raise ValueError('Puerto no válido')
            url = scheme + '://127.0.0.1:' + str(port) + '/login'
            context = ssl._create_unverified_context() if scheme == 'https' else None
            with urlopen(url, context=context, timeout=2) as response:
                if response.status != 200:
                    raise ValueError('Respuesta HTTP no válida')
            successes = successes + 1 if pid == previous else 1
            previous = pid
            if successes >= 3:
                print('Visor comprobado:', url, 'PID:', pid)
                return True
        except (OSError, ValueError):
            previous = None
            successes = 0
        time.sleep(2)
    return False

if not ready():
    raise SystemExit('El visor no respondió de forma estable dentro de 60 segundos.')
PY
sudo systemctl is-active --quiet modulinkr-gateway.service
sudo systemctl is-active --quiet modulinkr-web.service
ml_installed=1
printf 'Servicios activos: modulinkr-gateway.service, modulinkr-web.service\nCopia previa: %s\n' "$ml_backup"
