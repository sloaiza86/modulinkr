#!/usr/bin/env bash
# Actualiza la identidad física y la separación de datos del gateway.
set -euo pipefail
ml_runtime_only=0
case "${1:-}" in --runtime-only) ml_runtime_only=1;; '') ;; *) printf 'Argumento no válido: %s\n' "$1" >&2; exit 2;; esac
ml_stage="$(cd "$(dirname "$0")/../.." && pwd)"
ml_service="$(systemctl show modulinkr-gateway.service --property=WorkingDirectory --value)"
ml_web="$(systemctl show modulinkr-web.service --property=WorkingDirectory --value)"
test -f "$ml_service/gateway_service.py"
test -f "$ml_web/web_service.py"
test "$(realpath "$ml_service/..")" = "$(realpath "$ml_web/..")"
ml_files=(
  pi-service/nodo.bin pi-service/nodo.bin.version pi-service/nodo-app.bin pi-service/nodo-app.bin.sha256
  pi-service/identity.py pi-service/buffer.py pi-service/protocol.py pi-service/firmware_images.py
  pi-service/rak_policy.json pi-service/rak_firmware.py pi-service/rak_commission.py
  pi-service/gateway_service.py pi-service/mqtt_publisher.py pi-service/heltec_rx_parser.py
  pi-service/mesh_config.py pi-service/gateway_metrics.py
  pi-service/node_commands.py pi-service/node_maintenance.py pi-service/modbus_command.py
  pi-web/identityapi.py pi-web/web_service.py pi-web/configapi.py
  pi-web/otaapi.py pi-web/firmwaremeta.py pi-web/netstatus.py pi-web/mqttapi.py
  pi-web/debugapi.py pi-web/radioapi.py
  pi-web/systemapi.py pi-web/supervision.py pi-web/commandapi.py
  pi-web/dataapi.py pi-web/maintenanceapi.py
  pi-web/static/index.html pi-web/static/style.css pi-web/static/app.js pi-web/static/identity.js pi-web/static/quarantine.js
  pi-web/static/rak-onboarding.js
)
if [ "$ml_runtime_only" -eq 1 ]; then
  ml_runtime_files=()
  for ml_file in "${ml_files[@]}"; do
    case "$ml_file" in pi-service/nodo.bin*|pi-service/nodo-app.bin*) ;; *) ml_runtime_files+=("$ml_file");; esac
  done
  ml_files=("${ml_runtime_files[@]}")
fi
python3 - "$ml_stage" "$ml_runtime_only" <<'PY'
import ast
import hashlib
from pathlib import Path
import sys
root=Path(sys.argv[1])
sys.path.insert(0,str(root/'gateway/pi-web'))
sys.path.insert(0,str(root/'gateway/pi-service'))
import firmwaremeta
from firmware_images import flash_parts
if sys.argv[2]!='1':
    binary=root/'gateway/pi-service/nodo.bin'
    app=root/'gateway/pi-service/nodo-app.bin'
    version=firmwaremeta.image_version(binary)
    if not version or firmwaremeta.release_number(version)<(0,0,83) or firmwaremeta.image_version(app)!=version:
        raise SystemExit('Las imágenes requieren compilar y empaquetar el firmware 0.0.83 o posterior.')
    if (root/'gateway/pi-service/nodo.bin.version').read_text().strip()!=version:
        raise SystemExit('La versión de distribución no coincide con el binario.')
    if hashlib.sha256(app.read_bytes()).hexdigest()!=(root/'gateway/pi-service/nodo-app.bin.sha256').read_text().strip():
        raise SystemExit('El hash de la aplicación no coincide con el binario.')
    flash_parts(binary.read_bytes())
    print('Firmware comprobado:',version)
for path in root.glob('gateway/*/*.py'):
    ast.parse(path.read_text(), filename=str(path))
    print('Sintaxis:', path.name)
PY

"$ml_web/.venv/bin/python" -B -I - "$ml_stage/gateway/pi-web" <<'PY'
import sys
sys.path.insert(0, sys.argv[1])
import configapi
print('Importación comprobada: configapi, rak_commission y rak_firmware', flush=True)
PY

ml_db="$(sudo python3 - <<'PY'
from pathlib import Path
import subprocess
pid=subprocess.check_output(['systemctl','show','modulinkr-gateway.service','--property=MainPID','--value'],text=True).strip()
env=dict(item.split('=',1) for item in Path('/proc/'+pid+'/environ').read_bytes().decode().split('\0') if '=' in item)
path=Path(env.get('MODULINKR_DB','/home/practica/modulinkr_buffer.db'))
if not path.is_absolute() or not path.is_file(): raise SystemExit('No se pudo localizar la base operativa del gateway.')
print(path)
PY
)"
ml_backup="$(mktemp -d)"
for ml_file in "${ml_files[@]}"; do
  ml_relative="${ml_file#*/}"
  case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
  test -f "$ml_stage/gateway/$ml_file"
  mkdir -p "$ml_backup/$(dirname "$ml_file")"
  if [ -f "$ml_target" ]; then cp -p "$ml_target" "$ml_backup/$ml_file"; fi
done
ml_installed=0
restore_identity() {
  ml_exit=$?
  trap - EXIT
  if [ "$ml_installed" -eq 0 ]; then
    sudo systemctl stop modulinkr-web.service modulinkr-gateway.service || true
    for ml_file in "${ml_files[@]}"; do
      ml_relative="${ml_file#*/}"
      case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
      case "$ml_file" in *.sh) ml_mode=0755;; *) ml_mode=0644;; esac
      if [ -f "$ml_backup/$ml_file" ]; then sudo install -v -m "$ml_mode" "$ml_backup/$ml_file" "$ml_target"; else sudo rm -f "$ml_target"; fi
    done
    if [ -f "$ml_backup/buffer.db" ]; then
      sudo python3 - "$ml_backup/buffer.db" "$ml_db" <<'PY'
import sqlite3
import sys
from pathlib import Path
import os
failed=Path(sys.argv[1]).with_name('failed-buffer.db')
with sqlite3.connect(sys.argv[2]) as current, sqlite3.connect(str(failed)) as saved:
    current.backup(saved)
os.chmod(failed,0o600)
print('Base previa a la restauración:',failed)
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
PY
    fi
    sudo systemctl start modulinkr-gateway.service modulinkr-web.service || true
  fi
  exit "$ml_exit"
}
trap restore_identity EXIT
sudo systemctl stop modulinkr-web.service modulinkr-gateway.service
sudo python3 - "$ml_db" "$ml_backup/buffer.db" <<'PY'
import os
import sqlite3
import sys
with sqlite3.connect(sys.argv[1]) as source, sqlite3.connect(sys.argv[2]) as target:
    source.backup(target)
os.chmod(sys.argv[2],0o600)
print('Copia SQLite:',sys.argv[2])
PY
for ml_file in "${ml_files[@]}"; do
  ml_relative="${ml_file#*/}"
  case "$ml_file" in pi-service/*) ml_target="$ml_service/$ml_relative";; *) ml_target="$ml_web/$ml_relative";; esac
  case "$ml_file" in *.sh) ml_mode=0755;; *) ml_mode=0644;; esac
  sudo install -v -m "$ml_mode" "$ml_stage/gateway/$ml_file" "$ml_target"
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
    attempts = 0
    print('Comprobando visor: plazo=' + str(timeout) + ' s, respuestas estables=3', flush=True)
    while time.monotonic() < deadline:
        attempts += 1
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
            print('Visor HTTP 200: PID=' + pid + ', respuestas=' + str(successes) + '/3', flush=True)
            if successes >= 3:
                print('Visor comprobado:', url, 'PID:', pid)
                return True
        except (OSError, ValueError) as error:
            print('Visor pendiente: intento=' + str(attempts) + ', PID=' + pid + ', error=' + str(error), flush=True)
            previous = None
            successes = 0
        time.sleep(2)
    return False

if not ready():
    subprocess.run(['journalctl', '-u', 'modulinkr-web.service', '-n', '40', '--no-pager'], check=False)
    raise SystemExit('El visor no respondió de forma estable dentro de 60 segundos.')
PY
sudo systemctl is-active --quiet modulinkr-gateway.service
sudo systemctl is-active --quiet modulinkr-web.service
ml_installed=1
printf 'Servicios activos: modulinkr-gateway.service, modulinkr-web.service\nCopia previa: %s\n' "$ml_backup"
