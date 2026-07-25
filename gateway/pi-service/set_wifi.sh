#!/usr/bin/env bash
# set_wifi.sh {scan|connect}
# Gestiona la red WiFi del gateway con NetworkManager (nmcli). Lo invoca la
# página "Configurar red WiFi" del visor vía la regla sudo acotada del
# instalador, o el operador por SSH.
#
#   scan     imprime las redes visibles tras un rescan, en formato terse de
#            nmcli (IN-USE:SIGNAL:SECURITY:SSID); el visor lo parsea.
#   connect  lee dos líneas de stdin, el SSID y la contraseña (vacía = red
#            abierta), y conecta. La contraseña entra por stdin (nmcli
#            --ask), nunca por argumentos, para que no asome en la lista de
#            procesos. NetworkManager guarda el perfil (persiste a reinicios);
#            este script no escribe la contraseña en ningún archivo propio.
set -euo pipefail

[ "$(id -u)" = "0" ] || { echo "Ejecutar con sudo." >&2; exit 1; }
command -v nmcli >/dev/null 2>&1 || { echo "nmcli no está (NetworkManager)." >&2; exit 1; }

MODE="${1:-}"
case "$MODE" in
    scan)
        nmcli --terse --fields IN-USE,SIGNAL,SECURITY,SSID \
              device wifi list --rescan yes
        ;;
    connect)
        # Primera línea SSID, segunda contraseña. IFS vacío conserva
        # espacios; el SSID puede llevarlos.
        IFS= read -r SSID || true
        IFS= read -r PASS || true
        [ -n "${SSID:-}" ] || { echo "SSID vacío." >&2; exit 1; }
        if [ -n "${PASS:-}" ]; then
            printf '%s\n' "$PASS" | nmcli --ask device wifi connect "$SSID"
        else
            nmcli device wifi connect "$SSID"
        fi
        ;;
    *)
        echo "Uso: set_wifi.sh {scan|connect}" >&2
        exit 1
        ;;
esac
