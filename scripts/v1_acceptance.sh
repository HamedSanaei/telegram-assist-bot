#!/usr/bin/env bash
set -euo pipefail

IMAGE="${TAB_IMAGE:-telegram-assist-bot:acceptance}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TMP_ROOT="$(mktemp -d)"
cleanup() {
  docker compose --project-name telegram-assist-acceptance-a --env-file "$TMP_ROOT/a/.env" \
    --file "$ROOT/compose.yaml" down >/dev/null 2>&1 || true
  docker compose --project-name telegram-assist-acceptance-b --env-file "$TMP_ROOT/b/.env" \
    --file "$ROOT/compose.yaml" down >/dev/null 2>&1 || true
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT

docker build --build-arg VERSION=1.0.0 --tag "$IMAGE" "$ROOT"
test "$(docker image inspect "$IMAGE" --format '{{.Config.User}}')" = "10001:10001"
docker run --rm --user 10001:10001 "$IMAGE" --help >/dev/null

TAB_MONGODB_USERNAME=acceptance TAB_MONGODB_PASSWORD=not-a-production-secret \
  TAB_MONGODB_DATABASE=telegram_assist_acceptance \
  bash "$ROOT/install.sh" --instance acceptance --retention-days 7 \
  --non-interactive --dry-run >/dev/null
pwsh -NoProfile -File "$ROOT/install.ps1" -Instance acceptance \
  -RetentionDays 7 -InstallDirectory "$TMP_ROOT/pwsh-dry-run" \
  -NonInteractive -DryRun >/dev/null

for instance in a b; do
  mkdir -p "$TMP_ROOT/$instance/config"
  cp "$ROOT/config/configuration.example.json" "$TMP_ROOT/$instance/config/configuration.json"
  cat > "$TMP_ROOT/$instance/.env" <<EOF
COMPOSE_PROJECT_NAME=telegram-assist-acceptance-$instance
TAB_INSTANCE_DIR=$TMP_ROOT/$instance
TAB_IMAGE=$IMAGE
TAB_MONGODB_USERNAME=acceptance
TAB_MONGODB_PASSWORD=not-a-production-secret
TAB_MONGODB_DATABASE=telegram_assist_acceptance_$instance
EOF
  docker compose --env-file "$TMP_ROOT/$instance/.env" --file "$ROOT/compose.yaml" config --quiet
  docker compose --env-file "$TMP_ROOT/$instance/.env" --file "$ROOT/compose.yaml" up -d mongodb
done

docker compose --project-name telegram-assist-acceptance-a --env-file "$TMP_ROOT/a/.env" \
  --file "$ROOT/compose.yaml" ps --status running mongodb | grep mongodb
docker compose --project-name telegram-assist-acceptance-b --env-file "$TMP_ROOT/b/.env" \
  --file "$ROOT/compose.yaml" ps --status running mongodb | grep mongodb
test "$(docker volume ls --filter name=telegram-assist-acceptance- --format '{{.Name}}' | sort -u | wc -l)" -ge 2
test "$(docker network ls --filter name=telegram-assist-acceptance- --format '{{.Name}}' | sort -u | wc -l)" -ge 2

docker compose --project-name telegram-assist-acceptance-a --env-file "$TMP_ROOT/a/.env" \
  --file "$ROOT/compose.yaml" down
docker volume inspect telegram-assist-acceptance-a_mongodb_data >/dev/null
docker compose --project-name telegram-assist-acceptance-b --env-file "$TMP_ROOT/b/.env" \
  --file "$ROOT/compose.yaml" ps --status running mongodb | grep mongodb

echo "v1 acceptance checks passed"
