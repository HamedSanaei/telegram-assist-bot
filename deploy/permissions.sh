#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-audit}"
shift || true
INSTANCE_DIR=""
RUNTIME_UID="10001"
RUNTIME_GID="10001"
HOST_UID=""
HOST_GID=""

usage() {
  cat <<'EOF'
Usage: permissions.sh {audit|repair} --instance-dir ABSOLUTE_PATH [options]
  --runtime-uid UID
  --runtime-gid GID
  --host-uid UID
  --host-gid GID
EOF
}

while (($#)); do
  case "$1" in
    --instance-dir) INSTANCE_DIR="${2:-}"; shift 2 ;;
    --runtime-uid) RUNTIME_UID="${2:-}"; shift 2 ;;
    --runtime-gid) RUNTIME_GID="${2:-}"; shift 2 ;;
    --host-uid) HOST_UID="${2:-}"; shift 2 ;;
    --host-gid) HOST_GID="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "Unknown permission option." >&2; usage >&2; exit 2 ;;
  esac
done

if [[ "$MODE" != "audit" && "$MODE" != "repair" ]]; then
  echo "Permission mode must be audit or repair." >&2
  exit 2
fi
if [[ "$INSTANCE_DIR" != /* || -L "$INSTANCE_DIR" ]]; then
  echo "Instance directory must be an absolute non-symlink path." >&2
  exit 2
fi
for value in "$RUNTIME_UID" "$RUNTIME_GID"; do
  if [[ ! "$value" =~ ^[1-9][0-9]*$ ]]; then
    echo "Runtime UID and GID must be positive integers." >&2
    exit 2
  fi
done
HOST_UID="${HOST_UID:-${SUDO_UID:-$(id -u)}}"
HOST_GID="${HOST_GID:-${SUDO_GID:-$(id -g)}}"
for value in "$HOST_UID" "$HOST_GID"; do
  if [[ ! "$value" =~ ^[0-9]+$ ]]; then
    echo "Host UID and GID must be non-negative integers." >&2
    exit 2
  fi
done

report_path() {
  local label="$1" path="$2"
  if [[ -e "$path" ]]; then
    printf '%s ' "$label"
    stat --printf='mode=%a uid=%u gid=%g\n' "$path"
  else
    printf '%s missing\n' "$label"
  fi
}

audit() {
  report_path instance "$INSTANCE_DIR"
  report_path env "$INSTANCE_DIR/.env"
  report_path config_directory "$INSTANCE_DIR/config"
  report_path config "$INSTANCE_DIR/config/configuration.json"
  report_path backups "$INSTANCE_DIR/backups"
  report_path metadata_directory "$INSTANCE_DIR/metadata"
  report_path metadata "$INSTANCE_DIR/metadata/instance.json"
  printf 'volume_policy mode=700 uid=%s gid=%s\n' "$RUNTIME_UID" "$RUNTIME_GID"
}

if [[ "$MODE" == "audit" ]]; then
  audit
  exit 0
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Permission repair requires root; rerun through sudo." >&2
  exit 3
fi

install -d -m 0710 -o "$HOST_UID" -g "$RUNTIME_GID" "$INSTANCE_DIR"
install -d -m 2750 -o "$RUNTIME_UID" -g "$HOST_GID" "$INSTANCE_DIR/config"
install -d -m 0700 -o "$HOST_UID" -g "$HOST_GID" \
  "$INSTANCE_DIR/backups" "$INSTANCE_DIR/metadata"

if [[ -f "$INSTANCE_DIR/.env" && ! -L "$INSTANCE_DIR/.env" ]]; then
  chown "$HOST_UID:$HOST_GID" "$INSTANCE_DIR/.env"
  chmod 0600 "$INSTANCE_DIR/.env"
fi
if [[ -f "$INSTANCE_DIR/config/configuration.json" \
  && ! -L "$INSTANCE_DIR/config/configuration.json" ]]; then
  chown "$RUNTIME_UID:$HOST_GID" "$INSTANCE_DIR/config/configuration.json"
  chmod 0640 "$INSTANCE_DIR/config/configuration.json"
fi
if [[ -f "$INSTANCE_DIR/metadata/instance.json" \
  && ! -L "$INSTANCE_DIR/metadata/instance.json" ]]; then
  chown "$HOST_UID:$HOST_GID" "$INSTANCE_DIR/metadata/instance.json"
  chmod 0600 "$INSTANCE_DIR/metadata/instance.json"
fi
for script in manage.sh permissions.sh; do
  if [[ -f "$INSTANCE_DIR/$script" && ! -L "$INSTANCE_DIR/$script" ]]; then
    chown "$HOST_UID:$HOST_GID" "$INSTANCE_DIR/$script"
    chmod 0700 "$INSTANCE_DIR/$script"
  fi
done

audit
