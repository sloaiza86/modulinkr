#!/usr/bin/env bash
# Empaqueta únicamente los archivos necesarios para esta actualización.
set -euo pipefail
ml_root="$(cd "$(dirname "$0")/../.." && pwd)"
export PYTHONDONTWRITEBYTECODE=1
python3 - "$ml_root" "${1:-}" <<'PY'
import ast
import hashlib
from pathlib import Path
import re
import shlex
import sys
import tarfile
root=Path(sys.argv[1])
with_firmware=sys.argv[2]=='--with-firmware'
if sys.argv[2] not in ('','--with-firmware'):
    raise SystemExit('Argumento no válido: '+sys.argv[2])
source=(root/'gateway/pi-service/install_identity.sh').read_text()
match=re.search(r'ml_files=\((.*?)\n\)',source,re.S)
files=['gateway/'+p for p in shlex.split(match[1])]
if not with_firmware:
    files=[p for p in files if not Path(p).name.startswith(('nodo.bin','nodo-app.bin'))]
else:
    sys.path[:0]=[str(root/'gateway/pi-web'),str(root/'gateway/pi-service')]
    import firmwaremeta
    binary=root/'gateway/pi-service/nodo.bin'
    app=root/'gateway/pi-service/nodo-app.bin'
    version=firmwaremeta.image_version(binary)
    if not version or firmwaremeta.release_number(version)<(0,0,86) or firmwaremeta.image_version(app)!=version:
        raise SystemExit('Se requiere compilar y empaquetar el firmware 0.0.86 o posterior.')
    if hashlib.sha256(app.read_bytes()).hexdigest()!=(root/'gateway/pi-service/nodo-app.bin.sha256').read_text().strip():
        raise SystemExit('El hash de la aplicación no coincide con el binario.')
    print('Firmware comprobado:',version)
files+=['gateway/pi-service/install_identity.sh','gateway/pi-service/install_mesh.sh']
server=['server/install_mesh_metrics.sh','server/consumer/consumer_service.py',
        'server/consumer/gateway_metrics_ingest.py','server/db/apply_migrations.sh',
        'server/db/migrations/007_gateway_metrics.sql']
for members,filename in [(files,'modulinkr-mesh-gateway-20261015.tar.gz'),(server,'modulinkr-mesh-server-20261015.tar.gz')]:
    members=sorted(set(members))
    for member in members:
        path=root/member
        if not path.is_file(): raise SystemExit('Falta el archivo: '+member)
        if path.suffix=='.py': ast.parse(path.read_text(),filename=member)
    target=Path('/tmp')/filename
    with tarfile.open(target,'w:gz') as archive:
        for member in members: archive.add(root/member,arcname=member,recursive=False)
    print('Paquete:',target,'archivos:',len(members),'bytes:',target.stat().st_size,'sha256:',hashlib.sha256(target.read_bytes()).hexdigest())
PY
