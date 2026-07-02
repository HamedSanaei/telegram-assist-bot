#!/usr/bin/env bash
# Installer for the Ubuntu bundle produced by scripts/build_publish.py.
# Run from inside the extracted bundle directory as root:
#   sudo bash install.sh [main|collector|suite|worker|all]
#
# "suite" installs a single service running the main app and the
# collector together in one process (src.run_all); use it instead of
# "main" + "collector" when you prefer one service for everything.
#
# Installs the project to /opt/telegram-admin-bot, creates a virtualenv,
# installs dependencies, and copies the requested systemd service files.

set -euo pipefail

TARGET="/opt/telegram-admin-bot"
ROLE="${1:-all}"
SERVICE_USER="telegrambot"

echo "==> Installing Telegram Admin Bot to ${TARGET} (role: ${ROLE})"

if ! id "${SERVICE_USER}" &>/dev/null; then
    useradd --system --home "${TARGET}" "${SERVICE_USER}"
fi

mkdir -p "${TARGET}"
cp -r src requirements.txt pyproject.toml README.md docs deploy "${TARGET}/"
mkdir -p "${TARGET}/config" "${TARGET}/data" "${TARGET}/logs"
cp -n config/configuration.example.json "${TARGET}/config/" || true
if [ ! -f "${TARGET}/config/configuration.json" ]; then
    cp config/configuration.example.json "${TARGET}/config/configuration.json"
    echo "==> Created ${TARGET}/config/configuration.json - FILL IN YOUR SECRETS"
fi

if [ ! -d "${TARGET}/.venv" ]; then
    python3 -m venv "${TARGET}/.venv"
fi
"${TARGET}/.venv/bin/pip" install --upgrade pip -q
"${TARGET}/.venv/bin/pip" install -r "${TARGET}/requirements.txt" -q

chown -R "${SERVICE_USER}:${SERVICE_USER}" "${TARGET}"

install_service() {
    cp "${TARGET}/deploy/$1" /etc/systemd/system/
    echo "==> Installed systemd unit: $1"
}

case "${ROLE}" in
    main)      install_service telegram-admin-bot.service ;;
    collector) install_service telegram-collector.service ;;
    suite)     install_service telegram-suite.service ;;
    worker)    install_service iran-vpn-worker.service ;;
    all)
        install_service telegram-admin-bot.service
        install_service telegram-collector.service
        install_service iran-vpn-worker.service
        ;;
    *) echo "Unknown role '${ROLE}' (use main|collector|suite|worker|all)"; exit 1 ;;
esac

systemctl daemon-reload
echo "==> Done. Edit ${TARGET}/config/configuration.json, then enable services, e.g.:"
echo "    systemctl enable --now telegram-admin-bot"
