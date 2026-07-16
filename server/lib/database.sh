# database.sh
# Módulo de base de datos del instalador: PostgreSQL 16 (extensión TimescaleDB
# opcional), rol y base de la aplicación, ajuste para poca memoria, swap y
# aplicación de las migraciones. Idempotente: reejecutar no rompe una
# instalación previa. Se carga con `source` desde install.sh.

PG_VERSION="${PG_VERSION:-16}"
MODULINKR_DB_NAME="${MODULINKR_DB_NAME:-modulinkr}"
MODULINKR_DB_USER="${MODULINKR_DB_USER:-modulinkr}"
MODULINKR_DB_PASSWORD="${MODULINKR_DB_PASSWORD:-}"
ENABLE_TIMESCALEDB="${ENABLE_TIMESCALEDB:-1}"

# Ajuste pensado para una VM de 1 GB de RAM compartida con Mosquitto
# (Plataformas V4 §despliegue). Todos configurables desde el config file.
PG_SHARED_BUFFERS="${PG_SHARED_BUFFERS:-128MB}"
PG_EFFECTIVE_CACHE_SIZE="${PG_EFFECTIVE_CACHE_SIZE:-512MB}"
PG_WORK_MEM="${PG_WORK_MEM:-4MB}"
PG_MAINTENANCE_WORK_MEM="${PG_MAINTENANCE_WORK_MEM:-32MB}"
PG_MAX_CONNECTIONS="${PG_MAX_CONNECTIONS:-20}"

SWAP_SIZE="${SWAP_SIZE:-2G}"
SWAP_FILE="${SWAP_FILE:-/swapfile}"

MODULINKR_ETC="${MODULINKR_ETC:-/etc/modulinkr}"
DB_ENV_FILE="$MODULINKR_ETC/database.env"

_psql_super() { sudo -u postgres psql -v ON_ERROR_STOP=1 -X -q "$@"; }

# db_add_repos: repositorios oficiales de PostgreSQL (PGDG) y, si procede, de
# TimescaleDB. Solo escribe lo que falte.
db_add_repos() {
    step "Repositorios de paquetes"
    apt-get install -y curl ca-certificates gnupg lsb-release >/dev/null
    install -d /usr/share/keyrings
    local codename; codename="$(lsb_release -cs)"

    if [ ! -f /usr/share/keyrings/pgdg.gpg ]; then
        curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
            | gpg --dearmor -o /usr/share/keyrings/pgdg.gpg
    fi
    if [ ! -f /etc/apt/sources.list.d/pgdg.list ]; then
        echo "deb [signed-by=/usr/share/keyrings/pgdg.gpg] http://apt.postgresql.org/pub/repos/apt ${codename}-pgdg main" \
            > /etc/apt/sources.list.d/pgdg.list
        ok "Repositorio PGDG añadido"
    fi

    if [ "$ENABLE_TIMESCALEDB" = "1" ]; then
        if [ ! -f /usr/share/keyrings/timescaledb.gpg ]; then
            curl -fsSL https://packagecloud.io/timescale/timescaledb/gpgkey \
                | gpg --dearmor -o /usr/share/keyrings/timescaledb.gpg
        fi
        if [ ! -f /etc/apt/sources.list.d/timescaledb.list ]; then
            echo "deb [signed-by=/usr/share/keyrings/timescaledb.gpg] https://packagecloud.io/timescale/timescaledb/ubuntu/ ${codename} main" \
                > /etc/apt/sources.list.d/timescaledb.list
            ok "Repositorio TimescaleDB añadido"
        fi
    fi
    apt-get update >/dev/null
}

db_install_packages() {
    step "Instalación de PostgreSQL $PG_VERSION"
    local pkgs="postgresql-$PG_VERSION postgresql-client-$PG_VERSION"
    [ "$ENABLE_TIMESCALEDB" = "1" ] && pkgs="$pkgs timescaledb-2-postgresql-$PG_VERSION"
    DEBIAN_FRONTEND=noninteractive apt-get install -y $pkgs >/dev/null
    ok "Paquetes instalados: $pkgs"
}

# db_ensure_swap: colchón de swap ante picos con MQTT y la base en 1 GB
# (Plataformas V4 §despliegue). No toca nada si ya hay swap activo.
db_ensure_swap() {
    step "Swap"
    if [ "$(swapon --show --noheadings | wc -l)" -gt 0 ]; then
        ok "Ya hay swap activo; sin cambios"
        return 0
    fi
    if [ ! -f "$SWAP_FILE" ]; then
        log "Creando $SWAP_FILE de $SWAP_SIZE"
        if ! fallocate -l "$SWAP_SIZE" "$SWAP_FILE" 2>/dev/null; then
            local mb; mb="$(numfmt --from=iec "$SWAP_SIZE")"; mb=$((mb / 1024 / 1024))
            dd if=/dev/zero of="$SWAP_FILE" bs=1M count="$mb" status=none
        fi
        chmod 600 "$SWAP_FILE"
        mkswap "$SWAP_FILE" >/dev/null
    fi
    swapon "$SWAP_FILE"
    grep -q "^$SWAP_FILE " /etc/fstab || echo "$SWAP_FILE none swap sw 0 0" >> /etc/fstab
    ok "Swap activo desde $SWAP_FILE"
}

db_ensure_running() {
    systemctl enable --now "postgresql@$PG_VERSION-main" >/dev/null 2>&1 || systemctl enable --now postgresql >/dev/null 2>&1 || true
    pg_isready >/dev/null 2>&1 || { systemctl restart postgresql; sleep 2; }
}

# db_load_or_make_password: mantiene estable la contraseña entre reejecuciones.
# Si no viene del config, la lee del env guardado; si tampoco, la genera.
db_load_or_make_password() {
    if [ -z "$MODULINKR_DB_PASSWORD" ] && [ -f "$DB_ENV_FILE" ]; then
        # shellcheck disable=SC1090
        . "$DB_ENV_FILE"
        MODULINKR_DB_PASSWORD="${MODULINKR_DB_PASSWORD:-}"
    fi
    if [ -z "$MODULINKR_DB_PASSWORD" ]; then
        MODULINKR_DB_PASSWORD="$(rand_secret)"
        log "Contraseña de la base generada automáticamente"
    fi
}

db_create_role_and_db() {
    step "Rol y base de datos"
    local u="$MODULINKR_DB_USER" p="$MODULINKR_DB_PASSWORD" d="$MODULINKR_DB_NAME"
    local pesc="${p//\'/\'\'}"

    if _psql_super -tAc "SELECT 1 FROM pg_roles WHERE rolname='$u'" | grep -q 1; then
        _psql_super -c "ALTER ROLE \"$u\" WITH LOGIN PASSWORD '$pesc'"
        ok "Rol '$u' ya existía; contraseña sincronizada"
    else
        _psql_super -c "CREATE ROLE \"$u\" WITH LOGIN PASSWORD '$pesc'"
        ok "Rol '$u' creado"
    fi

    if _psql_super -tAc "SELECT 1 FROM pg_database WHERE datname='$d'" | grep -q 1; then
        ok "Base '$d' ya existía"
    else
        _psql_super -c "CREATE DATABASE \"$d\" OWNER \"$u\""
        ok "Base '$d' creada"
    fi
}

# db_apply_tuning: escribe un drop-in en conf.d en vez de tocar el
# postgresql.conf principal, para no pisar la config base del paquete.
db_apply_tuning() {
    step "Ajuste de memoria"
    local confd="${PG_CONFD:-/etc/postgresql/$PG_VERSION/main/conf.d}"
    install -d "$confd"
    {
        echo "# Generado por el instalador de ModuLinkr. Ajuste para VM de poca RAM."
        echo "# Editar aquí, no en postgresql.conf. Reejecutar el instalador lo regenera."
        # TimescaleDB debe precargarse en el arranque para poder crear la
        # extensión (shared_preload_libraries se lee solo al iniciar Postgres).
        [ "$ENABLE_TIMESCALEDB" = "1" ] && echo "shared_preload_libraries = 'timescaledb'"
        echo "shared_buffers = $PG_SHARED_BUFFERS"
        echo "effective_cache_size = $PG_EFFECTIVE_CACHE_SIZE"
        echo "work_mem = $PG_WORK_MEM"
        echo "maintenance_work_mem = $PG_MAINTENANCE_WORK_MEM"
        echo "max_connections = $PG_MAX_CONNECTIONS"
    } > "$confd/99-modulinkr.conf"
    ok "Ajuste escrito en conf.d/99-modulinkr.conf"
    systemctl restart postgresql
    sleep 2
    pg_isready >/dev/null 2>&1 && ok "PostgreSQL reiniciado con el nuevo ajuste"
}

db_enable_timescaledb() {
    [ "$ENABLE_TIMESCALEDB" = "1" ] || return 0
    step "Extensión TimescaleDB"
    # No abortar la instalación si falla: la base y las migraciones no dependen
    # de la extensión (db-schema.md §6 la deja como opción aditiva futura).
    if _psql_super -d "$MODULINKR_DB_NAME" -c "CREATE EXTENSION IF NOT EXISTS timescaledb"; then
        ok "Extensión disponible en '$MODULINKR_DB_NAME'"
    else
        warn "No se pudo habilitar TimescaleDB; se continúa sin la extensión."
    fi
    # Nota: el esquema no convierte samples en hypertable; la extensión queda
    # lista por si se decide más adelante, sin cambiar el schema actual.
}

db_run_migrations() {
    step "Migraciones del esquema"
    MODULINKR_DB_NAME="$MODULINKR_DB_NAME" MODULINKR_PSQL="sudo -u postgres psql" \
        bash "$SERVER_DIR/db/apply_migrations.sh" --db "$MODULINKR_DB_NAME"
}

# db_grant_app: las migraciones corren como 'postgres', así que las tablas
# quedan a su nombre. El rol de la aplicación (el que usará el consumidor
# cloud) necesita permisos sobre el esquema. Idempotente. ALTER DEFAULT
# PRIVILEGES cubre los objetos que creen futuras migraciones.
db_grant_app() {
    step "Permisos del rol de aplicación"
    local u="$MODULINKR_DB_USER"
    _psql_super -d "$MODULINKR_DB_NAME" <<SQL
GRANT USAGE ON SCHEMA public TO "$u";
GRANT ALL ON ALL TABLES IN SCHEMA public TO "$u";
GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO "$u";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON TABLES TO "$u";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT ALL ON SEQUENCES TO "$u";
SQL
    ok "Permisos concedidos a '$u' sobre el esquema public"
}

db_save_env() {
    install -d -m 700 "$MODULINKR_ETC"
    cat > "$DB_ENV_FILE" <<EOF
# Credenciales de la base ModuLinkr. Generado por el instalador. No versionar.
MODULINKR_DB_NAME=$MODULINKR_DB_NAME
MODULINKR_DB_USER=$MODULINKR_DB_USER
MODULINKR_DB_PASSWORD=$MODULINKR_DB_PASSWORD
MODULINKR_DB_RO_PASSWORD=$MODULINKR_DB_RO_PASSWORD
EOF
    chmod 600 "$DB_ENV_FILE"
    ok "Credenciales guardadas en $DB_ENV_FILE (solo root)"
}

# db_load_or_make_ro_password: contraseña del rol de solo lectura, con la
# misma política que la del rol de aplicación (estable entre reejecuciones).
db_load_or_make_ro_password() {
    if [ -z "${MODULINKR_DB_RO_PASSWORD:-}" ] && [ -f "$DB_ENV_FILE" ]; then
        # shellcheck disable=SC1090
        . "$DB_ENV_FILE"
        MODULINKR_DB_RO_PASSWORD="${MODULINKR_DB_RO_PASSWORD:-}"
    fi
    if [ -z "${MODULINKR_DB_RO_PASSWORD:-}" ]; then
        MODULINKR_DB_RO_PASSWORD="$(rand_secret)"
        log "Contraseña del rol de solo lectura generada automáticamente"
    fi
}

# db_enable_remote_ro: acceso remoto de SOLO LECTURA para el visor del Pi
# (pi-web/README.md §2). Rol modulinkr_ro con SELECT únicamente, listener
# en todas las interfaces y una regla hostssl restringida a ese rol y esta
# base. El cifrado lo da el ssl=on por defecto de Ubuntu (cert snakeoil):
# el cliente conecta con sslmode=require (canal cifrado; la identidad del
# servidor no se verifica, la protección de acceso es la contraseña).
# Nota de operación: falta abrir 5432 en el firewall de la nube (NSG).
db_enable_remote_ro() {
    step "Acceso remoto de solo lectura (visor del Pi)"
    local u="modulinkr_ro" d="$MODULINKR_DB_NAME"
    local pesc="${MODULINKR_DB_RO_PASSWORD//\'/\'\'}"

    if _psql_super -tAc "SELECT 1 FROM pg_roles WHERE rolname='$u'" | grep -q 1; then
        _psql_super -c "ALTER ROLE \"$u\" WITH LOGIN PASSWORD '$pesc'"
        ok "Rol '$u' ya existía; contraseña sincronizada"
    else
        _psql_super -c "CREATE ROLE \"$u\" WITH LOGIN PASSWORD '$pesc'"
        ok "Rol '$u' creado"
    fi

    _psql_super -d "$d" <<SQL
GRANT CONNECT ON DATABASE "$d" TO "$u";
GRANT USAGE ON SCHEMA public TO "$u";
GRANT SELECT ON ALL TABLES IN SCHEMA public TO "$u";
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO "$u";
SQL
    ok "Permisos de solo lectura concedidos a '$u'"

    # Listener en todas las interfaces (drop-in propio, separado del de
    # tuning porque aquel se reescribe entero en cada pasada).
    local confd="${PG_CONFD:-/etc/postgresql/$PG_VERSION/main/conf.d}"
    if [ ! -f "$confd/98-modulinkr-remote.conf" ]; then
        cat > "$confd/98-modulinkr-remote.conf" <<EOF
# Acceso remoto de solo lectura para el visor del Pi. Generado por el
# instalador; la restricción de quién entra vive en pg_hba.conf.
listen_addresses = '*'
EOF
        ok "listen_addresses='*' en conf.d/98-modulinkr-remote.conf"
    else
        ok "Drop-in de acceso remoto ya presente"
    fi

    # pg_hba: solo el rol ro, solo esta base, solo con TLS y scram.
    local hba="/etc/postgresql/$PG_VERSION/main/pg_hba.conf"
    if ! grep -q "hostssl $d $u" "$hba"; then
        backup_once "$hba"
        cat >> "$hba" <<EOF

# ModuLinkr: visor del Pi, solo lectura (generado por el instalador)
hostssl $d $u 0.0.0.0/0 scram-sha-256
hostssl $d $u ::/0      scram-sha-256
EOF
        ok "Regla hostssl añadida a pg_hba.conf"
    else
        ok "Regla hostssl ya presente en pg_hba.conf"
    fi

    # listen_addresses requiere reinicio (no basta reload).
    systemctl restart "postgresql@$PG_VERSION-main" 2>/dev/null || systemctl restart postgresql
    ok "PostgreSQL reiniciado con el listener remoto"
    warn "Recordatorio: abrir el puerto 5432/tcp en el firewall de la nube (NSG de Azure)"
    log "Conexión del visor: host=<dominio de la VM> user=$u sslmode=require"
}

# install_database: orquesta el módulo completo.
install_database() {
    require_root
    db_load_or_make_password
    db_load_or_make_ro_password
    db_add_repos
    db_install_packages
    db_ensure_swap
    db_ensure_running
    db_create_role_and_db
    db_apply_tuning
    db_enable_timescaledb
    db_run_migrations
    db_grant_app
    db_enable_remote_ro
    db_save_env
    step "Base de datos lista"
    log "Base '$MODULINKR_DB_NAME', rol '$MODULINKR_DB_USER'. Conexión local:"
    log "  psql -h localhost -U $MODULINKR_DB_USER -d $MODULINKR_DB_NAME"
}
