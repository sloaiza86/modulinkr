#!/usr/bin/env bash
# Aplica el aislamiento físico usando las credenciales operativas existentes.
set -euo pipefail
test "$(id -u)" -eq 0
ml_source="$(cd "$(dirname "$0")" && pwd)"
ml_target="$(systemctl show modulinkr-consumer.service --property=WorkingDirectory --value)"
ml_user="$(systemctl show modulinkr-consumer.service --property=User --value)"
test -f "$ml_target/consumer_service.py"
test -n "$ml_user"
ml_group="$(id -gn "$ml_user")"
test -f /etc/modulinkr/consumer.env
set -a
. /etc/modulinkr/consumer.env
set +a
python3 - "$ml_source" <<'PY'
import ast
from pathlib import Path
import sys
for path in (Path(sys.argv[1])/'consumer').glob('*.py'):
    ast.parse(path.read_text(),filename=str(path))
    print('Sintaxis:',path.name)
PY
ml_backup="$(mktemp -d /var/tmp/modulinkr-identity.XXXXXX)"
ml_files=(consumer_service.py identity_ingest.py health.py)
for ml_file in "${ml_files[@]}"; do
  test -f "$ml_source/consumer/$ml_file"
  if [ -f "$ml_target/$ml_file" ]; then cp -p "$ml_target/$ml_file" "$ml_backup/$ml_file"; fi
done
sudo -u postgres pg_dump -Fc "$MODULINKR_DB_NAME" > "$ml_backup/database.dump"
chmod 600 "$ml_backup/database.dump"
ml_migrations="$ml_backup/migrations"
mkdir "$ml_migrations"
cp "$ml_source/db/migrations/006_physical_identity.sql" "$ml_migrations/"
MODULINKR_PSQL='sudo -u postgres psql' bash "$ml_source/db/apply_migrations.sh" --db "$MODULINKR_DB_NAME" --dir "$ml_migrations"
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d "$MODULINKR_DB_NAME" --set=app_role="$MODULINKR_DB_USER" <<'SQL'
GRANT EXECUTE ON FUNCTION public.ensure_modulinkr_deployment(text) TO :"app_role";
SQL
ml_installed=0
restore_identity() {
  ml_exit=$?
  trap - EXIT
  if [ "$ml_installed" -eq 0 ]; then
    systemctl stop modulinkr-consumer.service || true
    for ml_file in "${ml_files[@]}"; do
      if [ -f "$ml_backup/$ml_file" ]; then install -v -m 0644 -o "$ml_user" -g "$ml_group" "$ml_backup/$ml_file" "$ml_target/$ml_file"; else rm -f "$ml_target/$ml_file"; fi
    done
    systemctl start modulinkr-consumer.service || true
  fi
  exit "$ml_exit"
}
trap restore_identity EXIT
systemctl stop modulinkr-consumer.service
for ml_file in "${ml_files[@]}"; do
  install -v -m 0644 -o "$ml_user" -g "$ml_group" "$ml_source/consumer/$ml_file" "$ml_target/$ml_file"
done
systemctl start modulinkr-consumer.service
sleep 3
systemctl is-active --quiet modulinkr-consumer.service
ml_installed=1
printf 'Servicio activo: modulinkr-consumer.service\nCopia previa: %s\n' "$ml_backup"
