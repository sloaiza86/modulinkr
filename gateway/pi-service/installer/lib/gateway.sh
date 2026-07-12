# gateway.sh
# Módulo del instalador del gateway ModuLinkr (lado Pi, Raspberry Pi OS). Instala
# dependencias, crea un venv dedicado, recoge la configuración (serie, red LoRa,
# seguridad AES-CCM, broker MQTT), guarda todo (incluidos secretos) en
# /etc/modulinkr/gateway.env con permisos de root, y deja el servicio systemd
# corriendo. Se carga con source, no se ejecuta directamente.

MODULINKR_ETC="/etc/modulinkr"
GW_ENV_FILE="$MODULINKR_ETC/gateway.env"
GW_UNIT="/etc/systemd/system/modulinkr-gateway.service"

# gw_load_env: idempotencia de secretos. Si ya existe una instalación previa,
# carga sus valores para que ask/ask_secret no vuelvan a preguntar lo definido.
gw_load_env() {
    [ -f "$GW_ENV_FILE" ] || return 0
    # shellcheck disable=SC1090
    set -a; . "$GW_ENV_FILE"; set +a
    log "Config previa cargada desde $GW_ENV_FILE (reejecución)"
}

# gw_port_label PATH: etiqueta legible de un puerto. Si es un enlace de
# /dev/serial/by-id (nombra el chip USB del aparato), muestra ese nombre y a qué
# dispositivo apunta; si es un tty crudo, muestra la ruta.
gw_port_label() {
    local p="$1"
    if [ -L "$p" ]; then
        printf "%s  (%s)" "$p" "$(readlink -f "$p" 2>/dev/null || echo '?')"
    else
        printf "%s" "$p"
    fi
}

# gw_pick_serial_port: fija MODULINKR_PORT. Si viene del config lo respeta. Si no,
# detecta los puertos USB (prefiere /dev/serial/by-id, que da nombres estables e
# identifica el chip). La detección NO es fiable por sí sola (cualquier adaptador
# USB-serie aparece igual), así que siempre confirma con el usuario.
gw_pick_serial_port() {
    if [ -n "${MODULINKR_PORT:-}" ]; then ok "Puerto (de config): $MODULINKR_PORT"; return 0; fi
    local cands=() c
    if [ -d /dev/serial/by-id ]; then
        for c in /dev/serial/by-id/*; do [ -e "$c" ] && cands+=("$c"); done
    fi
    if [ "${#cands[@]}" -eq 0 ]; then
        for c in /dev/ttyUSB* /dev/ttyACM*; do [ -e "$c" ] && cands+=("$c"); done
    fi

    if [ "${#cands[@]}" -eq 0 ]; then
        warn "No se detectó ningún puerto USB. ¿Está conectado el Heltec?"
        gw_ask_required MODULINKR_PORT "Ruta del puerto serie del Heltec" "/dev/ttyUSB0"
        return 0
    fi

    if [ "${ASSUME_YES:-0}" = "1" ]; then
        MODULINKR_PORT="${cands[0]}"; ok "Puerto: $MODULINKR_PORT"; return 0
    fi

    if [ "${#cands[@]}" -eq 1 ]; then
        echo "Se detectó un puerto USB-serie:"
        printf "  %s\n" "$(gw_port_label "${cands[0]}")"
        local r
        read -r -p "¿Es este el Heltec? [S/n]: " r || true
        case "${r:-s}" in
            [nN]*) gw_ask_required MODULINKR_PORT "Escribe la ruta del puerto del Heltec" "" ;;
            *)     MODULINKR_PORT="${cands[0]}" ;;
        esac
    else
        echo "Se detectaron varios puertos USB-serie. Indica cuál es el Heltec:"
        local i=1; for c in "${cands[@]}"; do printf "  %d) %s\n" "$i" "$(gw_port_label "$c")"; i=$((i+1)); done
        local choice
        read -r -p "Selección [1]: " choice || true
        choice="${choice:-1}"
        MODULINKR_PORT="${cands[$((choice-1))]:-${cands[0]}}"
    fi
    ok "Puerto del Heltec: $MODULINKR_PORT"
}

# gw_ask_key: fija MODULINKR_SEC_ENABLED y MODULINKR_SEC_KEY. Pregunta primero si
# se activa el cifrado de radio (AES-CCM) y, solo si se activa, pide la clave (32
# hex, idéntica en el gateway y en los nodos) sin eco y confirmada. Reutiliza la
# guardada de una instalación previa.
gw_ask_key() {
    local cur k1 k2 r
    cur="${MODULINKR_SEC_KEY:-}"
    if [ -n "$cur" ]; then
        printf '%s' "$cur" | grep -Eq '^[0-9A-Fa-f]{32}$' \
            || die "MODULINKR_SEC_KEY guardada no es hexadecimal de 32 caracteres. Corrige $GW_ENV_FILE."
        MODULINKR_SEC_ENABLED=1
        ok "Clave de red reutilizada de la configuración previa"
        return 0
    fi
    if [ "${ASSUME_YES:-0}" = "1" ]; then
        MODULINKR_SEC_ENABLED=0; MODULINKR_SEC_KEY=""; return 0
    fi
    echo "Cifrado de radio (AES-CCM): protege las tramas LoRa entre gateway y nodos."
    read -r -p "¿Activar el cifrado de radio? (igual en gateway y nodos) [S/n]: " r || true
    case "${r:-s}" in
        [nN]*) MODULINKR_SEC_ENABLED=0; MODULINKR_SEC_KEY=""
               warn "Cifrado de radio desactivado."
               return 0 ;;
    esac
    echo "La clave es un secreto de 32 caracteres hexadecimales, idéntico en el"
    echo "gateway y en todos los nodos."
    while :; do
        read -r -s -p "Clave de red: " k1 || true; echo
        if ! printf '%s' "$k1" | grep -Eq '^[0-9A-Fa-f]{32}$'; then
            warn "La clave debe tener 32 caracteres hexadecimales (0-9, A-F)."; continue
        fi
        read -r -s -p "Repite la clave para confirmar: " k2 || true; echo
        if [ "$k1" = "$k2" ]; then
            MODULINKR_SEC_KEY="$k1"; MODULINKR_SEC_ENABLED=1
            ok "Cifrado de radio activado"
            return 0
        fi
        warn "Las claves no coinciden. Vuelve a intentarlo."
    done
}

# gw_ask_required VAR "PREGUNTA" [DEFAULT]: como ask, pero no acepta vacío.
gw_ask_required() {
    local __var="$1" prompt="$2" def="${3:-}" cur
    ask "$__var" "$prompt" "$def"
    eval "cur=\${$__var:-}"
    while [ -z "$cur" ]; do
        [ "${ASSUME_YES:-0}" = "1" ] && die "Falta '$__var' (obligatorio). Defínelo en el config."
        warn "Este dato es obligatorio."
        read -r -p "$prompt: " cur || true
        eval "$__var=\"\$cur\""
    done
}

# gw_ask_tls: separa dos decisiones que suelen confundirse. Primero el cifrado
# (TLS sí o no); luego, con cifrado, cómo se verifica la identidad del broker
# (su certificado). Fija MODULINKR_MQTT_TLS, _CAFILE y _TLS_INSECURE.
gw_ask_tls() {
    if [ -n "${MODULINKR_MQTT_TLS:-}" ]; then
        [ "$MODULINKR_MQTT_TLS" = "1" ] && ok "TLS activado (de config)" || ok "TLS desactivado (de config)"
    elif [ "${ASSUME_YES:-0}" = "1" ]; then
        MODULINKR_MQTT_TLS=1
    else
        local r
        echo "Cifrado: TLS protege todo el tráfico MQTT. En el puerto 8883 es lo habitual."
        read -r -p "¿Cifrar la conexión con TLS? [S/n]: " r || true
        case "${r:-s}" in [nN]*) MODULINKR_MQTT_TLS=0 ;; *) MODULINKR_MQTT_TLS=1 ;; esac
    fi

    MODULINKR_MQTT_CAFILE="${MODULINKR_MQTT_CAFILE:-}"
    MODULINKR_MQTT_TLS_INSECURE="${MODULINKR_MQTT_TLS_INSECURE:-0}"
    export MODULINKR_MQTT_TLS MODULINKR_MQTT_CAFILE MODULINKR_MQTT_TLS_INSECURE

    [ "$MODULINKR_MQTT_TLS" = "1" ] || return 0
    # Estrategia ya fijada por config, o modo no interactivo: no preguntar.
    { [ -n "$MODULINKR_MQTT_CAFILE" ] || [ "$MODULINKR_MQTT_TLS_INSECURE" = "1" ] || [ "${ASSUME_YES:-0}" = "1" ]; } && return 0

    echo "Identidad del broker: cómo comprobar que su certificado es de fiar."
    echo "  1) Autoridades de confianza del sistema   (certificado público, p. ej. Let's Encrypt)"
    echo "  2) Un certificado que indicaré            (broker autofirmado o CA propia)"
    echo "  3) Sin comprobación                       (solo para pruebas, inseguro)"
    local opt
    read -r -p "Opción [1]: " opt || true
    case "${opt:-1}" in
        2) gw_ask_required MODULINKR_MQTT_CAFILE "Ruta del certificado del broker (.crt o .pem)" ;;
        3) MODULINKR_MQTT_TLS_INSECURE=1
           warn "La identidad del broker no se comprobará. Usar solo en pruebas." ;;
        *) : ;;
    esac
    export MODULINKR_MQTT_CAFILE MODULINKR_MQTT_TLS_INSECURE
}

# gather_gateway: pregunta SOLO lo que el usuario conoce (broker, credenciales,
# clave de red, cifrado). Lo demás (usuario, venv, buffer) se deriva o detecta.
gather_gateway() {
    GW_USER="${SUDO_USER:-$(id -un)}"
    id "$GW_USER" >/dev/null 2>&1 || GW_USER="$(id -un)"
    GW_HOME="$(eval echo "~$GW_USER")"
    GW_VENV="$APP_DIR/.venv"
    MODULINKR_DB="${MODULINKR_DB:-$GW_HOME/modulinkr_buffer.db}"
    export GW_USER GW_HOME GW_VENV MODULINKR_DB
    log "El servicio correrá como '$GW_USER'; entorno Python en $GW_VENV"

    step "Radio LoRa (Heltec)"
    echo "El gateway se comunica con la radio LoRa por USB."
    gw_pick_serial_port
    echo
    ask MODULINKR_NETWORK_ID "Identificador de red, debe coincidir con los nodos" "1"
    gw_ask_key

    step "Broker MQTT"
    echo "Servidor al que el gateway publica la telemetría."
    gw_ask_required MODULINKR_MQTT_HOST "Dirección del broker (host o IP)"
    ask        MODULINKR_MQTT_PORT "Puerto del broker" "8883"
    ask        MODULINKR_MQTT_USER "Usuario MQTT" "modulinkr"
    ask_secret MODULINKR_MQTT_PASS "Contraseña MQTT"
    gw_ask_tls
}

# gw_install_packages: dependencias del sistema. python3-serial y
# python3-cryptography vienen de apt a propósito, para no compilar cryptography
# en el Pi (el venv las verá con --system-site-packages).
gw_install_packages() {
    step "Dependencias del sistema"
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y python3 python3-venv python3-pip \
                       python3-serial python3-cryptography >/dev/null
    ok "Paquetes base instalados (python3, venv, pyserial, cryptography)"
}

# gw_setup_venv: crea el venv dedicado (propio del gateway) y le pone paho-mqtt.
gw_setup_venv() {
    step "Entorno Python dedicado"
    if [ ! -d "$GW_VENV" ]; then
        # --system-site-packages: el venv ve pyserial y cryptography de apt
        # (evita compilar cryptography en el Pi) y añade paho-mqtt encima.
        sudo -u "$GW_USER" -H python3 -m venv --system-site-packages "$GW_VENV"
        ok "Venv creado en $GW_VENV"
    else
        ok "Venv ya existe en $GW_VENV (se reutiliza)"
    fi
    sudo -u "$GW_USER" -H "$GW_VENV/bin/pip" install --quiet --upgrade pip
    sudo -u "$GW_USER" -H "$GW_VENV/bin/pip" install --quiet paho-mqtt pyserial
    ok "paho-mqtt instalado en el venv"
}

# gw_write_env: escribe la config y los secretos en un archivo solo-root.
gw_write_env() {
    step "Configuración y secretos"
    install -d -m 700 "$MODULINKR_ETC"
    umask 077
    {
        echo "# Config del gateway ModuLinkr. Generado por el instalador."
        echo "# Solo root. No versionar (está en .gitignore)."
        echo "MODULINKR_PORT=$MODULINKR_PORT"
        echo "MODULINKR_NETWORK_ID=$MODULINKR_NETWORK_ID"
        echo "MODULINKR_DB=$MODULINKR_DB"
        echo "MODULINKR_SEC_ENABLED=$MODULINKR_SEC_ENABLED"
        echo "MODULINKR_SEC_KEY=$MODULINKR_SEC_KEY"
        if [ -n "${MODULINKR_MQTT_HOST:-}" ]; then
            echo "MODULINKR_MQTT_HOST=$MODULINKR_MQTT_HOST"
            echo "MODULINKR_MQTT_PORT=${MODULINKR_MQTT_PORT:-8883}"
            echo "MODULINKR_MQTT_USER=${MODULINKR_MQTT_USER:-}"
            echo "MODULINKR_MQTT_PASS=${MODULINKR_MQTT_PASS:-}"
            echo "MODULINKR_MQTT_TLS=${MODULINKR_MQTT_TLS:-1}"
            echo "MODULINKR_MQTT_CAFILE=${MODULINKR_MQTT_CAFILE:-}"
            echo "MODULINKR_MQTT_TLS_INSECURE=${MODULINKR_MQTT_TLS_INSECURE:-0}"
        fi
    } > "$GW_ENV_FILE"
    chmod 600 "$GW_ENV_FILE"
    ok "Guardado en $GW_ENV_FILE (solo root)"
}

# gw_write_unit: genera la unidad systemd con las rutas de esta instalación.
# Sin secretos: todo viene del EnvironmentFile.
gw_write_unit() {
    step "Servicio systemd"
    cat > "$GW_UNIT" <<EOF
[Unit]
# Generado por el instalador de ModuLinkr. El beacon del árbol de rutas depende
# de este proceso: si se cae, la red LoRa se queda sin raíz hasta que systemd lo
# relanza. Por eso Restart=always.
Description=ModuLinkr gateway service (LoRa mesh root, ACK and beacon)
After=multi-user.target
Wants=network-online.target

[Service]
Type=simple
User=$GW_USER
Group=dialout
WorkingDirectory=$APP_DIR
EnvironmentFile=$GW_ENV_FILE
ExecStart=$GW_VENV/bin/python3 $APP_DIR/gateway_service.py
Restart=always
RestartSec=2
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF
    ok "Unidad escrita en $GW_UNIT"
}

gw_enable() {
    step "Arranque del servicio"
    systemctl daemon-reload
    systemctl enable modulinkr-gateway >/dev/null 2>&1 || true
    systemctl restart modulinkr-gateway
    sleep 1
    if systemctl is-active --quiet modulinkr-gateway; then
        ok "modulinkr-gateway activo"
    else
        warn "El servicio no quedó activo; revisar: journalctl -u modulinkr-gateway -n 40"
    fi
}

# install_gateway: orquesta el módulo completo.
install_gateway() {
    require_root
    gw_install_packages
    gw_setup_venv
    gw_write_env
    gw_write_unit
    gw_enable
    step "Gateway listo"
    log "App: $APP_DIR   venv: $GW_VENV   usuario: $GW_USER"
    log "Config y secretos: $GW_ENV_FILE"
    if [ -n "${MODULINKR_MQTT_HOST:-}" ]; then
        log "Broker: $MODULINKR_MQTT_HOST:${MODULINKR_MQTT_PORT:-8883} (usuario '${MODULINKR_MQTT_USER:-}')"
    else
        log "Sin broker MQTT: la telemetría se acumula en el buffer local."
    fi
    log "Logs en vivo: journalctl -u modulinkr-gateway -f"
}
