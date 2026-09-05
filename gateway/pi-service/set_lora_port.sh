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

[ "$(id -u)" = "0" ] || { echo "[ERROR] Este comando requiere permisos de root. Vuelve a ejecutarlo con sudo." >&2; exit 1; }
[ -n "$PORT" ] || { echo "[ERROR] Uso: set_lora_port.sh <puerto>" >&2; exit 1; }
case "$PORT" in
    /dev/serial/by-id/*|/dev/ttyUSB*|/dev/ttyACM*) ;;
    *) echo "[ERROR] Puerto no admitido: $PORT" >&2; exit 1 ;;
esac
[ -e "$PORT" ] || { echo "[ERROR] El puerto $PORT no existe." >&2; exit 1; }
[ -f "$GW_ENV" ] || { echo "[ERROR] No existe $GW_ENV. Ejecuta primero el instalador." >&2; exit 1; }

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

echo "[ OK ] Puerto serie configurado: $PORT"
echo "[ OK ] gateway.env: MODULINKR_PORT actualizado"
[ -f "$WEB_ENV" ] && echo "[ OK ] web.env: MODULINKR_GATEWAY_PORT actualizado"
echo "[ OK ] Servicio reiniciado: modulinkr-gateway"
