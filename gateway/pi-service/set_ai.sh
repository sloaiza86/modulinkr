#!/usr/bin/env bash
# set_ai.sh
# Reescribe las claves MODULINKR_AI_* de web.env con pares KEY=VALUE
# recibidos por stdin. La credencial llega codificada en base64 para que el
# archivo pueda cargarla como entorno sin interpretar caracteres del secreto.
# La clave omitida conserva la vigente y ningún valor se imprime.
set -euo pipefail

WEB_ENV=/etc/modulinkr/web.env
ALLOWED=" MODULINKR_AI_PROVIDER MODULINKR_AI_MODEL MODULINKR_AI_BASE_URL \
MODULINKR_AI_API_KEY_B64 MODULINKR_AI_VERIFIED_SHA256 "

[ "$(id -u)" = "0" ] || { echo "Ejecutar con sudo." >&2; exit 1; }
[ -f "$WEB_ENV" ] || { echo "No existe $WEB_ENV (¿instalador ejecutado?)." >&2; exit 1; }

declare -A NEW
cnt=0
while IFS= read -r line; do
    [ -n "$line" ] || continue
    key=${line%%=*}
    val=${line#*=}
    case "$ALLOWED" in
        *" $key "*) : ;;
        *) echo "Clave no admitida: $key" >&2; exit 1 ;;
    esac
    case "$key" in
        MODULINKR_AI_PROVIDER)
            case "$val" in openai|openai_compatible) : ;; *) echo "Proveedor no admitido." >&2; exit 1 ;; esac
            ;;
        MODULINKR_AI_MODEL)
            [[ "$val" =~ ^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$ ]] || { echo "Modelo no válido." >&2; exit 1; }
            ;;
        MODULINKR_AI_BASE_URL)
            [[ "$val" =~ ^https://[A-Za-z0-9:/._~%+\[\]-]+$ || "$val" =~ ^http://(localhost|127\.0\.0\.1)(:[0-9]+)?(/[A-Za-z0-9:/._~%+\[\]-]*)?$ ]] || { echo "URL base no válida." >&2; exit 1; }
            ;;
        MODULINKR_AI_API_KEY_B64)
            [ ${#val} -le 11000 ] && [[ "$val" =~ ^[A-Za-z0-9+/]+={0,2}$ ]] || { echo "Credencial codificada no válida." >&2; exit 1; }
            printf '%s' "$val" | base64 --decode >/dev/null 2>&1 || { echo "Credencial codificada no válida." >&2; exit 1; }
            ;;
        MODULINKR_AI_VERIFIED_SHA256)
            [[ "$val" =~ ^[a-f0-9]{64}$ ]] || { echo "Verificación de proveedor no válida." >&2; exit 1; }
            ;;
    esac
    NEW["$key"]="$val"
    cnt=$((cnt + 1))
done

[ "$cnt" -gt 0 ] || { echo "Sin claves que actualizar." >&2; exit 1; }

tmp=$(mktemp "${WEB_ENV}.ai.XXXXXX")
trap 'rm -f "$tmp"' EXIT
while IFS= read -r line || [ -n "$line" ]; do
    key=${line%%=*}
    [ -n "${NEW[$key]+x}" ] && continue
    printf '%s\n' "$line"
done < "$WEB_ENV" > "$tmp"
for key in "${!NEW[@]}"; do
    printf '%s=%s\n' "$key" "${NEW[$key]}"
done >> "$tmp"

chmod 600 "$tmp"
chown root:root "$tmp" 2>/dev/null || true
mv "$tmp" "$WEB_ENV"
trap - EXIT

echo "web.env: ${#NEW[@]} clave(s) IA actualizada(s)"
