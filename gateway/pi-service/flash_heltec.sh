#!/usr/bin/env bash
# flash_heltec.sh
# Flashea el firmware de la radio Heltec (ESP32-S3) desde el Pi por USB.
# Pensado para la instalación fresca (Heltec virgen) y para actualizaciones.
#
# Uso (como root, necesita el env del servicio y systemctl):
#   sudo ./flash_heltec.sh                 usa heltec-radio.bin junto al script
#   sudo ./flash_heltec.sh /ruta/otro.bin
#
# El binario lo genera make_dist.sh en heltec-radio/ (imagen única desde
# 0x0). El puerto sale de /etc/modulinkr/gateway.env (MODULINKR_PORT, ruta
# by-id estable). El CP2102 permite el auto-reset por DTR/RTS: el chip
# entra solo en modo bootloader, sin tocar botones. El servicio del
# gateway se para durante el flasheo (libera el puerto) y se rearranca.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${1:-$DIR/heltec-radio.bin}"
VENV="$DIR/.venv"

[ "$(id -u)" = "0" ] || { echo "[ERROR] Este comando requiere permisos de root. Vuelve a ejecutarlo con sudo." >&2; exit 1; }
[ -f "$BIN" ] || { echo "[ERROR] No existe $BIN. Genéralo con make_dist.sh y cópialo al gateway." >&2; exit 1; }

if [ -f /etc/modulinkr/gateway.env ]; then
    # shellcheck disable=SC1091
    . /etc/modulinkr/gateway.env
fi
PORT="${MODULINKR_PORT:-}"
[ -n "$PORT" ] || { echo "[ERROR] MODULINKR_PORT no está definido. Ejecuta primero el instalador del gateway." >&2; exit 1; }
[ -e "$PORT" ] || { echo "[ERROR] El puerto $PORT no existe. Conecta el Heltec y vuelve a intentarlo." >&2; exit 1; }

# esptool vive en el venv del servicio (lo instala el Gateway Installer);
# si falta (venv anterior), se añade sobre la marcha.
PY="$VENV/bin/python3"
[ -x "$PY" ] || { echo "[ERROR] No existe el entorno Python en $VENV. Ejecuta primero el instalador." >&2; exit 1; }
"$PY" -m esptool version >/dev/null 2>&1 || {
    echo "[INFO] Instalando esptool en el entorno Python..."
    "$VENV/bin/pip" install esptool
}

echo "Binario : $BIN"
echo "Puerto  : $PORT"
(sha256sum "$BIN" 2>/dev/null || shasum -a 256 "$BIN") | awk '{print "sha256  :", $1}'

echo "[INFO] Deteniendo modulinkr-gateway para liberar el puerto..."
systemctl stop modulinkr-gateway 2>/dev/null || true

echo "[INFO] Escribiendo la imagen completa de la radio desde 0x0..."
"$PY" -m esptool --chip esp32s3 --port "$PORT" --baud 460800 \
    write_flash 0x0 "$BIN"

echo "[INFO] Iniciando modulinkr-gateway..."
systemctl start modulinkr-gateway 2>/dev/null || true
