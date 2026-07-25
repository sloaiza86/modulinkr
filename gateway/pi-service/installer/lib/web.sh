#!/usr/bin/env bash
# lib/web.sh
# Módulo del visor web (pi-web) para el Gateway Installer. Opcional: se
# ofrece tras instalar el gateway y se salta limpio si el operador no lo
# quiere o si el árbol pi-web no está junto a pi-service. Idempotente:
# reejecutar actualiza dependencias y assets, respeta el env existente y
# reinicia el servicio.
#
# Mismas convenciones que el resto: pregunta solo lo que el operador sabe
# (credenciales del visor y acceso de solo lectura a la base de la VM),
# secretos en /etc/modulinkr/web.env (solo root), unidad systemd generada
# con las rutas de esta instalación.

WEB_ENV_FILE="$MODULINKR_ETC/web.env"
WEB_UNIT="/etc/systemd/system/modulinkr-web.service"

# El árbol pi-web viaja junto a pi-service (mismo scp a la home del Pi).
WEB_DIR="$(cd "$APP_DIR/.." && pwd)/pi-web"
WEB_VENV="$WEB_DIR/.venv"

# Certificado TLS del visor (autofirmado, generado en la primera instalación
# y conservado después). Bajo el árbol del visor, propiedad del usuario del
# servicio, para que uvicorn pueda leerlo sin permisos de root.
WEB_TLS_DIR="$WEB_DIR/.tls"
WEB_CERT="$WEB_TLS_DIR/web-cert.pem"
WEB_KEY="$WEB_TLS_DIR/web-key.pem"

WEB_WANTED=0

web_load_env() {
    if [ -f "$WEB_ENV_FILE" ]; then
        # shellcheck disable=SC1090
        . "$WEB_ENV_FILE"
        ok "Config previa del visor cargada de $WEB_ENV_FILE"
    fi
}

gather_web() {
    step "Visor web (opcional)"
    if [ ! -d "$WEB_DIR" ]; then
        warn "No hay árbol pi-web junto a pi-service ($WEB_DIR); visor omitido"
        return 0
    fi
    if ! confirm "¿Instalar el visor web del gateway?" "y"; then
        log "Visor omitido a petición del operador"
        return 0
    fi
    WEB_WANTED=1
    web_load_env

    ask MODULINKR_WEB_PORT "Puerto del visor (HTTPS)" "${MODULINKR_WEB_PORT:-8443}"
    ask MODULINKR_WEB_USER "Usuario del visor" "${MODULINKR_WEB_USER:-admin}"
    if [ -z "${MODULINKR_WEB_PASS:-}" ]; then
        ask_secret MODULINKR_WEB_PASS "Contraseña del visor"
    else
        ok "Contraseña del visor conservada del env existente"
    fi

    # Acceso al histórico: rol de solo lectura provisionado por el
    # instalador del servidor (db_enable_remote_ro). El host suele ser el
    # mismo que el broker MQTT, por eso se ofrece como default.
    ask MODULINKR_PG_HOST "Host del PostgreSQL de la VM" "${MODULINKR_PG_HOST:-${MODULINKR_MQTT_HOST:-}}"
    if [ -z "${MODULINKR_PG_PASSWORD:-}" ]; then
        ask_secret MODULINKR_PG_PASSWORD "Contraseña del rol modulinkr_ro (en database.env de la VM)"
    else
        ok "Contraseña de modulinkr_ro conservada del env existente"
    fi
    export MODULINKR_WEB_PORT MODULINKR_WEB_USER MODULINKR_WEB_PASS \
           MODULINKR_PG_HOST MODULINKR_PG_PASSWORD
}

web_setup_venv() {
    step "Entorno Python del visor"
    if [ ! -d "$WEB_VENV" ]; then
        run sudo -u "$GW_USER" -H python3 -m venv "$WEB_VENV"
        ok "Venv creado en $WEB_VENV"
    else
        ok "Venv ya existe en $WEB_VENV (se reutiliza)"
    fi
    run sudo -u "$GW_USER" -H "$WEB_VENV/bin/pip" install --upgrade pip
    run sudo -u "$GW_USER" -H "$WEB_VENV/bin/pip" install \
        fastapi uvicorn psycopg2-binary pyserial paho-mqtt
    ok "fastapi, uvicorn, psycopg2, pyserial y paho-mqtt en el venv del visor"
}

web_fetch_vendor() {
    step "Assets del frontend"
    # Con Internet se (re)descargan a la versión fijada; sin Internet se
    # acepta lo ya presente (instalación previa) y solo se avisa si falta.
    if sudo -u "$GW_USER" -H bash "$WEB_DIR/get_vendor.sh"; then
        ok "Assets descargados a static/vendor"
    elif [ -f "$WEB_DIR/static/vendor/vis-network.min.js" ] && \
         [ -f "$WEB_DIR/static/vendor/echarts.min.js" ]; then
        warn "Sin descarga (¿sin Internet?); se usan los assets ya presentes"
    else
        warn "Assets no disponibles: el visor arrancará pero sin mapa ni gráficos."
        warn "Reejecutar $WEB_DIR/get_vendor.sh con Internet."
    fi
}

web_make_cert() {
    step "Certificado TLS del visor"
    # El visor sirve HTTPS con un certificado autofirmado. Se genera la
    # primera vez y se conserva en reinstalaciones: regenerarlo invalidaría
    # el que el operador ya haya marcado como de confianza en sus
    # dispositivos. Propiedad del usuario del servicio (lo lee uvicorn al
    # arrancar), no de root, así que no vive en /etc/modulinkr.
    if [ -f "$WEB_CERT" ] && [ -f "$WEB_KEY" ]; then
        ok "Certificado ya presente en $WEB_CERT (se conserva)"
        return 0
    fi
    if ! command -v openssl >/dev/null 2>&1; then
        warn "openssl no está: el visor no arrancará con TLS. Instalar openssl y reejecutar."
        return 0
    fi
    run sudo -u "$GW_USER" -H mkdir -p "$WEB_TLS_DIR"
    local host ip san
    host="$(hostname)"
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
    # SAN por nombre mDNS (la vía de acceso normal) más la IP actual. La IP
    # puede cambiar por DHCP; el acceso por <host>.local no depende de ella.
    san="DNS:${host}.local,DNS:${host},DNS:localhost,IP:127.0.0.1"
    [ -n "$ip" ] && san="${san},IP:${ip}"
    if sudo -u "$GW_USER" -H openssl req -x509 -newkey rsa:2048 -nodes \
            -keyout "$WEB_KEY" -out "$WEB_CERT" -days 3650 \
            -subj "/O=ModuLinkr/CN=${host}.local" \
            -addext "subjectAltName=${san}" >/dev/null 2>&1; then
        chmod 600 "$WEB_KEY"  2>/dev/null || true
        chmod 644 "$WEB_CERT" 2>/dev/null || true
        ok "Certificado autofirmado en $WEB_CERT (SAN: ${san}, 3650 días)"
    else
        warn "openssl falló al generar el certificado; el visor no arrancará con TLS"
    fi
}

web_write_env() {
    step "Configuración y secretos del visor"
    install -d -m 700 "$MODULINKR_ETC"
    umask 077
    {
        echo "# Config del visor web ModuLinkr. Generado por el instalador."
        echo "# Solo root. No versionar."
        echo "MODULINKR_DB=$MODULINKR_DB"
        echo "MODULINKR_WEB_PORT=$MODULINKR_WEB_PORT"
        echo "MODULINKR_WEB_USER=$MODULINKR_WEB_USER"
        echo "MODULINKR_WEB_PASS=$MODULINKR_WEB_PASS"
        # Clave de firma de las cookies de sesión: autogenerada, no se
        # pregunta; se conserva del env existente en reinstalaciones para
        # no invalidar las sesiones abiertas.
        echo "MODULINKR_WEB_SECRET=${MODULINKR_WEB_SECRET:-$(openssl rand -hex 32)}"
        echo "MODULINKR_WEB_ONLINE_S=${MODULINKR_WEB_ONLINE_S:-60}"
        # Certificado TLS: uvicorn los recibe por --ssl-*; el visor los usa
        # para la cookie segura y para servir el cert en /cert.
        echo "MODULINKR_WEB_CERT=$WEB_CERT"
        echo "MODULINKR_WEB_KEY=$WEB_KEY"
        # Puerto serie del Heltec: el comisionamiento por USB lo excluye
        # de la detección (abrirlo resetearía la radio del gateway).
        if [ -n "${MODULINKR_PORT:-}" ]; then
            echo "MODULINKR_GATEWAY_PORT=$MODULINKR_PORT"
        fi
        if [ -n "${MODULINKR_PG_HOST:-}" ]; then
            echo "MODULINKR_PG_HOST=$MODULINKR_PG_HOST"
            echo "MODULINKR_PG_PORT=${MODULINKR_PG_PORT:-5432}"
            echo "MODULINKR_PG_DB=${MODULINKR_PG_DB:-modulinkr}"
            echo "MODULINKR_PG_USER=${MODULINKR_PG_USER:-modulinkr_ro}"
            echo "MODULINKR_PG_PASSWORD=$MODULINKR_PG_PASSWORD"
        fi
    } > "$WEB_ENV_FILE"
    chmod 600 "$WEB_ENV_FILE"
    ok "Guardado en $WEB_ENV_FILE (solo root)"
}

web_write_sudoers() {
    step "Permisos sudo acotados del visor"
    # Las páginas de configuración del visor ejecutan un puñado de acciones
    # privilegiadas acotadas. La regla protege frente a una sesión web
    # comprometida (la API no puede ejecutar otra cosa); no pretende aislar
    # al usuario del servicio, que es dueño de los scripts. set_mqtt.sh y
    # set_db.sh reciben los valores por stdin (sin argumentos), así que su
    # entrada en la regla no lleva comodín.
    chmod +x "$APP_DIR/set_lora_port.sh" "$APP_DIR/flash_heltec.sh" \
             "$APP_DIR/set_mqtt.sh" "$APP_DIR/set_db.sh" \
             "$APP_DIR/flash_nodo.sh" "$APP_DIR/get_net.sh" \
             "$APP_DIR/set_wifi.sh" 2>/dev/null || true
    cat > /etc/sudoers.d/modulinkr-web <<EOF
# Generado por el instalador de ModuLinkr. Acciones privilegiadas de las
# páginas de configuración del visor (radio LoRa, MQTT, base de datos,
# firmware del nodo, parámetros de red, red WiFi). No editar a mano.
$GW_USER ALL=(root) NOPASSWD: $APP_DIR/set_lora_port.sh *, $APP_DIR/flash_heltec.sh, $APP_DIR/set_mqtt.sh, $APP_DIR/set_db.sh, $APP_DIR/flash_nodo.sh *, $APP_DIR/get_net.sh, $APP_DIR/set_wifi.sh scan, $APP_DIR/set_wifi.sh connect
EOF
    chmod 440 /etc/sudoers.d/modulinkr-web
    if visudo -cf /etc/sudoers.d/modulinkr-web >/dev/null 2>&1; then
        ok "Regla sudo en /etc/sudoers.d/modulinkr-web"
    else
        rm -f /etc/sudoers.d/modulinkr-web
        warn "Regla sudo inválida, retirada; la página de la radio quedará en solo lectura"
    fi
}

web_grant_journal() {
    step "Lectura del journal para el visor"
    # La página "Herramientas de depuración" lee el journal del servicio del
    # gateway (journalctl) para el visor de journaling y el de tramas
    # modbus-debug. Leer el journal de un servicio del sistema exige el grupo
    # systemd-journal; se añade al usuario del servicio. El servicio recoge
    # el grupo nuevo al reiniciarse (web_enable). Idempotente.
    if usermod -aG systemd-journal "$GW_USER" 2>/dev/null; then
        ok "Usuario $GW_USER en el grupo systemd-journal"
    else
        warn "No se pudo añadir $GW_USER a systemd-journal; el visor de "
        warn "journaling quedará vacío hasta concederlo."
    fi
}

web_write_unit() {
    step "Servicio systemd del visor"
    cat > "$WEB_UNIT" <<EOF
[Unit]
# Generado por el instalador de ModuLinkr. El visor es independiente del
# servicio del gateway: lee su buffer en solo lectura, así que puede caerse
# o reiniciarse sin tocar la red LoRa.
Description=ModuLinkr web viewer (network state, topology, data charts)
After=multi-user.target
Wants=network-online.target

[Service]
Type=simple
User=$GW_USER
WorkingDirectory=$WEB_DIR
EnvironmentFile=$WEB_ENV_FILE
ExecStart=$WEB_VENV/bin/uvicorn web_service:app --host 0.0.0.0 --port \${MODULINKR_WEB_PORT} --ssl-certfile \${MODULINKR_WEB_CERT} --ssl-keyfile \${MODULINKR_WEB_KEY}
Restart=always
RestartSec=3
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    ok "Unidad escrita en $WEB_UNIT"
}

web_enable() {
    step "Arranque del visor"
    systemctl daemon-reload
    systemctl enable modulinkr-web >/dev/null 2>&1 || true
    systemctl restart modulinkr-web
    sleep 1
    if systemctl is-active --quiet modulinkr-web; then
        ok "modulinkr-web activo en el puerto $MODULINKR_WEB_PORT"
        log "Visor: https://$(hostname).local:$MODULINKR_WEB_PORT"
    else
        warn "El visor no quedó activo; revisar: journalctl -u modulinkr-web -n 40"
    fi
}

install_web() {
    [ "$WEB_WANTED" = "1" ] || return 0
    web_setup_venv
    web_fetch_vendor
    web_make_cert
    web_write_env
    web_write_sudoers
    web_grant_journal
    web_write_unit
    web_enable
}
