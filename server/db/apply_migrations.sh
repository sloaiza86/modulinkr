#!/usr/bin/env bash
# apply_migrations.sh
# Aplica en orden las migraciones de db/migrations/*.sql que aún no constan en
# la tabla schema_migrations. Reejecutable: las ya aplicadas se saltan. Cada
# archivo controla su propia transacción (BEGIN/COMMIT).
#
# Uso:
#   MODULINKR_DB_NAME=modulinkr ./apply_migrations.sh
#   ./apply_migrations.sh --db modulinkr
#
# Conexión: por defecto usa `psql`. Para correr como superusuario del sistema
# (instalación en la VM) exportar MODULINKR_PSQL, p. ej.:
#   MODULINKR_PSQL="sudo -u postgres psql" ./apply_migrations.sh --db modulinkr
set -euo pipefail

DB=""
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MIG_DIR="$SCRIPT_DIR/migrations"

while [ $# -gt 0 ]; do
    case "$1" in
        --db)  DB="$2"; shift 2 ;;
        --dir) MIG_DIR="$2"; shift 2 ;;
        *) echo "Argumento no reconocido: $1" >&2; exit 2 ;;
    esac
done

DB="${DB:-${MODULINKR_DB_NAME:-modulinkr}}"
PSQL_BASE="${MODULINKR_PSQL:-psql}"

# psql_run: ejecuta SQL en $DB, parando ante el primer error.
psql_run() { $PSQL_BASE -v ON_ERROR_STOP=1 -X -q -d "$DB" "$@"; }

# Tabla de control. IF NOT EXISTS, así el runner es idempotente desde cero.
psql_run <<'SQL'
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    text        PRIMARY KEY,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now()
);
SQL

shopt -s nullglob
applied=0
for f in $(printf '%s\n' "$MIG_DIR"/*.sql | sort); do
    name="$(basename "$f")"
    sum="$(sha256sum "$f" | awk '{print $1}')"

    prev="$(psql_run -t -A -c \
        "SELECT checksum FROM schema_migrations WHERE filename = '$name'")"
    prev="$(printf '%s' "$prev" | tr -d '[:space:]')"

    if [ -n "$prev" ]; then
        if [ "$prev" != "$sum" ]; then
            echo "AVISO: '$name' ya aplicada pero su contenido cambió (checksum distinto)." >&2
            echo "       Las migraciones son inmutables; crear una nueva en vez de editar." >&2
        fi
        continue
    fi

    echo "Aplicando $name ..."
    # Se pasa por stdin, no con -f: psql corre como el usuario 'postgres' y no
    # podría leer archivos bajo /home. Quien redirige es el proceso que corre
    # el instalador (root), con acceso al archivo.
    psql_run < "$f"
    psql_run -c \
        "INSERT INTO schema_migrations (filename, checksum) VALUES ('$name', '$sum')"
    applied=$((applied + 1))
done

if [ "$applied" -eq 0 ]; then
    echo "Sin migraciones pendientes. La base ya está al día."
else
    echo "Migraciones aplicadas: $applied."
fi
