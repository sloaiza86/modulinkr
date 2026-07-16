#!/usr/bin/env bash
# get_vendor.sh
# Descarga los assets JS de terceros a static/vendor/. Se ejecuta una vez
# con Internet (en la instalación o antes de desplegar): así el repo no
# versiona binarios ajenos y el visor funciona después sin conexión.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/static/vendor"
mkdir -p "$DIR"

# vis-network (mapa de topología). Versión fijada, no "latest".
VIS_VERSION="9.1.9"
curl -fsSL -o "$DIR/vis-network.min.js" \
    "https://unpkg.com/vis-network@$VIS_VERSION/standalone/umd/vis-network.min.js"

# ECharts (gráficos del módulo de datos). Versión fijada.
ECHARTS_VERSION="5.5.1"
curl -fsSL -o "$DIR/echarts.min.js" \
    "https://unpkg.com/echarts@$ECHARTS_VERSION/dist/echarts.min.js"

echo "assets en $DIR:"
ls -lh "$DIR"
