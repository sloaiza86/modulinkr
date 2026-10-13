#!/usr/bin/env bash
# Empaqueta exclusivamente el código de supervisión para ambos equipos.
set -euo pipefail
ml_root="$(cd "$(dirname "$0")" && pwd)"
ml_output="${1:-/tmp}"
mkdir -p "$ml_output"
ml_gateway=(
  gateway/pi-service/buffer.py gateway/pi-service/install_supervision.sh
  gateway/pi-web/web_service.py gateway/pi-web/systemapi.py gateway/pi-web/supervision.py
  gateway/pi-web/static/index.html gateway/pi-web/static/style.css
  gateway/pi-web/static/app.js gateway/pi-web/static/supervision.js
)
ml_server=(
  server/install_supervision.sh server/lib/consumer.sh server/db/apply_migrations.sh
  server/db/migrations/005_system_health.sql server/consumer/consumer_service.py
  server/consumer/db.py server/consumer/consumer_status.py server/consumer/monitor_service.py
  server/consumer/systemd/modulinkr-consumer.service server/consumer/systemd/modulinkr-monitor.service
)
COPYFILE_DISABLE=1 tar -czvf "$ml_output/modulinkr-supervision-gateway.tar.gz" -C "$ml_root" "${ml_gateway[@]}"
COPYFILE_DISABLE=1 tar -czvf "$ml_output/modulinkr-supervision-server.tar.gz" -C "$ml_root" "${ml_server[@]}"
shasum -a 256 "$ml_output/modulinkr-supervision-gateway.tar.gz" "$ml_output/modulinkr-supervision-server.tar.gz"
