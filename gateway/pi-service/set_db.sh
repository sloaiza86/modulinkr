#!/usr/bin/env bash
# set_db.sh
# Reescribe las claves MODULINKR_PG_* de web.env con los pares KEY=VALUE
# que llegan por stdin (una por linea; solo se aceptan claves de la lista
# blanca). Lo invoca la pagina "Configurar base de datos" del visor via la
# regla sudo acotada del instalador, o el operador por SSH. Los secretos
# entran por stdin, no por argumentos.
#
# NO reinicia el visor: el modulo de datos (dataapi.py) relee estos
# parametros en caliente en el propio proceso tras guardar. La escritura
# aqui es para que el valor sobreviva a un reinicio del servicio.
#
# Solo se tocan las claves recibidas: las ausentes quedan como estaban.
set -euo pipefail

WEB_ENV=/etc/modulinkr/web.env

ALLOWED=" MODULINKR_PG_HOST MODULINKR_PG_PORT MODULINKR_PG_DB \
MODULINKR_PG_USER MODULINKR_PG_PASSWORD "

[ "$(id -u)" = "0" ] || { echo "[ERROR] Este comando requiere permisos de root. Vuelve a ejecutarlo con sudo." >&2; exit 1; }
[ -f "$WEB_ENV" ] || { echo "[ERROR] No existe $WEB_ENV. Ejecuta primero el instalador." >&2; exit 1; }

declare -A NEW
cnt=0
while IFS= read -r line; do
    [ -n "$line" ] || continue
    key=${line%%=*}
    val=${line#*=}
    case "$ALLOWED" in
        *" $key "*) NEW["$key"]="$val"; cnt=$((cnt + 1)) ;;
        *) echo "[ERROR] Clave no admitida: $key" >&2; exit 1 ;;
    esac
done

[ "$cnt" -gt 0 ] || { echo "[ERROR] No se recibieron claves para actualizar." >&2; exit 1; }

tmp=$(mktemp)
while IFS= read -r line || [ -n "$line" ]; do
    k=${line%%=*}
    [ -n "${NEW[$k]+x}" ] && continue
    printf '%s\n' "$line"
done < "$WEB_ENV" > "$tmp"
for k in "${!NEW[@]}"; do
    printf '%s=%s\n' "$k" "${NEW[$k]}"
done >> "$tmp"

chmod 600 "$tmp"
chown root:root "$tmp" 2>/dev/null || true
mv "$tmp" "$WEB_ENV"

echo "[ OK ] web.env: ${#NEW[@]} clave(s) de PostgreSQL actualizada(s)"
