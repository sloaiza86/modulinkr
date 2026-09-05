#!/usr/bin/env bash
# ModuLinkr Server Installer
# Instalador del servidor cloud de ModuLinkr. Instala, por separado o juntos,
# el broker MQTT (Mosquitto con TLS), la base de datos (PostgreSQL) y el
# consumidor cloud (MQTT a PostgreSQL). Pensado para una VM Ubuntu de poca
# memoria y para ser reejecutable sin romper una instalación previa.
#
# Uso:
#   sudo ./install.sh                         menú interactivo
#   sudo ./install.sh --components database    solo la base de datos
#   sudo ./install.sh --components all -y      todo, sin preguntas
#   sudo ./install.sh --config mi.conf --components all
#
# Componentes: broker | database | consumer | all
set -euo pipefail

SERVER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export SERVER_DIR

CONFIG_FILE=""
COMPONENTS=""
export ASSUME_YES=0

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --config)     CONFIG_FILE="$2"; shift 2 ;;
        --components) COMPONENTS="$2"; shift 2 ;;
        -y|--yes)     ASSUME_YES=1; shift ;;
        -h|--help)    usage 0 ;;
        *) echo "[ERROR] Argumento no reconocido: $1" >&2; usage 2 ;;
    esac
done

# shellcheck source=lib/common.sh
. "$SERVER_DIR/lib/common.sh"
# shellcheck source=lib/database.sh
. "$SERVER_DIR/lib/database.sh"
# shellcheck source=lib/mosquitto.sh
. "$SERVER_DIR/lib/mosquitto.sh"
# shellcheck source=lib/consumer.sh
. "$SERVER_DIR/lib/consumer.sh"

load_config "$CONFIG_FILE"
require_ubuntu

banner() {
    printf "%b" "$C_STEP"
    cat <<'EOF'
  __  __         _       _    _       _
 |  \/  |___  __| |_  _ | |  (_)_ _ | |___ _ _
 | |\/| / _ \/ _` | || || |__| | ' \| / / '_|
 |_|  |_\___/\__,_|\_,_||____|_|_||_|_\_\_|
        Server Installer
EOF
    printf "%b\n" "$C_RESET"
}

# menu: elige componentes si no vinieron por --components.
menu() {
    [ -n "$COMPONENTS" ] && return 0
    if [ "$ASSUME_YES" = "1" ]; then COMPONENTS="all"; return 0; fi
    echo "Selecciona los componentes que quieres instalar:"
    echo "  1) broker    Mosquitto MQTT con TLS"
    echo "  2) database  PostgreSQL 16 y esquema de telemetría"
    echo "  3) consumer  Servicio de ingesta MQTT a PostgreSQL"
    echo "  4) all       broker, database y consumer"
    local choice; read -r -p "Opción [4]: " choice; choice="${choice:-4}"
    case "$choice" in
        1) COMPONENTS="broker" ;;
        2) COMPONENTS="database" ;;
        3) COMPONENTS="consumer" ;;
        4) COMPONENTS="all" ;;
        *) die "Opción no válida: $choice" ;;
    esac
}

# gather_common: datos que comparten los componentes (identidad de la red).
gather_common() {
    ask MODULINKR_NETWORK_NAME "Nombre de la red ModuLinkr" "modulinkr"
    export MODULINKR_NETWORK_NAME
}

# gather_database: detalles de la base antes de instalar.
gather_database() {
    ask MODULINKR_DB_NAME "Nombre de la base de datos" "modulinkr"
    ask MODULINKR_DB_USER "Usuario de la base de datos" "modulinkr"
    export MODULINKR_DB_NAME MODULINKR_DB_USER
    # La contraseña se genera sola si no viene del config; ver database.sh.
}

main() {
    banner
    require_root
    menu

    local want_broker=0 want_db=0 want_consumer=0
    case ",$COMPONENTS," in
        *,all,*)      want_broker=1; want_db=1; want_consumer=1 ;;
        *) case ",$COMPONENTS," in *,broker,*)   want_broker=1 ;; esac
           case ",$COMPONENTS," in *,database,*) want_db=1 ;; esac
           case ",$COMPONENTS," in *,consumer,*) want_consumer=1 ;; esac ;;
    esac
    [ "$want_broker" = 0 ] && [ "$want_db" = 0 ] && [ "$want_consumer" = 0 ] && \
        die "Nada que instalar: componentes '$COMPONENTS'."

    gather_common
    [ "$want_db" = 1 ] && gather_database
    [ "$want_broker" = 1 ] && gather_broker     # definido en mosquitto.sh
    [ "$want_consumer" = 1 ] && gather_consumer # definido en consumer.sh

    # Orden deliberado: el consumer va el último porque reutiliza las
    # credenciales que database deja en database.env.
    [ "$want_broker" = 1 ] && install_broker
    [ "$want_db" = 1 ] && install_database
    [ "$want_consumer" = 1 ] && install_consumer

    step "Instalación completada"
    log "Componentes: $COMPONENTS"
}

main "$@"
