#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$ROOT/.env"
COMPOSE=(docker compose --project-name "$COMPOSE_PROJECT_NAME" \
  --env-file "$ROOT/.env" -f "$ROOT/compose.yaml")
ACTION="${1:-help}"

case "$ACTION" in
  start) "${COMPOSE[@]}" up -d ;;
  stop) "${COMPOSE[@]}" stop ;;
  restart) "${COMPOSE[@]}" restart ;;
  status) "${COMPOSE[@]}" ps ;;
  logs) "${COMPOSE[@]}" logs --tail 200 -f "${2:-}" ;;
  update) "${COMPOSE[@]}" pull; "${COMPOSE[@]}" up -d ;;
  login) "${COMPOSE[@]}" run --rm runtime login --config /app/config/configuration.json ;;
  config-check) "${COMPOSE[@]}" run --rm runtime check --config /app/config/configuration.json ;;
  backup)
    mkdir -p "$ROOT/backups"
    stamp="$(date -u +%Y%m%dT%H%M%SZ)"
    "${COMPOSE[@]}" exec -T mongodb sh -c \
      'mongodump --quiet --archive --gzip --username "$MONGO_INITDB_ROOT_USERNAME" --password "$MONGO_INITDB_ROOT_PASSWORD" --authenticationDatabase admin' \
      >"$ROOT/backups/mongodb-${stamp}.archive.gz"
    ;;
  uninstall) "${COMPOSE[@]}" down; echo "Data volumes and instance files were preserved." ;;
  purge)
    if [[ "${2:-}" != "--yes" ]]; then
      echo "Run 'manage.sh purge --yes' to delete only this instance's volumes." >&2
      exit 2
    fi
    "${COMPOSE[@]}" down --volumes --remove-orphans
    ;;
  *)
    echo "Usage: manage.sh {start|stop|restart|status|logs|update|login|config-check|backup|uninstall|purge --yes}"
    ;;
esac
