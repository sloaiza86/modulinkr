#!/usr/bin/env bash
# get_net.sh
# Lee de gateway.env (solo root) los parámetros que TODO dispositivo del
# despliegue debe compartir con el gateway, y los imprime como KEY=VALUE.
# Lo invoca el asistente de configuración de nodo del visor (regla sudo
# acotada) para bloquear esos campos del formulario a los valores reales.
#
# Solo lectura: no modifica nada. Expone la clave de red y la del broker
# (el nodo las necesita para cifrar y publicar igual que la red); el visor
# las usa para armar el config.json que va al nodo, el mismo destino que
# tendrían escritas a mano.
#
# Se extrae con sed (no grep) para tolerar claves ausentes: una clave que el
# servicio usa por defecto y que no está en el archivo devuelve valor vacío,
# sin abortar el script.
set -euo pipefail

GW_ENV=/etc/modulinkr/gateway.env

[ "$(id -u)" = "0" ] || { echo "[ERROR] Este comando requiere permisos de root. Vuelve a ejecutarlo con sudo." >&2; exit 1; }
[ -f "$GW_ENV" ] || { echo "[ERROR] No existe $GW_ENV." >&2; exit 1; }

for k in MODULINKR_LORA_REGION MODULINKR_LORA_FREQ_HZ \
         MODULINKR_NETWORK_ID MODULINKR_MAX_TTL MODULINKR_SF MODULINKR_BW_KHZ \
         MODULINKR_SEC_ENABLED MODULINKR_SEC_KEY \
         MODULINKR_MQTT_HOST MODULINKR_MQTT_PORT MODULINKR_MQTT_USER \
         MODULINKR_MQTT_PASS MODULINKR_MQTT_TLS; do
    v="$(sed -n "s/^$k=//p" "$GW_ENV" | tail -1)"
    printf '%s=%s\n' "$k" "$v"
done
