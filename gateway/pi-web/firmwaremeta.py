"""Identidad del firmware leída de la aplicación ESP32 distribuida."""
from functools import lru_cache
from pathlib import Path
import re
import struct


@lru_cache(maxsize=16)
def _read(path, size, mtime):
    data = Path(path).read_bytes()
    offset = 0x10000 if path.endswith(('nodo.bin', 'heltec-radio.bin')) else 0
    app = data[offset:]
    if len(app) < 288 or app[0] != 0xE9:
        return None
    if struct.unpack_from('<I', app, 32)[0] != 0xABCD5432:
        return None
    versions = set(re.findall(rb'MLFW:([0-9][0-9A-Za-z.\-]{0,18})\x00', app))
    if len(versions) != 1 or not any(app[176:208]):
        return None
    return versions.pop().decode('ascii') + '+' + app[176:182].hex()


def image_version(path):
    path = Path(path)
    try:
        st = path.stat()
        return _read(str(path), st.st_size, st.st_mtime_ns)
    except (OSError, ValueError, struct.error):
        return None


def release_number(value):
    match = re.fullmatch(r'(\d+)\.(\d+)\.(\d+)(?:[-+][0-9A-Za-z.+-]+)?', value or '')
    return tuple(map(int, match.groups())) if match else None


def comparison(installed, available):
    old, new = release_number(installed), release_number(available)
    if new is None:
        return 'unavailable'
    if old is None:
        return 'unknown'
    if old == new:
        return 'current'
    return 'older' if old > new else 'different'


def release_notes(component, version):
    notes = {
        ('node', (0, 0, 61)): ['Registros de diagnóstico con tiempo, nivel, componente y evento, legibles por USB y web.'],
        ('radio', (0, 3, 3)): ['Registros de diagnóstico uniformes. Se conservan las respuestas USB que interpreta el gateway.'],
        ('node', (0, 0, 60)): ['Versión de prueba para verificar la actualización USB y la confirmación del arranque. Sin cambios funcionales respecto a 0.0.59.'],
        ('radio', (0, 3, 2)): ['Versión de prueba para verificar la actualización de la radio. Sin cambios funcionales respecto a 0.3.1.'],
        ('node', (0, 0, 59)): [
            'Comprobación del estado de MQTT y NB-IoT mediante consultas al módem cada 60 segundos.',
            'Mensajes más claros al validar la configuración del nodo.',
            'Identificación del firmware y comprobación de la imagen antes de instalar por LoRa.'
        ],
        ('radio', (0, 3, 1)): [
            'Consulta de la versión instalada por USB sin reiniciar la radio.'
        ],
    }
    return notes.get((component, release_number(version)), [])


def distribution_version(binary, source, previous):
    version = image_version(binary)
    declared = re.search(r'^#define MODULINKR_FIRMWARE_VERSION "([^"\n]+)"', Path(source).read_text())
    if not version or not declared or version.split('+')[0] != declared[1]:
        raise ValueError('El binario no corresponde a la version del codigo. Compila el firmware en VS Code antes de empaquetarlo.')
    old = image_version(previous)
    if old and comparison(old, version) == 'current' and old != version:
        raise ValueError('Esta version ya tiene otro firmware empaquetado. Incrementa la version antes de distribuir cambios.')
    return version


if __name__ == '__main__':
    import sys
    try:
        version = distribution_version(*sys.argv[1:4]) if len(sys.argv) == 4 else image_version(sys.argv[1])
    except ValueError as error:
        raise SystemExit(str(error))
    if not version:
        raise SystemExit('No se pudo identificar la compilacion del binario. Compila el firmware en VS Code antes de empaquetarlo.')
    print(version)
