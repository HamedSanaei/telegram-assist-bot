#!/usr/bin/env bash
set -euo pipefail

INSTANCE=""
RETENTION_DAYS="2"
INSTALL_DIR=""
IMAGE="ghcr.io/hamedsanaei/telegram-assist-bot:1.0.0"
NON_INTERACTIVE=0
UPDATE=0
DRY_RUN=0
BASE_URL="${TAB_INSTALL_BASE_URL:-https://raw.githubusercontent.com/HamedSanaei/telegram-assist-bot/main}"

usage() {
  cat <<'EOF'
Usage: install.sh --instance NAME [options]
  --retention-days N     Media and approval retention (1..3650, default 2)
  --install-dir PATH     Instance directory
  --image IMAGE:TAG      Container image
  --non-interactive      Read required values from TAB_* environment variables
  --update               Update assets/image without overwriting configuration
  --dry-run              Validate and print the plan without changing the host
  --help
EOF
}

while (($#)); do
  case "$1" in
    --instance) INSTANCE="${2:-}"; shift 2 ;;
    --retention-days) RETENTION_DAYS="${2:-}"; shift 2 ;;
    --install-dir) INSTALL_DIR="${2:-}"; shift 2 ;;
    --image|--version) IMAGE="${2:-}"; shift 2 ;;
    --non-interactive|--unattended) NON_INTERACTIVE=1; shift ;;
    --update) UPDATE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ ! "$INSTANCE" =~ ^[a-z][a-z0-9-]{0,31}$ ]]; then
  echo "Instance must match [a-z][a-z0-9-]{0,31}." >&2
  exit 2
fi
if [[ ! "$RETENTION_DAYS" =~ ^[0-9]+$ ]] \
  || ((RETENTION_DAYS < 1 || RETENTION_DAYS > 3650)); then
  echo "Retention days must be an integer between 1 and 3650." >&2
  exit 2
fi
INSTALL_DIR="${INSTALL_DIR:-${HOME}/.local/share/telegram-assist-bot/${INSTANCE}}"
PROJECT="telegram-assist-${INSTANCE}"
DATABASE="telegram_assist_${INSTANCE//-/_}"

if ((DRY_RUN)); then
  printf 'instance=%s\nproject=%s\ndatabase=%s\ninstall_dir=%s\nimage=%s\nretention_days=%s\n' \
    "$INSTANCE" "$PROJECT" "$DATABASE" "$INSTALL_DIR" "$IMAGE" "$RETENTION_DAYS"
  exit 0
fi

install_docker() {
  if command -v docker >/dev/null 2>&1 \
    && docker compose version >/dev/null 2>&1; then
    return
  fi
  if [[ ! -r /etc/os-release ]]; then
    echo "Docker is missing and this Linux distribution cannot be identified." >&2
    exit 3
  fi
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID_LIKE:-$ID}" in
    *debian*|*ubuntu*)
      sudo install -m 0755 -d /etc/apt/keyrings
      curl -fsSL "https://download.docker.com/linux/${ID}/gpg" |
        sudo gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg
      sudo chmod a+r /etc/apt/keyrings/docker.gpg
      arch="$(dpkg --print-architecture)"
      codename="${VERSION_CODENAME:?Unsupported Debian-family release}"
      printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
        "$arch" "$ID" "$codename" |
        sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
      sudo apt-get update
      sudo apt-get install -y docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
      ;;
    *fedora*|*rhel*|*centos*)
      sudo dnf -y install dnf-plugins-core
      sudo dnf config-manager --add-repo \
        https://download.docker.com/linux/fedora/docker-ce.repo
      sudo dnf -y install docker-ce docker-ce-cli containerd.io \
        docker-buildx-plugin docker-compose-plugin
      ;;
    *)
      echo "Unsupported distribution; install Docker Engine and Compose Plugin manually." >&2
      exit 3
      ;;
  esac
  sudo systemctl enable --now docker
  if ! docker info >/dev/null 2>&1; then
    sudo usermod -aG docker "$USER"
    echo "Docker group access changed. Log out/in, then rerun the same installer command." >&2
    exit 4
  fi
}

prompt() {
  local variable="$1" label="$2" secret="${3:-0}" value
  value="${!variable:-}"
  if [[ -z "$value" && "$NON_INTERACTIVE" -eq 0 ]]; then
    if [[ "$secret" -eq 1 ]]; then
      read -r -s -p "${label}: " value
      printf '\n'
    else
      read -r -p "${label}: " value
    fi
  fi
  if [[ -z "$value" ]]; then
    echo "Missing required value: ${variable}" >&2
    exit 2
  fi
  printf -v "$variable" '%s' "$value"
}

install_docker
mkdir -p "$INSTALL_DIR/config"
chmod 700 "$INSTALL_DIR"

curl -fsSL "$BASE_URL/compose.yaml" -o "$INSTALL_DIR/compose.yaml"
curl -fsSL "$BASE_URL/config/configuration.example.json" \
  -o "$INSTALL_DIR/configuration.example.json"
curl -fsSL "$BASE_URL/deploy/manage.sh" -o "$INSTALL_DIR/manage.sh"
chmod 700 "$INSTALL_DIR/manage.sh"

if [[ -f "$INSTALL_DIR/config/configuration.json" && "$UPDATE" -eq 0 ]]; then
  echo "Instance already exists; use --update to refresh assets without overwriting Config." >&2
  exit 5
fi

if [[ ! -f "$INSTALL_DIR/.env" ]]; then
  prompt TAB_TELEGRAM_API_ID "Telegram API ID" 1
  prompt TAB_TELEGRAM_API_HASH "Telegram API Hash" 1
  prompt TAB_TELEGRAM_PHONE_NUMBER "Telegram phone number" 1
  prompt TAB_TELEGRAM_BOT_TOKEN "Telegram Bot Token" 1
  prompt TAB_APPROVAL_CHAT_ID "Approval chat ID"
  prompt TAB_ADMIN_USER_ID "Admin user ID"
  prompt TAB_SOURCE_USERNAME "Source channel username"
  prompt TAB_DESTINATION_NAME "Destination name"
  prompt TAB_DESTINATION_ID "Destination channel ID"
  TAB_DESTINATION_USERNAME="${TAB_DESTINATION_USERNAME:-}"
  TAB_TIMEZONE="${TAB_TIMEZONE:-Asia/Tehran}"
  MONGO_PASSWORD="$(openssl rand -hex 24)"
  umask 077
  {
    printf 'COMPOSE_PROJECT_NAME=%s\n' "$PROJECT"
    printf 'TAB_INSTANCE_DIR=%s\n' "$INSTALL_DIR"
    printf 'TAB_IMAGE=%s\n' "$IMAGE"
    printf 'TAB_MONGODB_DATABASE=%s\n' "$DATABASE"
    printf 'TAB_MONGODB_USERNAME=telegram_assist\n'
    printf 'TAB_MONGODB_PASSWORD=%s\n' "$MONGO_PASSWORD"
    printf 'TAB_MONGODB_URI=mongodb://telegram_assist:%s@mongodb:27017/?authSource=admin&directConnection=true\n' "$MONGO_PASSWORD"
    printf 'TAB_TELEGRAM_API_ID=%s\n' "$TAB_TELEGRAM_API_ID"
    printf 'TAB_TELEGRAM_API_HASH=%s\n' "$TAB_TELEGRAM_API_HASH"
    printf 'TAB_TELEGRAM_PHONE_NUMBER=%s\n' "$TAB_TELEGRAM_PHONE_NUMBER"
    printf 'TAB_TELEGRAM_BOT_TOKEN=%s\n' "$TAB_TELEGRAM_BOT_TOKEN"
  } >"$INSTALL_DIR/.env"
  chmod 600 "$INSTALL_DIR/.env"
fi

compose=(docker compose --project-name "$PROJECT" --env-file "$INSTALL_DIR/.env" \
  -f "$INSTALL_DIR/compose.yaml")
"${compose[@]}" config >/dev/null
"${compose[@]}" pull
"${compose[@]}" up -d mongodb

if [[ ! -f "$INSTALL_DIR/config/configuration.json" ]]; then
  render_args=(
    render-instance-config
    --template /instance/configuration.example.json
    --output /instance/config/configuration.json
    --instance "$INSTANCE"
    --retention-days "$RETENTION_DAYS"
    --approval-chat-id "$TAB_APPROVAL_CHAT_ID"
    --admin-user-id "$TAB_ADMIN_USER_ID"
    --source-username "$TAB_SOURCE_USERNAME"
    --destination-name "$TAB_DESTINATION_NAME"
    --destination-id "$TAB_DESTINATION_ID"
    --timezone "$TAB_TIMEZONE"
  )
  if [[ -n "$TAB_DESTINATION_USERNAME" ]]; then
    render_args+=(--destination-username "$TAB_DESTINATION_USERNAME")
  fi
  docker run --rm --env-file "$INSTALL_DIR/.env" \
    -v "$INSTALL_DIR:/instance" "$IMAGE" "${render_args[@]}"
fi

"${compose[@]}" run --rm runtime check --config /app/config/configuration.json
if ! "${compose[@]}" run --rm runtime login --config /app/config/configuration.json; then
  echo "Login was not completed. MongoDB and instance files were preserved." >&2
  exit 6
fi
"${compose[@]}" up -d
echo "Installed ${INSTANCE}. Manage it with: ${INSTALL_DIR}/manage.sh status"
