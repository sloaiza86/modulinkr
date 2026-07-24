#!/usr/bin/env bash
# set_mqtt.sh
# Reescribe las claves MODULINKR_MQTT_* de gateway.env con los pares
# KEY=VALUE que llegan por stdin (una por linea; solo se aceptan claves de
# la lista blanca) y reinicia el servicio del gateway. Lo invoca la pagina
# "Configurar MQTT" del visor via la regla sudo acotada del instalador, o
# el operador por SSH. Los secretos entran por stdin, no por argumentos,
# para que no asomen en la lista de procesos.
#
# Solo se tocan las claves recibidas: las ausentes quedan como estaban (una
# clave omitida conserva su valor, util para cambiar el host sin reescribir
# la contrasena).
set -euo pipefail

GW_ENV=/etc/modulinkr/gateway.env

ALLOWED=" MODULINKR_MQTT_HOST MODULINKR_MQTT_PORT MODULINKR_MQTT_USER \
MODULINKR_MQTT_PASS MODULINKR_MQTT_TLS MODULINKR_MQTT_CAFILE \
MODULINKR_MQTT_TLS_INSECURE "

[ "$(id -u)" = "0" ] || { echo "Ejecutar con sudo." >&2; exit 1; }
[ -f "$GW_ENV" ] || { echo "No existe $GW_ENV (¿instalador ejecutado?)." >&2; exit 1; }

declare -A NEW
cnt=0
while IFS= read -r line; do
    [ -n "$line" ] || continue
    key=${line%%=*}
    val=${line#*=}
    case "$ALLOWED" in
        *" $key "*) NEW["$key"]="$val"; cnt=$((cnt + 1)) ;;
        *) echo "Clave no admitida: $key" >&2; exit 1 ;;
    esac
done

[ "$cnt" -gt 0 ] || { echo "Sin claves que actualizar." >&2; exit 1; }

tmp=$(mktemp)
# Conserva las lineas cuyas claves NO se estan actualizando.
while IFS= read -r line || [ -n "$line" ]; do
    k=${line%%=*}
    [ -n "${NEW[$k]+x}" ] && continue
    printf '%s\n' "$line"
done < "$GW_ENV" > "$tmp"
# Anade las claves nuevas al final.
for k in "${!NEW[@]}"; do
    printf '%s=%s\n' "$k" "${NEW[$k]}"
done >> "$tmp"

chmod 600 "$tmp"
chown root:root "$tmp" 2>/dev/null || true
mv "$tmp" "$GW_ENV"

systemctl restart modulinkr-gateway 2>/dev/null || true

echo "gateway.env: ${#NEW[@]} clave(s) MQTT actualizada(s)"
echo "servicio: modulinkr-gateway reiniciado"
