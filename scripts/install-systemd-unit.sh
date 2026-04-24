#!/usr/bin/env bash
set -euo pipefail

# Install/refresh systemd unit for web4mihomo.
# Usage:
#   sudo bash scripts/install-systemd-unit.sh
# Optional env:
#   UNIT_NAME=web4mihomo
#   APP_DIR=/etc/mihomo/web4mihomo
#   APP_USER=root
#   APP_GROUP=root
#   PORT=8800

UNIT_NAME="${UNIT_NAME:-web4mihomo}"
APP_DIR="${APP_DIR:-/etc/mihomo/web4mihomo}"
APP_USER="${APP_USER:-root}"
APP_GROUP="${APP_GROUP:-root}"
PORT="${PORT:-8800}"

UNIT_PATH="/etc/systemd/system/${UNIT_NAME}.service"

if [[ ! -d "${APP_DIR}" ]]; then
  echo "APP_DIR does not exist: ${APP_DIR}" >&2
  exit 1
fi

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  echo "Python venv is missing: ${APP_DIR}/.venv/bin/python" >&2
  exit 1
fi

cat >"${UNIT_PATH}" <<EOF
[Unit]
Description=web4mihomo FastAPI service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
EnvironmentFile=-${APP_DIR}/.env
ExecStart=${APP_DIR}/.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port ${PORT}
Restart=always
RestartSec=2
TimeoutStopSec=20

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now "${UNIT_NAME}.service"
systemctl --no-pager --full status "${UNIT_NAME}.service" || true

echo
echo "Unit installed: ${UNIT_PATH}"
echo "Useful commands:"
echo "  sudo systemctl restart ${UNIT_NAME}"
echo "  sudo systemctl status ${UNIT_NAME}"
echo "  sudo journalctl -u ${UNIT_NAME} -f"
