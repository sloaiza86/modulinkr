# common.sh
# Utilidades compartidas por los módulos del instalador: registro en pantalla,
# preguntas interactivas, comprobaciones de entorno y ayudas de idempotencia.
# Se carga con `source`, no se ejecuta directamente.

# Colores solo si la salida es una terminal
if [ -t 1 ]; then
    C_RESET="\033[0m"; C_INFO="\033[0;34m"; C_OK="\033[0;32m"
    C_WARN="\033[0;33m"; C_ERR="\033[0;31m"; C_STEP="\033[1;36m"
else
    C_RESET=""; C_INFO=""; C_OK=""; C_WARN=""; C_ERR=""; C_STEP=""
fi

log()  { printf "%b[modulinkr]%b %s\n" "$C_INFO" "$C_RESET" "$*"; }
ok()   { printf "%b[  ok  ]%b %s\n" "$C_OK" "$C_RESET" "$*"; }
warn() { printf "%b[ warn ]%b %s\n" "$C_WARN" "$C_RESET" "$*" >&2; }
err()  { printf "%b[ error ]%b %s\n" "$C_ERR" "$C_RESET" "$*" >&2; }
step() { printf "\n%b== %s ==%b\n" "$C_STEP" "$*" "$C_RESET"; }
die()  { err "$*"; exit 1; }

# require_root: la mayoría de acciones necesitan privilegios (apt, systemctl).
require_root() {
    if [ "$(id -u)" -ne 0 ]; then
        die "Este paso requiere root. Reejecutar con sudo."
    fi
}

command_exists() { command -v "$1" >/dev/null 2>&1; }

# require_ubuntu: el instalador asume Ubuntu (apt, systemd). Avisa si no lo es.
require_ubuntu() {
    if [ ! -r /etc/os-release ]; then
        warn "No se encontró /etc/os-release; no se puede verificar la distribución."
        return 0
    fi
    . /etc/os-release
    case "${ID:-}${ID_LIKE:-}" in
        *debian*|*ubuntu*) : ;;
        *) warn "Distribución '${ID:-desconocida}' no verificada. El instalador está pensado para Ubuntu/Debian." ;;
    esac
}

# confirm PREGUNTA [default_yes]: devuelve 0 si el usuario acepta.
# En modo no interactivo ($ASSUME_YES=1) acepta sin preguntar.
confirm() {
    local prompt="$1" def="${2:-n}" reply
    if [ "${ASSUME_YES:-0}" = "1" ]; then return 0; fi
    local hint="[y/N]"; [ "$def" = "y" ] && hint="[Y/n]"
    read -r -p "$prompt $hint " reply || true
    reply="${reply:-$def}"
    case "$reply" in [yY]*) return 0 ;; *) return 1 ;; esac
}

# ask VAR "PREGUNTA" "DEFAULT": lee un valor. Si la variable ya trae valor
# (config file o entorno) o el modo es no interactivo, usa lo que haya sin
# preguntar. Deja el resultado en la variable nombrada.
ask() {
    local __var="$1" prompt="$2" def="${3:-}" cur reply
    eval "cur=\${$__var:-}"
    if [ -n "$cur" ]; then eval "$__var=\"\$cur\""; return 0; fi
    if [ "${ASSUME_YES:-0}" = "1" ]; then eval "$__var=\"\$def\""; return 0; fi
    if [ -n "$def" ]; then
        read -r -p "$prompt [$def]: " reply || true
        reply="${reply:-$def}"
    else
        read -r -p "$prompt: " reply || true
    fi
    eval "$__var=\"\$reply\""
}

# ask_secret VAR "PREGUNTA": como ask pero sin eco y sin default visible. Pide
# la contraseña dos veces y no continúa hasta que ambas coincidan y no estén
# vacías. Si la variable ya trae valor (config/entorno) no pregunta.
ask_secret() {
    local __var="$1" prompt="$2" cur reply reply2
    eval "cur=\${$__var:-}"
    if [ -n "$cur" ]; then eval "$__var=\"\$cur\""; return 0; fi
    if [ "${ASSUME_YES:-0}" = "1" ]; then
        die "Falta '$__var' y el modo es no interactivo. Definirlo en el config."
    fi
    while :; do
        read -r -s -p "$prompt: " reply || true; echo
        read -r -s -p "$prompt (repetir): " reply2 || true; echo
        if [ -z "$reply" ]; then
            warn "La contraseña no puede quedar vacía. Reintentar."
        elif [ "$reply" != "$reply2" ]; then
            warn "Las contraseñas no coinciden. Reintentar."
        else
            break
        fi
    done
    eval "$__var=\"\$reply\""
}

# rand_secret [n]: genera una contraseña aleatoria segura de n bytes (default 24).
rand_secret() { openssl rand -base64 "${1:-24}" | tr -d '\n/+=' | cut -c1-32; }

# load_config ARCHIVO: carga un archivo de variables (clave=valor). No falla si
# no existe; así el instalador corre igual interactivo sin config.
load_config() {
    local f="$1"
    [ -n "$f" ] || return 0
    [ -f "$f" ] || { warn "Config '$f' no existe; se continúa en modo interactivo."; return 0; }
    # shellcheck disable=SC1090
    set -a; . "$f"; set +a
    log "Config cargada desde $f"
}

# backup_once ARCHIVO: guarda una copia .bak la primera vez que se toca un
# archivo del sistema, para poder revertir. Idempotente.
backup_once() {
    local f="$1"
    [ -f "$f" ] || return 0
    [ -f "$f.modulinkr.bak" ] && return 0
    cp -a "$f" "$f.modulinkr.bak"
    log "Copia de seguridad: $f.modulinkr.bak"
}
