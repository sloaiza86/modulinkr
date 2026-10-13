#!/usr/bin/env bash
# Instala la supervisión con las credenciales operativas existentes.
set -euo pipefail
test "$(id -u)" -eq 0
ml_source="${MODULINKR_SERVER_SOURCE:-$(cd "$(dirname "$0")" && pwd)}"
ml_target="$(systemctl show modulinkr-consumer.service --property=WorkingDirectory --value 2>/dev/null || true)"
ml_user="$(systemctl show modulinkr-consumer.service --property=User --value 2>/dev/null || true)"
ml_target="${ml_target:-/opt/modulinkr/consumer}"
ml_user="${ml_user:-modulinkr-consumer}"
ml_group="$(id -gn "$ml_user")"
ml_exec="$(systemctl show modulinkr-consumer.service --property=ExecStart --value 2>/dev/null || true)"
ml_python="$(python3 -c 'import re,sys; match=re.search(r"path=([^ ;]+)",sys.stdin.read()); print(match.group(1) if match else "")' <<< "$ml_exec")"
ml_python="${ml_python:-$ml_target/.venv/bin/python3}"
test -f /etc/modulinkr/consumer.env
test -x "$ml_python"
set -a
. /etc/modulinkr/consumer.env
set +a
"$ml_python" -m pip install 'paho-mqtt>=2.1,<3'
ml_migrations="$(mktemp -d)"
trap 'rm -rf "$ml_migrations"' EXIT
cp "$ml_source/db/migrations/005_system_health.sql" "$ml_migrations/"
MODULINKR_PSQL='sudo -u postgres psql' bash "$ml_source/db/apply_migrations.sh" \
    --db "$MODULINKR_DB_NAME" --dir "$ml_migrations"
sudo -u postgres psql -X -v ON_ERROR_STOP=1 -d "$MODULINKR_DB_NAME" \
    --set=app_role="$MODULINKR_DB_USER" <<'SQL'
GRANT SELECT, INSERT, UPDATE ON system_health TO :"app_role";
SQL
ml_data="$(sudo -u postgres psql -X -At -d "$MODULINKR_DB_NAME" -c 'SHOW data_directory')"
ml_volume="$(python3 - "$ml_data" <<'PY'
import os
import sys
path = os.path.realpath(sys.argv[1])
while not os.path.ismount(path):
    path = os.path.dirname(path)
print(path)
PY
)"
printf 'MODULINKR_DATA_VOLUME=%s\n' "$ml_volume" > /etc/modulinkr/monitor.env
chmod 600 /etc/modulinkr/monitor.env
install -d -m 0700 -o "$ml_user" -g "$ml_group" /var/lib/modulinkr-consumer
systemctl stop modulinkr-consumer.service || true
for ml_file in consumer_service.py consumer_status.py monitor_service.py db.py; do
    install -v -m 0644 -o "$ml_user" -g "$ml_group" "$ml_source/consumer/$ml_file" "$ml_target/$ml_file"
done
for ml_unit in modulinkr-consumer modulinkr-monitor; do
    python3 - "$ml_source/consumer/systemd/$ml_unit.service" "/etc/systemd/system/$ml_unit.service" "$ml_target" "$ml_user" "$ml_python" <<'PY'
from pathlib import Path
import sys
source, destination, target, user, executable = sys.argv[1:]
text = Path(source).read_text().replace('User=modulinkr-consumer', 'User='+user)
text = text.replace('/opt/modulinkr/consumer/.venv/bin/python3', executable)
text = text.replace('/opt/modulinkr/consumer', target)
Path(destination).write_text(text)
Path(destination).chmod(0o644)
print('Unidad:', destination)
PY
done
systemctl daemon-reload
systemctl enable --now modulinkr-consumer.service modulinkr-monitor.service
systemctl restart modulinkr-consumer.service modulinkr-monitor.service
systemctl is-active modulinkr-consumer.service modulinkr-monitor.service
printf 'Volumen supervisado: %s\n' "$ml_volume"
