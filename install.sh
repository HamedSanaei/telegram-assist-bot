#!/usr/bin/env bash
set -euo pipefail

INSTANCE=""
RETENTION_DAYS="2"
INSTALL_DIR=""
IMAGE="ghcr.io/hamedsanaei/telegram-assist-bot:1.1.0"
MONGODB_IMAGE="${TAB_MONGODB_IMAGE:-mongo:7.0.32}"
MONGODB_IMAGE_EXPLICIT=0
RUNTIME_UID="${TAB_RUNTIME_UID:-10001}"
RUNTIME_GID="${TAB_RUNTIME_GID:-10001}"
ADMIN_USER_IDS="${TAB_ADMIN_USER_IDS:-${TAB_ADMIN_USER_ID:-}}"
SOURCE_USERNAMES="${TAB_SOURCE_USERNAMES:-${TAB_SOURCE_USERNAME:-}}"
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
  --mongodb-image IMAGE  MongoDB image (default mongo:7.0.32)
  --runtime-uid UID      Non-root application UID (default 10001)
  --runtime-gid GID      Non-root application GID (default 10001)
  --admin-user-ids CSV   One or more distinct Telegram administrator IDs
  --source-usernames CSV One or more public channel usernames or t.me links
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
    --mongodb-image) MONGODB_IMAGE="${2:-}"; MONGODB_IMAGE_EXPLICIT=1; shift 2 ;;
    --runtime-uid) RUNTIME_UID="${2:-}"; shift 2 ;;
    --runtime-gid) RUNTIME_GID="${2:-}"; shift 2 ;;
    --admin-user-ids) ADMIN_USER_IDS="${2:-}"; shift 2 ;;
    --source-usernames) SOURCE_USERNAMES="${2:-}"; shift 2 ;;
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
if [[ ! "$RUNTIME_UID" =~ ^[1-9][0-9]*$ \
  || ! "$RUNTIME_GID" =~ ^[1-9][0-9]*$ ]]; then
  echo "Runtime UID and GID must be positive integers." >&2
  exit 2
fi
INSTALL_DIR="${INSTALL_DIR:-${HOME}/.local/share/telegram-assist-bot/${INSTANCE}}"
PROJECT="telegram-assist-${INSTANCE}"
DATABASE="telegram_assist_${INSTANCE//-/_}"
KERNEL_RELEASE="${TAB_TEST_KERNEL_RELEASE:-$(uname -r)}"

env_value() {
  local key="$1" file="$2"
  [[ -f "$file" ]] || return 1
  awk -F= -v key="$key" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' "$file"
}

if [[ -f "$INSTALL_DIR/.env" ]]; then
  if ((MONGODB_IMAGE_EXPLICIT == 0)); then
    MONGODB_IMAGE="$(env_value TAB_MONGODB_IMAGE "$INSTALL_DIR/.env" || true)"
    MONGODB_IMAGE="${MONGODB_IMAGE:-mongo:7.0.32}"
  fi
  RUNTIME_UID="$(env_value TAB_RUNTIME_UID "$INSTALL_DIR/.env" || true)"
  RUNTIME_UID="${RUNTIME_UID:-10001}"
  RUNTIME_GID="$(env_value TAB_RUNTIME_GID "$INSTALL_DIR/.env" || true)"
  RUNTIME_GID="${RUNTIME_GID:-10001}"
fi

check_kernel_mongodb_pair() {
  local kernel="$1" image="$2" kernel_major kernel_minor mongo_major
  if [[ "$kernel" =~ ^([0-9]+)\.([0-9]+) ]]; then
    kernel_major="${BASH_REMATCH[1]}"
    kernel_minor="${BASH_REMATCH[2]}"
  else
    echo "Linux kernel version must begin with MAJOR.MINOR." >&2
    exit 2
  fi
  if [[ "$image" =~ :([0-9]+)\.([0-9]+)\.([0-9]+)$ ]]; then
    mongo_major="${BASH_REMATCH[1]}"
  else
    echo "MongoDB image must use an explicit MAJOR.MINOR.PATCH tag." >&2
    exit 2
  fi
  printf 'detected_kernel=%s\nselected_mongodb_image=%s\n' "$kernel" "$image"
  if ((kernel_major > 6 || (kernel_major == 6 && kernel_minor >= 19))) \
    && ((mongo_major == 8)); then
    echo "compatibility_decision=blocked: MongoDB 8.x is incompatible with Linux kernel 6.19+." >&2
    exit 2
  fi
  echo "compatibility_decision=compatible"
}

if ((DRY_RUN)); then
  admin_count="$(awk -F, '{print NF}' <<<"$ADMIN_USER_IDS")"
  source_count="$(awk -F, '{print NF}' <<<"$SOURCE_USERNAMES")"
  printf 'instance=%s\nproject=%s\ndatabase=%s\ninstall_dir=%s\nimage=%s\nmongodb_image=%s\nruntime_uid=%s\nruntime_gid=%s\nretention_days=%s\n' \
    "$INSTANCE" "$PROJECT" "$DATABASE" "$INSTALL_DIR" "$IMAGE" \
    "$MONGODB_IMAGE" "$RUNTIME_UID" "$RUNTIME_GID" "$RETENTION_DAYS"
  printf 'admin_count=%s\nadmin_user_ids=%s\nsource_count=%s\nsource_usernames=%s\nplanned_manager_command=tabctl --instance %s status\n' \
    "$admin_count" "$ADMIN_USER_IDS" "$source_count" "$SOURCE_USERNAMES" "$INSTANCE"
  check_kernel_mongodb_pair "$KERNEL_RELEASE" "$MONGODB_IMAGE"
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

run_permission_helper() {
  local helper="$1" mode="$2" host_uid host_gid
  host_uid="${SUDO_UID:-$(id -u)}"
  host_gid="${SUDO_GID:-$(id -g)}"
  permission_args=(
    "$mode"
    --instance-dir "$INSTALL_DIR"
    --runtime-uid "$RUNTIME_UID"
    --runtime-gid "$RUNTIME_GID"
    --host-uid "$host_uid"
    --host-gid "$host_gid"
  )
  if [[ "$(id -u)" -eq 0 ]]; then
    bash "$helper" "${permission_args[@]}"
  else
    sudo bash "$helper" "${permission_args[@]}"
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
permission_helper="$(mktemp)"
trap 'rm -f "$permission_helper"' EXIT
curl -fsSL "$BASE_URL/deploy/permissions.sh" -o "$permission_helper"
run_permission_helper "$permission_helper" repair

curl -fsSL "$BASE_URL/compose.yaml" -o "$INSTALL_DIR/compose.yaml"
curl -fsSL "$BASE_URL/config/configuration.example.json" \
  -o "$INSTALL_DIR/configuration.example.json"
curl -fsSL "$BASE_URL/deploy/manage.sh" -o "$INSTALL_DIR/manage.sh"
install -m 0700 "$permission_helper" "$INSTALL_DIR/permissions.sh"

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
  if [[ -z "$ADMIN_USER_IDS" ]]; then
    prompt ADMIN_USER_IDS "Administrator IDs (comma-separated)"
  fi
  if [[ -z "$SOURCE_USERNAMES" ]]; then
    prompt SOURCE_USERNAMES "Source channels (comma-separated)"
  fi
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
    printf 'TAB_RUNTIME_UID=%s\n' "$RUNTIME_UID"
    printf 'TAB_RUNTIME_GID=%s\n' "$RUNTIME_GID"
    printf 'TAB_MONGODB_IMAGE=%s\n' "$MONGODB_IMAGE"
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
run_permission_helper "$INSTALL_DIR/permissions.sh" repair

compose=(docker compose --project-name "$PROJECT" --env-file "$INSTALL_DIR/.env" \
  -f "$INSTALL_DIR/compose.yaml")
docker pull "$IMAGE"
docker run --rm "$IMAGE" deployment-preflight \
  --kernel-version "$KERNEL_RELEASE" --mongodb-image "$MONGODB_IMAGE"
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
    --admin-user-ids "$ADMIN_USER_IDS"
    --source-usernames "$SOURCE_USERNAMES"
    --destination-name "$TAB_DESTINATION_NAME"
    --destination-id "$TAB_DESTINATION_ID"
    --timezone "$TAB_TIMEZONE"
  )
  if [[ -n "$TAB_DESTINATION_USERNAME" ]]; then
    render_args+=(--destination-username "$TAB_DESTINATION_USERNAME")
  fi
  docker run --rm --user "$RUNTIME_UID:$RUNTIME_GID" \
    --env-file "$INSTALL_DIR/.env" \
    -v "$INSTALL_DIR:/instance" "$IMAGE" "${render_args[@]}"
  run_permission_helper "$INSTALL_DIR/permissions.sh" repair
fi

"${compose[@]}" run --rm runtime check --config /app/config/configuration.json
if ! "${compose[@]}" run --rm runtime login --config /app/config/configuration.json; then
  echo "Login was not completed. MongoDB and instance files were preserved." >&2
  exit 6
fi
"${compose[@]}" up -d

if ! command -v python3 >/dev/null 2>&1; then
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y python3
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf -y install python3
  else
    echo "Python 3 is required for the global tabctl manager." >&2
    exit 3
  fi
fi
if [[ "$(id -u)" -eq 0 ]]; then
  manager_target="/usr/local/bin/tabctl"
else
  manager_target="$HOME/.local/bin/tabctl"
  mkdir -p "$(dirname "$manager_target")"
fi
curl -fsSL "$BASE_URL/deploy/tabctl.py" -o "$manager_target"
chmod 0755 "$manager_target"
TAB_REGISTRY_PATH="${TAB_REGISTRY_PATH:-}" python3 "$manager_target" \
  instance import --path "$INSTALL_DIR" --name "$INSTANCE" >/dev/null
echo "Installed ${INSTANCE}. Manage it with: tabctl --instance ${INSTANCE} status"
