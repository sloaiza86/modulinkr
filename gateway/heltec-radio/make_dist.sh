#!/usr/bin/env bash
# make_dist.sh
# Empaqueta el firmware del Heltec (ESP32-S3) en un binario ÚNICO flasheable
# desde la dirección 0x0, para que el Pi pueda subirlo por USB con esptool
# (flash_heltec.sh del pi-service).
#
# Flujo: compilar primero en VS Code (PlatformIO) y ejecutar este script.
# Toma los artefactos de .pio/build/, los funde con el merge_bin del esptool
# que PlatformIO ya trae, y deja el resultado en pi-service/heltec-radio.bin
# (así viaja al Pi con el mismo scp del servicio).
#
# Un ESP32-S3 virgen necesita cuatro imágenes en sus offsets; merge_bin las
# convierte en un archivo que se escribe entero desde 0x0:
#   0x0     bootloader.bin
#   0x8000  partitions.bin
#   0xE000  boot_app0.bin   (del framework Arduino, no del build)
#   0x10000 firmware.bin
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="heltec_wifi_lora_32_V3"
BUILD="$DIR/.pio/build/$ENV_NAME"
OUT="$DIR/../pi-service/heltec-radio.bin"

for f in bootloader.bin partitions.bin firmware.bin; do
    if [ ! -f "$BUILD/$f" ]; then
        echo "Falta $BUILD/$f. Compilar primero en VS Code (PlatformIO)." >&2
        exit 1
    fi
done

BOOT_APP0="$(find "$HOME/.platformio/packages" -path "*arduinoespressif32*" \
             -name boot_app0.bin 2>/dev/null | head -1)"
if [ -z "$BOOT_APP0" ]; then
    echo "boot_app0.bin no encontrado en ~/.platformio/packages" >&2
    exit 1
fi

# El esptool de PlatformIO (tool-esptoolpy) ejecutado con el Python PROPIO
# de PlatformIO (penv, que ya trae pyserial); el python3 del sistema en
# macOS es el de Xcode y no lo tiene. Si no hay PlatformIO, el del PATH.
ESPTOOL_PY="$(find "$HOME/.platformio/packages/tool-esptoolpy" -maxdepth 1 \
              -name esptool.py 2>/dev/null | head -1)"
PIO_PYTHON="$HOME/.platformio/penv/bin/python3"
[ -x "$PIO_PYTHON" ] || PIO_PYTHON="$HOME/.platformio/penv/bin/python"
if [ -n "$ESPTOOL_PY" ] && [ -x "$PIO_PYTHON" ]; then
    ESPTOOL=("$PIO_PYTHON" "$ESPTOOL_PY")
elif command -v esptool.py >/dev/null 2>&1; then
    ESPTOOL=(esptool.py)
else
    echo "esptool no encontrado (ni en PlatformIO ni en el PATH)" >&2
    exit 1
fi

"${ESPTOOL[@]}" --chip esp32s3 merge_bin -o "$OUT" \
    0x0     "$BUILD/bootloader.bin" \
    0x8000  "$BUILD/partitions.bin" \
    0xE000  "$BOOT_APP0" \
    0x10000 "$BUILD/firmware.bin"

echo
echo "Generado: $OUT"
ls -lh "$OUT" | awk '{print "  tamaño:", $5}'
(shasum -a 256 "$OUT" 2>/dev/null || sha256sum "$OUT") | awk '{print "  sha256:", $1}'
