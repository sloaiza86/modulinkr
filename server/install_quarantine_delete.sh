#!/usr/bin/env bash
set -euo pipefail
if [ "$(id -u)" -ne 0 ]; then
  exec sudo bash "$0" "$@"
fi
ml_stage="$(cd "$(dirname "$0")" && pwd)"
ml_env="${MODULINKR_ETC:-/etc/modulinkr}/database.env"
test -f "$ml_env"
. "$ml_env"
test -n "${MODULINKR_DB_NAME:-}"
ml_ready="$(sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d "$MODULINKR_DB_NAME" -tAc \
  "SELECT to_regclass('public.quarantine') IS NOT NULL AND EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'modulinkr_ro')")"
if [ "$ml_ready" != t ]; then
  printf 'ERROR: quarantine o modulinkr_ro no existen en la base configurada.\n' >&2
  exit 1
fi
MODULINKR_PSQL="sudo -u postgres psql" bash "$ml_stage/server/db/apply_migrations.sh" \
  --db "$MODULINKR_DB_NAME" --dir "$ml_stage/server/db/migrations"
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d "$MODULINKR_DB_NAME" -P pager=off -c \
  "GRANT DELETE ON TABLE public.quarantine TO modulinkr_ro;
   SELECT has_table_privilege('modulinkr_ro', 'public.quarantine', 'DELETE') AS borrar_cuarentena,
          has_table_privilege('modulinkr_ro', 'public.samples', 'DELETE') AS borrar_muestras,
          has_table_privilege('modulinkr_ro', 'public.sample_values', 'DELETE') AS borrar_valores"
