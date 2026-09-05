#!/usr/bin/env bash
# get_vendor.sh
# Descarga los assets de terceros a static/vendor/. Se ejecuta con Internet
# durante la instalación o antes del despliegue. El visor funciona después
# sin conexión y el repositorio no versiona copias de dependencias externas.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/static/vendor"
mkdir -p "$DIR"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf -- "$TMP_DIR"' EXIT

fetch_npm_package() {
    local key="$1"
    local url="$2"
    local destination="$3"
    local archive="$TMP_DIR/$key.tgz"
    local extracted="$TMP_DIR/$key"

    mkdir -p "$extracted"
    curl -fsSL -o "$archive" "$url"
    tar -xzf "$archive" -C "$extracted" --strip-components=1
    rm -rf -- "$destination"
    mv "$extracted" "$destination"
}

# vis-network (mapa de topología). Versión fijada, no "latest".
VIS_VERSION="9.1.9"
curl -fsSL -o "$DIR/vis-network.min.js" \
    "https://unpkg.com/vis-network@$VIS_VERSION/standalone/umd/vis-network.min.js"

# ECharts (gráficos del módulo de datos). Versión fijada.
ECHARTS_VERSION="5.5.1"
curl -fsSL -o "$DIR/echarts.min.js" \
    "https://unpkg.com/echarts@$ECHARTS_VERSION/dist/echarts.min.js"

# esptool-js (flasheo del nodo por Web Serial desde el navegador, camino A).
# Bundle ESM autocontenido en un solo archivo: se sirve local y funciona sin
# Internet. Versión fijada. Escribe solo el firmware (eraseAll:false), así
# conserva el config.json del nodo.
ESPTOOL_VERSION="0.4.5"
curl -fsSL -o "$DIR/esptool-bundle.js" \
    "https://unpkg.com/esptool-js@$ESPTOOL_VERSION/bundle.js"

# Cally (selector de periodos). Se conserva el paquete oficial completo para
# mantener disponibles el módulo y su licencia.
CALLY_VERSION="0.9.2"
fetch_npm_package "cally" \
    "https://registry.npmjs.org/cally/-/cally-$CALLY_VERSION.tgz" \
    "$DIR/cally-$CALLY_VERSION"

# Web Awesome (árbol de medidas). El componente importa módulos auxiliares
# mediante rutas relativas, por lo que se conserva la distribución completa.
WEB_AWESOME_VERSION="3.11.0"
fetch_npm_package "webawesome" \
    "https://registry.npmjs.org/@awesome.me/webawesome/-/webawesome-$WEB_AWESOME_VERSION.tgz" \
    "$DIR/webawesome-$WEB_AWESOME_VERSION"

echo "assets en $DIR:"
find "$DIR" -maxdepth 1 -mindepth 1 -print | sort
