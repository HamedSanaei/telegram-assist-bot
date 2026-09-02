#!/usr/bin/env bash
set -Eeuo pipefail

IMAGE="${TAB_IMAGE:-telegram-assist-bot:acceptance}"
ACCEPTANCE_MONGO_AUTH="not-a-production-secret"
ACCEPTANCE_BOT_SUFFIX="AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
ACCEPTANCE_BOT_FIXTURE="123456:$ACCEPTANCE_BOT_SUFFIX"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
DIAGNOSTICS_ROOT="${TAB_ACCEPTANCE_DIAGNOSTICS_DIR:-$TMP_ROOT/diagnostics}"
REGISTRY_PATH="$TMP_ROOT/registry.json"
export TAB_REGISTRY_PATH="$REGISTRY_PATH"
INSTANCES=(acceptance-one acceptance-two)
CLEANED=0

compose() {
  local instance="$1"
  shift
  docker compose \
    --project-name "telegram-assist-$instance" \
    --env-file "$TMP_ROOT/$instance/.env" \
    --file "$TMP_ROOT/$instance/compose.yaml" "$@"
}

tabctl() {
  TAB_REGISTRY_PATH="$REGISTRY_PATH" \
    python3 "$ROOT/deploy/tabctl.py" "$@"
}

collect_diagnostics() {
  mkdir -p "$DIAGNOSTICS_ROOT"
  docker --version >"$DIAGNOSTICS_ROOT/docker-version.txt" 2>&1 || true
  docker compose version >"$DIAGNOSTICS_ROOT/compose-version.txt" 2>&1 || true
  for instance in "${INSTANCES[@]}"; do
    if [[ -f "$TMP_ROOT/$instance/.env" ]]; then
      compose "$instance" ps --all \
        >"$DIAGNOSTICS_ROOT/$instance-ps.txt" 2>&1 || true
      compose "$instance" logs --no-color --tail 500 \
        >"$DIAGNOSTICS_ROOT/$instance-logs.txt" 2>&1 || true
      cat "$DIAGNOSTICS_ROOT/$instance-ps.txt" >&2 || true
      cat "$DIAGNOSTICS_ROOT/$instance-logs.txt" >&2 || true
    fi
  done
  docker volume ls --filter name=telegram-assist-acceptance- \
    >"$DIAGNOSTICS_ROOT/volumes.txt" 2>&1 || true
  docker network ls --filter name=telegram-assist-acceptance- \
    >"$DIAGNOSTICS_ROOT/networks.txt" 2>&1 || true
}

cleanup_resources() {
  local instance
  for instance in "${INSTANCES[@]}"; do
    if [[ -f "$TMP_ROOT/$instance/.env" ]]; then
      compose "$instance" down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
  done
  docker image rm "$IMAGE" >/dev/null 2>&1 || true
}

finish() {
  local exit_code=$?
  trap - EXIT
  set +e
  if ((exit_code != 0)); then
    collect_diagnostics
  fi
  if ((CLEANED == 0)); then
    cleanup_resources
  fi
  rm -rf "$TMP_ROOT"
  exit "$exit_code"
}
trap finish EXIT

docker --version
docker compose version
docker build --build-arg VERSION=1.1.3 --tag "$IMAGE" "$ROOT"
test "$(docker image inspect "$IMAGE" --format '{{.Config.User}}')" = "10001:10001"
docker run --rm --user 10001:10001 "$IMAGE" --help >/dev/null

TAB_MONGODB_USERNAME=acceptance TAB_MONGODB_PASSWORD=not-a-production-secret \
  TAB_MONGODB_DATABASE=telegram_assist_acceptance \
  bash "$ROOT/install.sh" --instance acceptance --retention-days 7 \
  --admin-user-ids "700000001,700000002,700000003" \
  --source-usernames "@SourceOne,https://t.me/SourceTwo,t.me/SourceThree" \
  --mongodb-image mongo:7.0.32 --non-interactive --dry-run >/dev/null
pwsh -NoProfile -File "$ROOT/install.ps1" -Instance acceptance \
  -RetentionDays 7 -InstallDirectory "$TMP_ROOT/pwsh-dry-run" \
  -AdminUserIds "700000001,700000002,700000003" \
  -SourceUsernames "@SourceOne,https://t.me/SourceTwo,t.me/SourceThree" \
  -MongoDbImage mongo:7.0.32 -NonInteractive -DryRun >/dev/null

unmapped_id=20001
while getent passwd "$unmapped_id" >/dev/null \
  || getent group "$unmapped_id" >/dev/null; do
  unmapped_id=$((unmapped_id + 1))
done
permission_probe="$TMP_ROOT/unmapped-permission"
sudo bash "$ROOT/deploy/permissions.sh" repair \
  --instance-dir "$permission_probe" \
  --runtime-uid "$unmapped_id" --runtime-gid "$unmapped_id" \
  --host-uid "$(id -u)" --host-gid "$(id -g)" >/dev/null
test "$(stat -c '%g' "$permission_probe")" = "$unmapped_id"
test "$(stat -c '%u' "$permission_probe/config")" = "$unmapped_id"
test "$(stat -c '%a' "$permission_probe")" = "710"
test "$(stat -c '%a' "$permission_probe/config")" = "2750"
test "$(stat -c '%a' "$permission_probe/backups")" = "700"
test "$(stat -c '%a' "$permission_probe/metadata")" = "700"
if getent passwd "$unmapped_id" >/dev/null \
  || getent group "$unmapped_id" >/dev/null; then
  echo "Permission repair unexpectedly created a host account or group." >&2
  exit 1
fi

update_probe="$TMP_ROOT/update-probe"
mkdir -p "$update_probe/instance/config" "$update_probe/bin" "$update_probe/home"
cat >"$update_probe/instance/.env" <<EOF
COMPOSE_PROJECT_NAME=telegram-assist-update-probe
TAB_INSTANCE_DIR=$update_probe/instance
TAB_IMAGE=$IMAGE
TAB_RUNTIME_UID=10001
TAB_RUNTIME_GID=10001
TAB_MONGODB_IMAGE=mongo:7.0.32
TAB_MONGODB_DATABASE=telegram_assist_update_probe
TAB_MONGODB_USERNAME=acceptance
TAB_MONGODB_PASSWORD=$ACCEPTANCE_MONGO_AUTH
TAB_MONGODB_URI=mongodb://acceptance:$ACCEPTANCE_MONGO_AUTH@mongodb:27017/?authSource=admin&directConnection=true
TAB_TELEGRAM_API_ID=12345
TAB_TELEGRAM_API_HASH=00000000000000000000000000000000
TAB_TELEGRAM_PHONE_NUMBER=+10000000000
TAB_TELEGRAM_BOT_TOKEN=$ACCEPTANCE_BOT_FIXTURE
EOF
printf '{"existing":true}\n' \
  >"$update_probe/instance/config/configuration.json"
printf 'session-preserved\n' >"$update_probe/instance/session.sentinel"
cat >"$update_probe/bin/docker" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' "$*" >>"$TAB_DOCKER_LOG"
exit 0
EOF
cat >"$update_probe/bin/python3" <<'EOF'
#!/usr/bin/env bash
exit 0
EOF
chmod 0700 "$update_probe/bin/docker" "$update_probe/bin/python3"
TAB_DOCKER_LOG="$update_probe/docker.log" \
  TAB_INSTALL_BASE_URL="file://$ROOT" \
  TAB_TEST_KERNEL_RELEASE="6.14.0" \
  HOME="$update_probe/home" \
  PATH="$update_probe/bin:$PATH" \
  bash "$ROOT/install.sh" --instance update-probe \
    --install-dir "$update_probe/instance" \
    --image "$IMAGE" --update >/dev/null
grep -F "run --rm runtime check --config /app/config/configuration.json" \
  "$update_probe/docker.log"
grep -F "up -d" "$update_probe/docker.log"
if grep -F "runtime login" "$update_probe/docker.log"; then
  echo "Update unexpectedly invoked Telegram login." >&2
  exit 1
fi
if grep -E '(^| )(stop|down)( |$)' "$update_probe/docker.log"; then
  echo "Update unexpectedly stopped existing services." >&2
  exit 1
fi
test "$(cat "$update_probe/instance/session.sentinel")" = "session-preserved"

for instance in "${INSTANCES[@]}"; do
  mkdir -p "$TMP_ROOT/$instance/config"
  cp "$ROOT/compose.yaml" "$TMP_ROOT/$instance/compose.yaml"
  cp "$ROOT/deploy/permissions.sh" "$TMP_ROOT/$instance/permissions.sh"
  chmod 0700 "$TMP_ROOT/$instance/permissions.sh"
  cat >"$TMP_ROOT/$instance/.env" <<EOF
COMPOSE_PROJECT_NAME=telegram-assist-$instance
TAB_INSTANCE_DIR=$TMP_ROOT/$instance
TAB_IMAGE=$IMAGE
TAB_MONGODB_USERNAME=acceptance
TAB_MONGODB_PASSWORD=$ACCEPTANCE_MONGO_AUTH
TAB_MONGODB_DATABASE=telegram_assist_${instance//-/_}
TAB_MONGODB_IMAGE=mongo:7.0.32
TAB_RUNTIME_UID=10001
TAB_RUNTIME_GID=10001
TAB_MONGODB_URI=mongodb://acceptance:$ACCEPTANCE_MONGO_AUTH@mongodb:27017/?authSource=admin&directConnection=true
TAB_TELEGRAM_API_ID=12345
TAB_TELEGRAM_API_HASH=00000000000000000000000000000000
TAB_TELEGRAM_PHONE_NUMBER=+10000000000
TAB_TELEGRAM_BOT_TOKEN=$ACCEPTANCE_BOT_FIXTURE
EOF
  sudo bash "$TMP_ROOT/$instance/permissions.sh" repair \
    --instance-dir "$TMP_ROOT/$instance" \
    --runtime-uid 10001 --runtime-gid 10001 \
    --host-uid "$(id -u)" --host-gid "$(id -g)"
  docker run --rm --user 10001:10001 \
    --env-file "$TMP_ROOT/$instance/.env" \
    --volume "$TMP_ROOT/$instance/config:/instance/config" \
    --volume "$ROOT/config/configuration.example.json:/instance/configuration.example.json:ro" \
    "$IMAGE" render-instance-config \
    --template /instance/configuration.example.json \
    --output /instance/config/configuration.json \
    --instance "$instance" \
    --retention-days 1 \
    --approval-chat-id -1009001 \
    --admin-user-ids "100000001,100000002,100000003" \
    --source-usernames "@SeedOne,https://t.me/SeedTwo,t.me/SeedThree" \
    --destination-name primary \
    --destination-id -1009002
  sudo bash "$TMP_ROOT/$instance/permissions.sh" repair \
    --instance-dir "$TMP_ROOT/$instance" \
    --runtime-uid 10001 --runtime-gid 10001 \
    --host-uid "$(id -u)" --host-gid "$(id -g)"
  compose "$instance" config --quiet
  compose "$instance" up -d --wait --wait-timeout 120 mongodb
  test "$(
    docker inspect --format '{{.State.Health.Status}}' \
      "$(compose "$instance" ps -q mongodb)"
  )" = "healthy"
  compose "$instance" run --rm runtime check \
    --config /app/config/configuration.json
  compose "$instance" run --rm --entrypoint /bin/sh runtime \
    -eu -c 'test "$(id -u)" = 10001 && test "$(id -g)" = 10001'
  tabctl instance import \
    --path "$TMP_ROOT/$instance" \
    --name "$instance" >/dev/null
done

test "$(tabctl instance list | wc -l)" -eq 2
tabctl instance list | grep $'acceptance-one\t'
tabctl instance list | grep $'acceptance-two\t'
tabctl --instance acceptance-one status
tabctl --instance acceptance-one config check

tabctl --instance acceptance-one admin add "200000001,200000002"
config_after_admin="$(
  sha256sum "$TMP_ROOT/acceptance-one/config/configuration.json" | cut -d' ' -f1
)"
if tabctl --instance acceptance-one admin add "200000001,200000002"; then
  echo "Repeated administrator add unexpectedly succeeded." >&2
  exit 1
fi
test "$(
  sha256sum "$TMP_ROOT/acceptance-one/config/configuration.json" | cut -d' ' -f1
)" = "$config_after_admin"

tabctl --instance acceptance-one source add \
  "@SourceOne,https://t.me/SourceTwo"
config_after_source="$(
  sha256sum "$TMP_ROOT/acceptance-one/config/configuration.json" | cut -d' ' -f1
)"
if tabctl --instance acceptance-one source add \
  "@SourceOne,https://t.me/SourceTwo"; then
  echo "Repeated source add unexpectedly succeeded." >&2
  exit 1
fi
test "$(
  sha256sum "$TMP_ROOT/acceptance-one/config/configuration.json" | cut -d' ' -f1
)" = "$config_after_source"

tabctl --instance acceptance-one source disable SourceOne
tabctl --instance acceptance-one source enable SourceOne
tabctl --instance acceptance-one repair --dry-run
tabctl --instance acceptance-one diagnostics \
  >"$TMP_ROOT/acceptance-one-diagnostics.json"

valid_config_hash="$(
  sha256sum "$TMP_ROOT/acceptance-one/config/configuration.json" | cut -d' ' -f1
)"
if tabctl --instance acceptance-one retention set 0; then
  echo "Invalid retention mutation unexpectedly succeeded." >&2
  exit 1
fi
test "$(
  sha256sum "$TMP_ROOT/acceptance-one/config/configuration.json" | cut -d' ' -f1
)" = "$valid_config_hash"
tabctl --instance acceptance-one config check

test "$(find "$TMP_ROOT/acceptance-one/backups/config" \
  -maxdepth 1 -type f -name 'configuration-*.json' | wc -l)" -ge 4
test "$(stat -c '%a' "$TMP_ROOT/acceptance-one/config/configuration.json")" = "640"
test "$(stat -c '%u' "$TMP_ROOT/acceptance-one/config/configuration.json")" = "10001"
tabctl --instance acceptance-one admin list | grep '"telegram_user_id": 200000001'
tabctl --instance acceptance-one source list | grep '"username": "sourceone"'
tabctl --instance acceptance-one source list | grep -A2 \
  '"username": "sourceone"' | grep '"enabled": true'
if grep -F -e "$ACCEPTANCE_MONGO_AUTH" -e "$ACCEPTANCE_BOT_FIXTURE" \
  "$TMP_ROOT/acceptance-one-diagnostics.json"; then
  echo "Diagnostics exposed an acceptance credential fixture." >&2
  exit 1
fi

# --- management menu one-shot actions ---
env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action status \
  | grep -F "Instance:"
if env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action status \
  | grep -F "$ACCEPTANCE_BOT_FIXTURE"; then
  echo "Menu status exposed an acceptance credential fixture." >&2
  exit 1
fi
env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action services \
  | grep -F "SERVICE"
env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action session \
  | grep -E "^state="
env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action queues \
  >/dev/null
env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action media-usage \
  | grep -E "^media_bytes="
if env TABCTL_MANAGER="$ROOT/deploy/tabctl.py" TABCTL_NO_COLOR=1 \
  bash "$ROOT/deploy/menu.sh" --instance acceptance-one --action doctor \
  | grep -F "One or more checks failed"; then
  echo "Menu doctor reported failures for a healthy acceptance instance." >&2
  exit 1
fi

# --- manager session, env and typed config surface ---
tabctl --instance acceptance-one session status | grep -E "^state="
tabctl --instance acceptance-one env list | grep "TAB_TELEGRAM_BOT_TOKEN=configured"
printf '%s\n' "$ACCEPTANCE_BOT_FIXTURE" | \
  tabctl --instance acceptance-one env set TAB_TELEGRAM_BOT_TOKEN
tabctl --instance acceptance-one config set timezone Asia/Tehran
if tabctl --instance acceptance-one config set timezone Not/AZone; then
  echo "Invalid timezone mutation unexpectedly succeeded." >&2
  exit 1
fi
tabctl --instance acceptance-one config set preview false
tabctl --instance acceptance-one config set cleanup-interval 1800

# --- encrypted backup round-trip ---
TAB_BACKUP_PASSPHRASE="$ACCEPTANCE_MONGO_AUTH" \
  tabctl --instance acceptance-one backup create --mode core --encrypt
encrypted_backup_id="$(TAB_BACKUP_PASSPHRASE="$ACCEPTANCE_MONGO_AUTH" \
  tabctl --instance acceptance-one backup list | tail -n1)"
TAB_BACKUP_PASSPHRASE="$ACCEPTANCE_MONGO_AUTH" \
  tabctl --instance acceptance-one backup verify "$encrypted_backup_id" \
  | grep '"encrypted": true'
if TAB_BACKUP_PASSPHRASE=wrong \
  tabctl --instance acceptance-one backup verify "$encrypted_backup_id"; then
  echo "Encrypted backup verified with the wrong passphrase." >&2
  exit 1
fi
TAB_BACKUP_PASSPHRASE="$ACCEPTANCE_MONGO_AUTH" \
  tabctl --instance acceptance-one backup delete "$encrypted_backup_id" --yes

# --- disposable instance: full backup, destroy, restore, health ---
restored=acceptance-restored
mkdir -p "$TMP_ROOT/$restored/config"
cp "$ROOT/compose.yaml" "$TMP_ROOT/$restored/compose.yaml"
cp "$ROOT/deploy/permissions.sh" "$TMP_ROOT/$restored/permissions.sh"
chmod 0700 "$TMP_ROOT/$restored/permissions.sh"
cat >"$TMP_ROOT/$restored/.env" <<EOF
COMPOSE_PROJECT_NAME=telegram-assist-$restored
TAB_INSTANCE_DIR=$TMP_ROOT/$restored
TAB_IMAGE=$IMAGE
TAB_MONGODB_USERNAME=acceptance
TAB_MONGODB_PASSWORD=$ACCEPTANCE_MONGO_AUTH
TAB_MONGODB_DATABASE=telegram_assist_${restored//-/_}
TAB_MONGODB_IMAGE=mongo:7.0.32
TAB_RUNTIME_UID=10001
TAB_RUNTIME_GID=10001
TAB_MONGODB_URI=mongodb://acceptance:$ACCEPTANCE_MONGO_AUTH@mongodb:27017/?authSource=admin&directConnection=true
TAB_TELEGRAM_API_ID=12345
TAB_TELEGRAM_API_HASH=00000000000000000000000000000000
TAB_TELEGRAM_PHONE_NUMBER=+10000000000
TAB_TELEGRAM_BOT_TOKEN=$ACCEPTANCE_BOT_FIXTURE
EOF
sudo bash "$TMP_ROOT/$restored/permissions.sh" repair \
  --instance-dir "$TMP_ROOT/$restored" \
  --runtime-uid 10001 --runtime-gid 10001 \
  --host-uid "$(id -u)" --host-gid "$(id -g)"
docker run --rm --user 10001:10001 \
  --env-file "$TMP_ROOT/$restored/.env" \
  --volume "$TMP_ROOT/$restored/config:/instance/config" \
  --volume "$ROOT/config/configuration.example.json:/instance/configuration.example.json:ro" \
  "$IMAGE" render-instance-config \
  --template /instance/configuration.example.json \
  --output /instance/config/configuration.json \
  --instance "$restored" \
  --retention-days 1 \
  --approval-chat-id -1009001 \
  --admin-user-ids "100000001" \
  --source-usernames "@RestoreSource" \
  --destination-name primary \
  --destination-id -1009002
sudo bash "$TMP_ROOT/$restored/permissions.sh" repair \
  --instance-dir "$TMP_ROOT/$restored" \
  --runtime-uid 10001 --runtime-gid 10001 \
  --host-uid "$(id -u)" --host-gid "$(id -g)"
compose "$restored" config --quiet
compose "$restored" up -d --wait --wait-timeout 120 mongodb
tabctl instance import --path "$TMP_ROOT/$restored" --name "$restored" >/dev/null
migration_backup="$(tabctl --instance "$restored" backup create --mode full | cut -d= -f2)"
test -n "$migration_backup"
tabctl --instance "$restored" backup verify "$migration_backup"
compose "$restored" down --volumes --remove-orphans
if docker volume inspect "telegram-assist-${restored}_mongodb_data" \
  >/dev/null 2>&1; then
  echo "Destroyed instance volume still exists before restore." >&2
  exit 1
fi
compose "$restored" up -d --wait --wait-timeout 120 mongodb
tabctl --instance "$restored" backup restore "$migration_backup" --yes \
  | grep "restore_status=healthy"
printf '%s\n' 'checkpoint=restore_config_check'
tabctl --instance "$restored" config check
printf '%s\n' 'checkpoint=restore_config_compare'
if ! cmp -s "$TMP_ROOT/$restored/config/configuration.json" \
  "$TMP_ROOT/$restored/backups/$migration_backup/configuration.json"; then
  echo "Restored configuration does not match the migration backup." >&2
  sha256sum "$TMP_ROOT/$restored/config/configuration.json" \
    "$TMP_ROOT/$restored/backups/$migration_backup/configuration.json" >&2
  exit 1
fi
printf '%s\n' 'checkpoint=restore_runtime_check'
if ! compose "$restored" ps --status running | grep -F runtime; then
  echo "Restored runtime is not running." >&2
  compose "$restored" ps --all >&2 || true
  exit 1
fi
printf '%s\n' 'checkpoint=restore_status_check'
if ! tabctl --instance "$restored" status --json \
  | grep -F '"instance": "acceptance-restored"'; then
  echo "Restored structured status did not identify the instance." >&2
  exit 1
fi
compose "$restored" down --volumes --remove-orphans
for volume in mongodb_data telegram_session media; do
  if docker volume inspect "telegram-assist-${restored}_${volume}" \
    >/dev/null 2>&1; then
    echo "Restored instance volume was not cleaned." >&2
    exit 1
  fi
done

for instance in "${INSTANCES[@]}"; do
  for volume in mongodb_data telegram_session media; do
    docker volume inspect "telegram-assist-${instance}_${volume}" >/dev/null
  done
  docker network inspect "telegram-assist-${instance}_application" >/dev/null
done
test "$(realpath "$TMP_ROOT/acceptance-one/config/configuration.json")" \
  != "$(realpath "$TMP_ROOT/acceptance-two/config/configuration.json")"
if cmp -s "$TMP_ROOT/acceptance-one/config/configuration.json" \
  "$TMP_ROOT/acceptance-two/config/configuration.json"; then
  echo "Acceptance Instances unexpectedly share identical Config content." >&2
  exit 1
fi

other_mongodb_id="$(compose acceptance-two ps -q mongodb)"
tabctl --instance acceptance-one restart
test "$(compose acceptance-two ps -q mongodb)" = "$other_mongodb_id"
compose acceptance-two ps --status running mongodb | grep mongodb

cleanup_resources
CLEANED=1
for instance in "${INSTANCES[@]}"; do
  for volume in mongodb_data telegram_session media; do
    if docker volume inspect "telegram-assist-${instance}_${volume}" >/dev/null 2>&1; then
      echo "Acceptance volume was not cleaned: $instance/$volume" >&2
      exit 1
    fi
  done
  if docker network inspect "telegram-assist-${instance}_application" \
    >/dev/null 2>&1; then
    echo "Acceptance network was not cleaned: $instance" >&2
    exit 1
  fi
done

echo "v1.1 acceptance checks passed"
