# mosquitto.sh
# Módulo del broker MQTT del instalador: Mosquitto con TLS. Con dominio emite un
# certificado Let's Encrypt (certbot) y lo despliega a Mosquitto con un hook de
# renovación; sin dominio genera un certificado autofirmado RSA y avisa. En
# ambos casos configura el listener TLS con autenticación por usuario y
# contraseña y deja el servicio bajo systemd. El certificado vive solo en el
# servidor: los nodos SIM7028 conectan con authmode=0, sin validar (ver
# node-config.md nota v2.3; RSA, no ECDSA, en el caso autofirmado). Idempotente.
# Se carga con `source` desde install.sh.

MQTT_TLS_PORT="${MQTT_TLS_PORT:-8883}"
CERT_DIR="${CERT_DIR:-/etc/mosquitto/certs}"
PASSWD_FILE="${PASSWD_FILE:-/etc/mosquitto/passwd}"
CONF_FILE="${CONF_FILE:-/etc/mosquitto/conf.d/modulinkr.conf}"
RSA_BITS="${RSA_BITS:-2048}"
CA_DAYS="${CA_DAYS:-3650}"
CERT_DAYS="${CERT_DAYS:-3650}"
FORCE_CERTS="${FORCE_CERTS:-0}"
LE_DIR="${LE_DIR:-/etc/letsencrypt}"

MODULINKR_USE_DOMAIN="${MODULINKR_USE_DOMAIN:-}"
MODULINKR_BROKER_DOMAIN="${MODULINKR_BROKER_DOMAIN:-}"
MODULINKR_BROKER_IP="${MODULINKR_BROKER_IP:-}"
MODULINKR_LE_EMAIL="${MODULINKR_LE_EMAIL:-}"
MODULINKR_MQTT_USER="${MODULINKR_MQTT_USER:-}"
MODULINKR_MQTT_PASSWORD="${MODULINKR_MQTT_PASSWORD:-}"

MODULINKR_ETC="${MODULINKR_ETC:-/etc/modulinkr}"
BROKER_ENV_FILE="$MODULINKR_ETC/broker.env"

# Rellenadas por la provisión del certificado.
CERTFILE=""; KEYFILE=""; BROKER_CA_HINT=""

_detect_ip() { hostname -I 2>/dev/null | awk '{print $1}'; }

# gather_broker: identifica el broker por dominio o por IP, y recoge las
# credenciales MQTT. La IP es un camino de primera clase, no un apaño: no todo
# despliegue tiene dominio.
gather_broker() {
    ask MODULINKR_USE_DOMAIN "¿Configurar el broker con un dominio? (sí/no)" "no"
    case "${MODULINKR_USE_DOMAIN,,}" in
        y|yes|si|sí|s) MODULINKR_USE_DOMAIN="yes" ;;
        *)             MODULINKR_USE_DOMAIN="no" ;;
    esac

    if [ "$MODULINKR_USE_DOMAIN" = "yes" ]; then
        ask MODULINKR_BROKER_DOMAIN "Dominio del broker (p. ej. modulinkr.loaiza.co)" ""
        [ -n "$MODULINKR_BROKER_DOMAIN" ] || die "Se eligió dominio pero no se indicó ninguno."
        ask MODULINKR_LE_EMAIL "Email para Let's Encrypt (avisos de expiración; vacío = sin email)" ""
        BROKER_ADDR="$MODULINKR_BROKER_DOMAIN"
    else
        ask MODULINKR_BROKER_IP "IP pública del broker" "$(_detect_ip)"
        [ -n "$MODULINKR_BROKER_IP" ] || die "No se indicó IP y no se pudo detectar una."
        BROKER_ADDR="$MODULINKR_BROKER_IP"
        CERT_SAN="IP:$MODULINKR_BROKER_IP,DNS:localhost,IP:127.0.0.1"
    fi

    ask MODULINKR_MQTT_USER "Usuario MQTT" "modulinkr"
    ask_secret MODULINKR_MQTT_PASSWORD "Contraseña MQTT"
    [ -n "$MODULINKR_MQTT_PASSWORD" ] || die "La contraseña MQTT no puede quedar vacía."

    export MODULINKR_USE_DOMAIN MODULINKR_BROKER_DOMAIN MODULINKR_BROKER_IP
    export MODULINKR_LE_EMAIL MODULINKR_MQTT_USER MODULINKR_MQTT_PASSWORD BROKER_ADDR
}

broker_install_packages() {
    step "Instalación de Mosquitto"
    apt-get install -y mosquitto mosquitto-clients openssl >/dev/null
    ok "Mosquitto instalado"
}

# broker_letsencrypt: certificado real vía certbot en modo standalone (usa el
# puerto 80). Si ya existe, no lo reemite; el timer de certbot lo renueva.
broker_letsencrypt() {
    step "Certificado Let's Encrypt para $MODULINKR_BROKER_DOMAIN"
    apt-get install -y certbot >/dev/null
    local live="$LE_DIR/live/$MODULINKR_BROKER_DOMAIN"
    if [ ! -d "$live" ]; then
        local email_arg="--register-unsafely-without-email"
        [ -n "$MODULINKR_LE_EMAIL" ] && email_arg="-m $MODULINKR_LE_EMAIL"
        log "Solicitando certificado (requiere el puerto 80 accesible desde internet)"
        certbot certonly --standalone --non-interactive --agree-tos $email_arg \
            -d "$MODULINKR_BROKER_DOMAIN" \
            || die "Certbot no pudo emitir el certificado. Comprueba que el puerto 80 esté accesible en el NSG de Azure y que el dominio apunte a esta VM."
        ok "Certificado emitido"
    else
        ok "El certificado ya existe; certbot lo renueva por su timer"
    fi
    broker_deploy_le_cert
    broker_install_renewal_hook
    CERTFILE="$CERT_DIR/fullchain.pem"; KEYFILE="$CERT_DIR/privkey.pem"
    BROKER_CA_HINT="confianza pública (Let's Encrypt)"
    warn "Certbot necesita el puerto 80 accesible durante las renovaciones, aproximadamente cada 60 días."
    warn "Mantén abierto el puerto 80 en el NSG de Azure. Comprueba la renovación con: sudo certbot renew --dry-run"
}

# broker_deploy_le_cert: copia el cert de Let's Encrypt a la carpeta de
# Mosquitto con permisos que el usuario 'mosquitto' puede leer (la clave en
# /etc/letsencrypt es solo-root).
broker_deploy_le_cert() {
    install -d -m 755 "$CERT_DIR"
    local live="$LE_DIR/live/$MODULINKR_BROKER_DOMAIN"
    install -o root -g mosquitto -m 644 "$live/fullchain.pem" "$CERT_DIR/fullchain.pem"
    install -o root -g mosquitto -m 640 "$live/privkey.pem"  "$CERT_DIR/privkey.pem"
    ok "Certificado desplegado en $CERT_DIR"
}

# broker_install_renewal_hook: al renovar, certbot vuelve a copiar el cert a
# Mosquitto y reinicia el servicio. Sin esto, Mosquitto seguiría sirviendo el
# viejo. El hook se dispara tras CUALQUIER renovación de certbot, así que filtra
# por dominio ($RENEWED_DOMAINS) y usa la ruta que certbot renovó de verdad
# ($RENEWED_LINEAGE). Reinicia (no recarga): garantiza que carga el cert nuevo
# sea cual sea la versión de Mosquitto; la desconexión es de un instante y los
# nodos reconectan.
broker_install_renewal_hook() {
    local hookdir="$LE_DIR/renewal-hooks/deploy"
    install -d "$hookdir"
    cat > "$hookdir/modulinkr-mosquitto.sh" <<EOF
#!/usr/bin/env bash
# Redespliega el certificado renovado a Mosquitto. Generado por el instalador
# de ModuLinkr. Ejecutado por certbot como deploy-hook tras cada renovación.
set -e
DOMAIN="$MODULINKR_BROKER_DOMAIN"
DEST="$CERT_DIR"
# Actuar solo si la renovación incluye nuestro dominio.
case " \${RENEWED_DOMAINS:-} " in *" \$DOMAIN "*) ;; *) [ -z "\${RENEWED_DOMAINS:-}" ] || exit 0 ;; esac
LINEAGE="\${RENEWED_LINEAGE:-$LE_DIR/live/\$DOMAIN}"
install -o root -g mosquitto -m 644 "\$LINEAGE/fullchain.pem" "\$DEST/fullchain.pem"
install -o root -g mosquitto -m 640 "\$LINEAGE/privkey.pem"  "\$DEST/privkey.pem"
systemctl restart mosquitto
EOF
    chmod +x "$hookdir/modulinkr-mosquitto.sh"
    ok "Hook de renovación instalado"
}

# broker_selfsigned: CA autofirmada RSA y certificado de servidor RSA cuando no
# hay dominio. Server-auth: la identidad del cliente va por usuario y
# contraseña, no por certificado de cliente.
broker_selfsigned() {
    warn "Sin dominio: se generará un certificado AUTOFIRMADO. No es de confianza pública;"
    warn "sirve porque los nodos SIM7028 conectan con authmode=0 (no validan el certificado)."
    step "Certificado autofirmado (RSA)"
    install -d -m 755 "$CERT_DIR"
    local ca_key="$CERT_DIR/ca.key" ca_crt="$CERT_DIR/ca.crt"
    local srv_key="$CERT_DIR/server.key" srv_crt="$CERT_DIR/server.crt"
    local srv_csr="$CERT_DIR/server.csr" srv_ext="$CERT_DIR/server.ext"

    if [ -f "$srv_crt" ] && [ "$FORCE_CERTS" != "1" ]; then
        warn "Ya existe $srv_crt; no se regenera (FORCE_CERTS=1 para rehacerlo)."
    else
        openssl genrsa -out "$ca_key" "$RSA_BITS" 2>/dev/null
        openssl req -x509 -new -nodes -key "$ca_key" -sha256 -days "$CA_DAYS" \
            -subj "/O=ModuLinkr/CN=ModuLinkr Root CA" -out "$ca_crt" 2>/dev/null
        openssl genrsa -out "$srv_key" "$RSA_BITS" 2>/dev/null
        openssl req -new -key "$srv_key" -subj "/O=ModuLinkr/CN=$BROKER_ADDR" \
            -out "$srv_csr" 2>/dev/null
        cat > "$srv_ext" <<EOF
subjectAltName = $CERT_SAN
basicConstraints = CA:FALSE
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth
EOF
        openssl x509 -req -in "$srv_csr" -CA "$ca_crt" -CAkey "$ca_key" \
            -CAcreateserial -out "$srv_crt" -days "$CERT_DAYS" -sha256 \
            -extfile "$srv_ext" 2>/dev/null
        rm -f "$srv_csr" "$srv_ext"
        ok "CA y certificado de servidor generados (SAN: $CERT_SAN)"
    fi
    chown -R root:mosquitto "$CERT_DIR" 2>/dev/null || true
    chmod 644 "$ca_crt" "$srv_crt" 2>/dev/null || true
    chmod 640 "$ca_key" "$srv_key" 2>/dev/null || true
    CERTFILE="$srv_crt"; KEYFILE="$srv_key"
    BROKER_CA_HINT="$ca_crt (autofirmada; para clientes que quieran validar)"
}

broker_provision_cert() {
    if [ "$MODULINKR_USE_DOMAIN" = "yes" ]; then
        broker_letsencrypt
    else
        broker_selfsigned
    fi
}

broker_write_config() {
    step "Configuración de Mosquitto"
    backup_once "$CONF_FILE"
    cat > "$CONF_FILE" <<EOF
# Generado por el instalador de ModuLinkr. Broker MQTT sobre TLS.
per_listener_settings false
allow_anonymous false
password_file $PASSWD_FILE

listener $MQTT_TLS_PORT
certfile $CERTFILE
keyfile $KEYFILE
tls_version tlsv1.2
EOF
    ok "Listener TLS en el puerto $MQTT_TLS_PORT"
}

# broker_set_password: crea o actualiza el usuario MQTT en el password_file.
broker_set_password() {
    step "Credenciales MQTT"
    if [ -f "$PASSWD_FILE" ]; then
        mosquitto_passwd -b "$PASSWD_FILE" "$MODULINKR_MQTT_USER" "$MODULINKR_MQTT_PASSWORD"
    else
        mosquitto_passwd -b -c "$PASSWD_FILE" "$MODULINKR_MQTT_USER" "$MODULINKR_MQTT_PASSWORD"
    fi
    chown root:mosquitto "$PASSWD_FILE" 2>/dev/null || true
    chmod 640 "$PASSWD_FILE"
    ok "Usuario MQTT '$MODULINKR_MQTT_USER' listo"
}

broker_enable() {
    step "Servicio"
    systemctl enable mosquitto >/dev/null 2>&1 || true
    systemctl restart mosquitto
    sleep 1
    if systemctl is-active --quiet mosquitto; then
        ok "Mosquitto activo"
    else
        warn "Mosquitto no quedó activo. Revisa el servicio con: journalctl -u mosquitto -n 40"
    fi
}

broker_save_env() {
    install -d -m 700 "$MODULINKR_ETC"
    cat > "$BROKER_ENV_FILE" <<EOF
# Parámetros del broker ModuLinkr. Generado por el instalador. No versionar.
MODULINKR_BROKER_ADDR=$BROKER_ADDR
MODULINKR_BROKER_TLS_PORT=$MQTT_TLS_PORT
MODULINKR_BROKER_CERT=$CERTFILE
MODULINKR_MQTT_USER=$MODULINKR_MQTT_USER
MODULINKR_MQTT_PASSWORD=$MODULINKR_MQTT_PASSWORD
EOF
    chmod 600 "$BROKER_ENV_FILE"
    ok "Parámetros guardados en $BROKER_ENV_FILE (solo root)"
}

# install_broker: orquesta el módulo completo.
install_broker() {
    require_root
    broker_install_packages
    broker_provision_cert
    broker_write_config
    broker_set_password
    broker_enable
    broker_save_env
    step "Broker listo"
    log "Endpoint TLS: $BROKER_ADDR:$MQTT_TLS_PORT (usuario '$MODULINKR_MQTT_USER')"
    log "Certificado: $CERTFILE"
    log "Los nodos SIM7028 conectan con authmode=0 (sin CA). CA para clientes que validen: $BROKER_CA_HINT"
}
