#!/usr/bin/env bash
# make_dist.sh
# Empaqueta el firmware del nodo (M5Stack Atom, ESP32) en un binario ÚNICO
# flasheable desde 0x0, para que el Pi lo suba por USB con esptool
# (flash_nodo.sh del pi-service).
#
# Flujo: compilar primero en VS Code (PlatformIO) y ejecutar este script.
# Toma los artefactos de .pio/build/, los funde con el merge_bin del esptool
# que PlatformIO ya trae, y deja el resultado en pi-service/nodo.bin (así
# viaja al Pi con el mismo scp del servicio).
#
# Un ESP32 clásico virgen necesita cuatro imágenes en sus offsets; merge_bin
# las funde en un archivo que se escribe entero desde 0x0 (rellena de 0xFF
# el hueco 0x0-0xFFF). Offsets del ESP32 clásico (distintos del S3 del
# Heltec, cuyo bootloader va en 0x0):
#   0x1000  bootloader.bin
#   0x8000  partitions.bin
#   0xE000  boot_app0.bin   (del framework Arduino, no del build)
#   0x10000 firmware.bin
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_NAME="atom-lite"
BUILD="$DIR/.pio/build/$ENV_NAME"
OUT="$DIR/../gateway/pi-service/nodo.bin"

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

# El esptool de PlatformIO ejecutado con su Python propio (penv, que ya trae
# pyserial); el python3 del sistema en macOS es el de Xcode y no lo tiene.
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

"${ESPTOOL[@]}" --chip esp32 merge_bin -o "$OUT" \
    0x1000  "$BUILD/bootloader.bin" \
    0x8000  "$BUILD/partitions.bin" \
    0xE000  "$BOOT_APP0" \
    0x10000 "$BUILD/firmware.bin"

# Versión del firmware junto al binario (kFirmwareVersion de main.cpp). El
# visor la reporta y la compara con la que anuncia el nodo por CFG.HELLO,
# para saber si el Atom está en la última versión.
VER="$(grep -oE 'kFirmwareVersion[^"]*"[^"]+"' "$DIR/src/main.cpp" \
       | sed -E 's/.*"([^"]+)".*/\1/' | head -1)"
if [ -n "$VER" ]; then
    printf '%s' "$VER" > "$OUT.version"
fi

# Segunda salida: la aplicación sola, para la actualización en caliente.
#
# El binario de arriba se escribe entero desde 0x0 y lleva gestor de arranque
# y tabla de particiones, que es lo que necesita un Atom virgen por USB. Una
# actualización en caliente no puede usarlo: escribe en la partición dormida
# (app1) mientras el nodo corre desde la otra, y ahí solo cabe la aplicación.
# Es además la mitad de grande, que por radio importa.
#
# El sha256 va en un archivo aparte porque el emisor tiene que anunciarlo
# antes de mandar nada, y quien lo recibe comprobarlo antes de instalar.
APP_OUT="$DIR/../gateway/pi-service/nodo-app.bin"
cp "$BUILD/firmware.bin" "$APP_OUT"
APP_SHA="$( (shasum -a 256 "$APP_OUT" 2>/dev/null || sha256sum "$APP_OUT") \
            | awk '{print $1}')"
printf '%s' "$APP_SHA" > "$APP_OUT.sha256"
[ -n "$VER" ] && printf '%s' "$VER" > "$APP_OUT.version"

echo
echo "Generado: $OUT"
ls -lh "$OUT" | awk '{print "  tamaño:", $5}'
(shasum -a 256 "$OUT" 2>/dev/null || sha256sum "$OUT") | awk '{print "  sha256:", $1}'
[ -n "$VER" ] && echo "  versión: $VER (en $OUT.version)"

APP_BYTES="$(wc -c < "$APP_OUT" | tr -d ' ')"
echo
echo "Generado: $APP_OUT"
echo "  tamaño: $APP_BYTES B ($((APP_BYTES / 1024)) kB, $((APP_BYTES * 100 / 1310720))% de app1)"
echo "  sha256: $APP_SHA"
echo "  fragmentos por radio: $(( (APP_BYTES + 212) / 213 ))"
