#!/usr/bin/env bash
# lib/consumer.sh
# Componente consumer del ModuLinkr Server Installer: el servicio Python
# que consume del broker MQTT y persiste en PostgreSQL (fase 3 del camino
# del dato). Idempotente: reejecutar actualiza el código, respeta el env
# existente y reinicia el servicio.
#
# Convenciones compartidas con database.sh y mosquitto.sh: pregunta solo
# lo que el operador conoce (broker y credenciales); usuario del servicio,
# venv y rutas se derivan. Secretos en /etc/modulinkr, solo root.

MODULINKR_ETC="${MODULINKR_ETC:-/etc/modulinkr}"
CONSUMER_ENV_FILE="$MODULINKR_ETC/consumer.env"
CONSUMER_DIR="/opt/modulinkr/consumer"
CONSUMER_USER="modulinkr-consumer"
CONSUMER_SERVICE="modulinkr-consumer"

# gather_consumer: datos que el operador conoce. El host del broker es el
# nombre del certificado TLS (el dominio público), no localhost, para que
# la verificación del certificado funcione también desde la propia VM.
gather_consumer() {
    if [ -z "${MODULINKR_MQTT_HOST:-}" ] && [ -f "$CONSUMER_ENV_FILE" ]; then
        # shellcheck disable=SC1090
        . "$CONSUMER_ENV_FILE"
    fi
    ask MODULINKR_MQTT_HOST "Host del broker MQTT (dominio del certificado TLS)" "${MODULINKR_MQTT_HOST:-}"
    [ -n "$MODULINKR_MQTT_HOST" ] || die "El host del broker es obligatorio"
    ask MODULINKR_MQTT_PORT "Puerto del broker" "${MODULINKR_MQTT_PORT:-8883}"
    ask MODULINKR_MQTT_USER "Usuario MQTT" "${MODULINKR_MQTT_USER:-modulinkr}"
    if [ -z "${MODULINKR_MQTT_PASS:-}" ]; then
        ask_secret MODULINKR_MQTT_PASS "Contraseña MQTT"
    else
        ok "Contraseña MQTT conservada del env existente"
    fi
    export MODULINKR_MQTT_HOST MODULINKR_MQTT_PORT MODULINKR_MQTT_USER MODULINKR_MQTT_PASS
}

# consumer_db_credentials: reutiliza las credenciales que el componente
# database dejó en database.env; solo pregunta si no existen.
consumer_db_credentials() {
    if [ -f "$MODULINKR_ETC/database.env" ]; then
        # shellcheck disable=SC1090
        . "$MODULINKR_ETC/database.env"
        ok "Credenciales de la base tomadas de database.env"
    fi
    ask MODULINKR_DB_NAME "Nombre de la base de datos" "${MODULINKR_DB_NAME:-modulinkr}"
    ask MODULINKR_DB_USER "Usuario de la base de datos" "${MODULINKR_DB_USER:-modulinkr}"
    if [ -z "${MODULINKR_DB_PASSWORD:-}" ]; then
        ask_secret MODULINKR_DB_PASSWORD "Contraseña de la base de datos"
    fi
    export MODULINKR_DB_NAME MODULINKR_DB_USER MODULINKR_DB_PASSWORD
}

consumer_install_code() {
    step "Código y venv del consumer"
    apt-get install -y -q python3-venv >/dev/null

    id -u "$CONSUMER_USER" >/dev/null 2>&1 || \
        useradd --system --no-create-home --shell /usr/sbin/nologin "$CONSUMER_USER"

    mkdir -p "$CONSUMER_DIR"
    cp "$SERVER_DIR/consumer/"*.py "$CONSUMER_DIR/"
    chown -R "$CONSUMER_USER:$CONSUMER_USER" "$CONSUMER_DIR"

    if [ ! -x "$CONSUMER_DIR/.venv/bin/python3" ]; then
        python3 -m venv "$CONSUMER_DIR/.venv"
    fi
    "$CONSUMER_DIR/.venv/bin/pip" install -q --upgrade paho-mqtt psycopg2-binary
    ok "Código en $CONSUMER_DIR, venv con paho-mqtt y psycopg2"
}

consumer_write_env() {
    step "Credenciales del consumer"
    mkdir -p "$MODULINKR_ETC"
    cat > "$CONSUMER_ENV_FILE" <<EOF
MODULINKR_MQTT_HOST=$MODULINKR_MQTT_HOST
MODULINKR_MQTT_PORT=$MODULINKR_MQTT_PORT
MODULINKR_MQTT_USER=$MODULINKR_MQTT_USER
MODULINKR_MQTT_PASS=$MODULINKR_MQTT_PASS
MODULINKR_MQTT_TLS=1
MODULINKR_DB_HOST=127.0.0.1
MODULINKR_DB_NAME=$MODULINKR_DB_NAME
MODULINKR_DB_USER=$MODULINKR_DB_USER
MODULINKR_DB_PASSWORD=$MODULINKR_DB_PASSWORD
EOF
    chmod 600 "$CONSUMER_ENV_FILE"
    ok "Credenciales guardadas en $CONSUMER_ENV_FILE (solo root)"
}

consumer_install_service() {
    step "Servicio systemd del consumer"
    cp "$SERVER_DIR/consumer/systemd/$CONSUMER_SERVICE.service" \
       "/etc/systemd/system/$CONSUMER_SERVICE.service"
    systemctl daemon-reload
    systemctl enable --now "$CONSUMER_SERVICE"
    systemctl restart "$CONSUMER_SERVICE"
    if systemctl is-active --quiet "$CONSUMER_SERVICE"; then
        ok "Servicio $CONSUMER_SERVICE activo"
    else
        warn "El servicio no arrancó. Revisa el registro con: journalctl -u $CONSUMER_SERVICE -n 50"
    fi
}

install_consumer() {
    step "Componente: consumer"
    consumer_db_credentials
    consumer_install_code
    consumer_write_env
    consumer_install_service
}
