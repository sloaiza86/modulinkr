#!/usr/bin/env bash
# Actualiza el gateway y retira la programación USB desde el servicio web.
set -euo pipefail
ml_source="$(cd "$(dirname "$0")" && pwd)"
ml_runtime="$(systemctl show modulinkr-gateway.service --property=WorkingDirectory --value)"
test -f "$ml_runtime/gateway_service.py"
case "${1:-}" in
  '') bash "$ml_source/install_identity.sh" --runtime-only;;
  --with-firmware) bash "$ml_source/install_identity.sh";;
  *) printf 'Argumento no válido: %s\n' "$1" >&2; exit 2;;
esac
sudo python3 - "$ml_runtime" <<'PY'
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
runtime=Path(sys.argv[1])
backup=Path(tempfile.mkdtemp(prefix='modulinkr-usb-',dir='/var/tmp'))
os.chmod(backup,0o700)
rule=Path('/etc/sudoers.d/modulinkr-web')
if rule.exists():
    shutil.copy2(rule,backup/rule.name)
    original=rule.read_text()
    updated=original.replace(', '+str(runtime/'flash_heltec.sh'),'').replace(', '+str(runtime/'flash_nodo.sh')+' *','')
    candidate=backup/'sudoers.checked'
    candidate.write_text(updated)
    os.chmod(candidate,0o440)
    subprocess.run(['visudo','-cf',str(candidate)],check=True)
    shutil.copy2(candidate,rule)
for name in ('flash_nodo.sh','flash_heltec.sh'):
    path=runtime/name
    if path.exists():
        shutil.copy2(path,backup/name)
        path.unlink()
        print('Retirado:',path)
print('Copia previa de USB y permisos:',backup)
PY
