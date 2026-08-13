#!/usr/bin/env bash
# ModuLinkr Gateway Installer
# Instalador del gateway ModuLinkr (lado Raspberry Pi). Instala dependencias,
# crea un venv dedicado, pregunta la configuración (serie, red, seguridad
# AES-CCM y broker MQTT, con las contraseñas confirmadas), guarda los secretos
# en /etc/modulinkr/gateway.env (solo root) y deja el servicio systemd
# corriendo. Ofrece además el visor web (pi-web, servicio modulinkr-web) si
# su árbol está junto a pi-service. Reejecutable sin romper una instalación
# previa.
#
# Uso:
#   sudo ./install.sh                       instalación interactiva
#   sudo ./install.sh -y                    sin preguntas (usa config y defaults)
#   sudo ./install.sh --config mi.conf      con un archivo de ajustes no sensibles
set -euo pipefail

INSTALLER_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export INSTALLER_DIR
# El código del servicio vive en el directorio padre del instalador (pi-service).
APP_DIR="$(cd "$INSTALLER_DIR/.." && pwd)"
export APP_DIR

CONFIG_FILE=""
export ASSUME_YES=0

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --config)  CONFIG_FILE="$2"; shift 2 ;;
        -y|--yes)  ASSUME_YES=1; shift ;;
        -h|--help) usage 0 ;;
        *) echo "[ERROR] Argumento no reconocido: $1" >&2; usage 2 ;;
    esac
done

# shellcheck source=lib/common.sh
. "$INSTALLER_DIR/lib/common.sh"
# shellcheck source=lib/gateway.sh
. "$INSTALLER_DIR/lib/gateway.sh"
# shellcheck source=lib/web.sh
. "$INSTALLER_DIR/lib/web.sh"

load_config "$CONFIG_FILE"
require_ubuntu

banner() {
    printf "%b" "$C_STEP"
    cat <<'EOF'
  __  __         _       _    _       _
 |  \/  |___  __| |_  _ | |  (_)_ _ | |___ _ _
 | |\/| / _ \/ _` | || || |__| | ' \| / / '_|
 |_|  |_\___/\__,_|\_,_||____|_|_||_|_\_\_|
        Gateway Installer (Raspberry Pi)
EOF
    printf "%b\n" "$C_RESET"
}

main() {
    banner
    require_root
    gw_load_env         # reusa secretos de una instalación previa
    gather_gateway      # pregunta lo que falte (contraseñas confirmadas)
    gather_web          # visor web opcional (pi-web junto a pi-service)
    install_gateway     # instala, configura y arranca
    install_web         # visor, si se pidió
}

main "$@"
