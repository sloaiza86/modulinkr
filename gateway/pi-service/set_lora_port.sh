#!/usr/bin/env bash
# set_lora_port.sh <puerto>
# Fija el puerto serie del Heltec: MODULINKR_PORT en gateway.env, su copia
# de exclusión MODULINKR_GATEWAY_PORT en web.env, y reinicio del servicio
# del gateway. Lo invoca la página "Configurar radio LoRa" del visor (vía
# la regla sudo acotada del instalador) o el operador por SSH.
set -euo pipefail

PORT="${1:-}"
GW_ENV=/etc/modulinkr/gateway.env
WEB_ENV=/etc/modulinkr/web.env

[ "$(id -u)" = "0" ] || { echo "Ejecutar con sudo." >&2; exit 1; }
[ -n "$PORT" ] || { echo "Uso: set_lora_port.sh <puerto>" >&2; exit 1; }
case "$PORT" in
    /dev/serial/by-id/*|/dev/ttyUSB*|/dev/ttyACM*) ;;
    *) echo "Puerto no admitido: $PORT" >&2; exit 1 ;;
esac
[ -e "$PORT" ] || { echo "El puerto $PORT no existe." >&2; exit 1; }
[ -f "$GW_ENV" ] || { echo "No existe $GW_ENV (¿instalador ejecutado?)." >&2; exit 1; }

if grep -q '^MODULINKR_PORT=' "$GW_ENV"; then
    sed -i "s|^MODULINKR_PORT=.*|MODULINKR_PORT=$PORT|" "$GW_ENV"
else
    echo "MODULINKR_PORT=$PORT" >> "$GW_ENV"
fi

if [ -f "$WEB_ENV" ]; then
    if grep -q '^MODULINKR_GATEWAY_PORT=' "$WEB_ENV"; then
        sed -i "s|^MODULINKR_GATEWAY_PORT=.*|MODULINKR_GATEWAY_PORT=$PORT|" "$WEB_ENV"
    else
        echo "MODULINKR_GATEWAY_PORT=$PORT" >> "$WEB_ENV"
    fi
fi

systemctl restart modulinkr-gateway 2>/dev/null || true

echo "puerto: $PORT"
echo "gateway.env: MODULINKR_PORT actualizado"
[ -f "$WEB_ENV" ] && echo "web.env: MODULINKR_GATEWAY_PORT actualizado"
echo "servicio: modulinkr-gateway reiniciado"
