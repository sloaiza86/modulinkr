"""Empaqueta archivos de identidad sin incluir credenciales ni dependencias."""
import hashlib
from pathlib import Path
import re
import sys
import tarfile

ROOT=Path(__file__).resolve().parents[2]


def files(component):
    if component in ('gateway','gateway-runtime'):
        script=ROOT/'gateway/pi-service/install_identity.sh'
        match=re.search(r'ml_files=\((.*?)\)',script.read_text(),re.S)
        names=['gateway/'+name for name in match[1].split()]
        if component=='gateway-runtime':
            names=[name for name in names if not Path(name).name.startswith(('nodo.bin','nodo-app.bin'))]
        names+=['gateway/pi-service/install_identity.sh']
    elif component=='server':
        names=['server/install_identity.sh','server/consumer/consumer_service.py',
               'server/consumer/identity_ingest.py','server/consumer/health.py','server/db/apply_migrations.sh',
               'server/db/migrations/006_physical_identity.sql']
    else:
        raise ValueError('El componente debe ser gateway, gateway-runtime o server.')
    for name in names:
        if not (ROOT/name).is_file(): raise ValueError('Falta el archivo '+name)
    return names


def validate_images():
    sys.path.insert(0,str(ROOT/'gateway/pi-web'))
    sys.path.insert(0,str(ROOT/'gateway/pi-service'))
    import firmwaremeta
    from firmware_images import flash_parts
    directory=ROOT/'gateway/pi-service'
    version=firmwaremeta.image_version(directory/'nodo.bin')
    release=firmwaremeta.release_number(version)
    source=(ROOT/'nodo/src/main.cpp').read_text()
    expected=re.search(r'#define MODULINKR_FIRMWARE_VERSION "([^"\n]+)"',source)
    required=firmwaremeta.release_number(expected[1]) if expected else None
    if required is None or release!=required or firmwaremeta.image_version(directory/'nodo-app.bin')!=version:
        raise ValueError('Se requiere Build en VS Code y make_dist.sh para la versión actual del código del nodo.')
    if (directory/'nodo.bin.version').read_text().strip()!=version:
        raise ValueError('La versión distribuida no coincide con la imagen.')
    if hashlib.sha256((directory/'nodo-app.bin').read_bytes()).hexdigest()!=(directory/'nodo-app.bin.sha256').read_text().strip():
        raise ValueError('El hash de la aplicación no coincide con la imagen.')
    flash_parts((directory/'nodo.bin').read_bytes())
    return version


def package(component,destination):
    names=files(component)
    if component=='gateway': print('Firmware:',validate_images())
    with tarfile.open(destination,'w:gz') as archive:
        for name in names: archive.add(ROOT/name,arcname=name,recursive=False)
    path=Path(destination)
    print('Paquete:',path.resolve(),'Archivos:',len(names),'Bytes:',path.stat().st_size)
    print('SHA256:',hashlib.sha256(path.read_bytes()).hexdigest())


if __name__=='__main__':
    if len(sys.argv)!=3: raise SystemExit('Uso: package_identity.py gateway|gateway-runtime|server ruta_del_paquete')
    try: package(sys.argv[1],sys.argv[2])
    except (ValueError,OSError) as error: raise SystemExit(str(error))
