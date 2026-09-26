#!/usr/bin/env bash
# Instala las tres acciones privilegiadas sin ampliar los permisos existentes.
set -euo pipefail
[ "$(id -u)" = 0 ] || { echo 'Se requieren permisos de root' >&2; exit 1; }
source_dir="$(cd "$(dirname "$0")" && pwd)"
service_user="${1:-$(systemctl show modulinkr-web.service -p User --value)}"
[[ "$service_user" =~ ^[a-z_][a-z0-9_-]*\$?$ ]] || { echo 'Usuario del visor no válido' >&2; exit 1; }
id "$service_user" >/dev/null
for binary in /usr/bin/python3 /usr/bin/systemctl /usr/bin/systemd-run; do
    test -x "$binary"
done
install -d -o root -g root -m 755 /usr/local/libexec
rule=$(mktemp)
trap 'rm -f "$rule"' EXIT
printf '%s ALL=(root) NOPASSWD: /usr/local/libexec/modulinkr-maintenance web, /usr/local/libexec/modulinkr-maintenance communications, /usr/local/libexec/modulinkr-maintenance gateway\n' "$service_user" > "$rule"
visudo -cf "$rule"
install -o root -g root -m 755 "$source_dir/maintenance.py" /usr/local/libexec/modulinkr-maintenance
install -o root -g root -m 440 "$rule" /etc/sudoers.d/modulinkr-maintenance
printf 'Mantenimiento instalado para %s\n' "$service_user"
