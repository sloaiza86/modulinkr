#!/usr/bin/env bash
# flash_nodo.sh <puerto>
# Flashea el firmware del nodo (M5Stack Atom, ESP32) en un Atom conectado por
# USB al Pi, en el puerto indicado. Lo invoca la página "Cargar firmware" del
# visor (regla sudo acotada) o el operador por SSH.
#
# A diferencia de flash_heltec.sh, el puerto es un ARGUMENTO (el Atom se
# enchufa a cualquier puerto USB), no el fijo del gateway, que queda excluido:
# ese es la radio del Heltec, no un nodo. El servicio del gateway no se toca.
#
# El binario nodo.bin lo genera nodo/make_dist.sh en el Mac (imagen única
# desde 0x0) y viaja con el mismo scp del pi-service.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="$DIR/nodo.bin"
VENV="$DIR/.venv"
PORT="${1:-}"

[ "$(id -u)" = "0" ] || { echo "[ERROR] Este comando requiere permisos de root. Vuelve a ejecutarlo con sudo." >&2; exit 1; }
[ -n "$PORT" ] || { echo "[ERROR] Uso: flash_nodo.sh <puerto>" >&2; exit 1; }
case "$PORT" in
    /dev/serial/by-id/*|/dev/ttyUSB*|/dev/ttyACM*) ;;
    *) echo "[ERROR] Puerto no admitido: $PORT" >&2; exit 1 ;;
esac
[ -e "$PORT" ] || { echo "[ERROR] El puerto $PORT no existe." >&2; exit 1; }
[ -f "$BIN" ] || { echo "[ERROR] No existe $BIN. Genéralo con make_dist.sh y cópialo al gateway." >&2; exit 1; }

# Excluir el puerto del Heltec del gateway. Se lee con grep (no con source:
# gateway.env puede tener valores con caracteres que el shell interpretaría).
if [ -f /etc/modulinkr/gateway.env ]; then
    GW_PORT="$(grep -E '^MODULINKR_PORT=' /etc/modulinkr/gateway.env | tail -1 | cut -d= -f2-)"
else
    GW_PORT=""
fi
if [ -n "$GW_PORT" ] && [ "$(readlink -f "$PORT")" = "$(readlink -f "$GW_PORT")" ]; then
    echo "[ERROR] Ese puerto pertenece a la radio del gateway, no a un nodo." >&2
    exit 1
fi

PY="$VENV/bin/python3"
[ -x "$PY" ] || { echo "[ERROR] No existe el entorno Python en $VENV. Ejecuta primero el instalador." >&2; exit 1; }
"$PY" -m esptool version >/dev/null 2>&1 || {
    echo "[INFO] Instalando esptool en el entorno Python..."
    "$VENV/bin/pip" install esptool
}

echo "Binario : $BIN"
echo "Puerto  : $PORT"
(sha256sum "$BIN" 2>/dev/null || shasum -a 256 "$BIN") | awk '{print "sha256  :", $1}'

# Baud conservador (115200): el puente USB-serie del Atom no sostiene el
# cambio a 460800 sobre el Pi (esptool sube el stub a 115200 y luego falla
# al verificar el flash al subir de baud). El binario es pequeño, así que
# el tiempo extra es asumible. (La radio Heltec, con otro adaptador, sí
# admite 460800 en flash_heltec.sh.)
echo "[INFO] Escribiendo la imagen completa del nodo desde 0x0..."
"$PY" -m esptool --chip esp32 --port "$PORT" --baud 115200 \
    write_flash 0x0 "$BIN"

echo "[ OK ] Firmware del nodo escrito. El Atom arranca con la imagen nueva."
