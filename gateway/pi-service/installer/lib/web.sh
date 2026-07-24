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

    ask MODULINKR_WEB_PORT "Puerto del visor" "${MODULINKR_WEB_PORT:-8080}"
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
        fastapi uvicorn psycopg2-binary pyserial
    ok "fastapi, uvicorn, psycopg2 y pyserial en el venv del visor"
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
    # La página "Configurar radio LoRa" ejecuta exactamente dos acciones
    # privilegiadas. La regla protege frente a una sesión web comprometida
    # (la API no puede ejecutar otra cosa); no pretende aislar al usuario
    # del servicio, que es dueño de los scripts.
    chmod +x "$APP_DIR/set_lora_port.sh" "$APP_DIR/flash_heltec.sh" 2>/dev/null || true
    cat > /etc/sudoers.d/modulinkr-web <<EOF
# Generado por el instalador de ModuLinkr. Acciones privilegiadas de la
# página "Configurar radio LoRa" del visor. No editar a mano.
$GW_USER ALL=(root) NOPASSWD: $APP_DIR/set_lora_port.sh *, $APP_DIR/flash_heltec.sh
EOF
    chmod 440 /etc/sudoers.d/modulinkr-web
    if visudo -cf /etc/sudoers.d/modulinkr-web >/dev/null 2>&1; then
        ok "Regla sudo en /etc/sudoers.d/modulinkr-web"
    else
        rm -f /etc/sudoers.d/modulinkr-web
        warn "Regla sudo inválida, retirada; la página de la radio quedará en solo lectura"
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
ExecStart=$WEB_VENV/bin/uvicorn web_service:app --host 0.0.0.0 --port \${MODULINKR_WEB_PORT}
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
        log "Visor: http://$(hostname).local:$MODULINKR_WEB_PORT"
    else
        warn "El visor no quedó activo; revisar: journalctl -u modulinkr-web -n 40"
    fi
}

install_web() {
    [ "$WEB_WANTED" = "1" ] || return 0
    web_setup_venv
    web_fetch_vendor
    web_write_env
    web_write_sudoers
    web_write_unit
    web_enable
}
