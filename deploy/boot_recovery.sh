#!/usr/bin/env bash
# Idempotent boot recovery for Telegram Assist Bot instances.
#
# Docker restart policies govern crashes while the daemon runs. `on-failure:20`
# deliberately stops after 20 bounded attempts so a permanent startup validation
# failure cannot create an unbounded restart storm, but that policy also means a
# stopped instance is not started again when the host reboots. This script closes
# that gap at the host layer: for every instance directory that contains a
# `compose.yaml`, it starts the stopped containers with the already configured
# policy. It never removes volumes, never re-creates containers, and never
# weakens the bounded restart contract.
set -euo pipefail

INSTANCE_ROOT="${TAB_INSTANCE_ROOT:-/opt/telegram-assist-bot/instances}"
COMPOSE_BIN="${TAB_COMPOSE_BIN:-docker}"
FAILED=0
STARTED=0

usage() {
  cat <<'EOF'
Usage: telegram-assist-boot-recovery [--root DIR]
  --root DIR   Directory containing one subdirectory per instance
               (default: /opt/telegram-assist-bot/instances)
  --help
EOF
}

while (($#)); do
  case "$1" in
    --root)
      INSTANCE_ROOT="${2:-}"
      shift 2
      ;;
    --help|-h)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ ! -d "${INSTANCE_ROOT}" ]]; then
  echo "Boot recovery: instance root '${INSTANCE_ROOT}' does not exist; nothing to do."
  exit 0
fi

for instance_dir in "${INSTANCE_ROOT}"/*; do
  [[ -d "${instance_dir}" ]] || continue
  [[ -f "${instance_dir}/compose.yaml" ]] || continue
  # This opt-in marker transfers only Kingofilter runtime to its scoped unit.
  # Other services retain boot recovery without reviving a disabled runtime.
  supervision_marker="${instance_dir}/.kingofilter-runtime-systemd"
  runtime_supervised=0
  if [[ -f "${supervision_marker}" ]]; then
    supervision_owner="$(<"${supervision_marker}")"
    if [[ "${supervision_owner}" == "telegram-assist-kingofilter-runtime.service" ]]; then
      runtime_supervised=1
    fi
  fi
  instance_name="$(basename "${instance_dir}")"
  echo "Boot recovery: starting instance '${instance_name}'."
  if (
    cd "${instance_dir}"
    if ((runtime_supervised)); then
      compose_command=("${COMPOSE_BIN}" compose)
      if [[ -f .env ]]; then
        compose_command+=(--env-file .env)
      fi
      configured_services="$("${compose_command[@]}" config --services)" || exit 1
      boot_services=()
      while IFS= read -r service; do
        if [[ -n "${service}" && "${service}" != "runtime" ]]; then
          boot_services+=("${service}")
        fi
      done <<< "${configured_services}"
      if ((${#boot_services[@]})); then
        "${compose_command[@]}" up -d --no-deps "${boot_services[@]}"
      fi
      exit "$?"
    fi
    if [[ -f .env ]]; then
      "${COMPOSE_BIN}" compose --env-file .env up -d
    else
      "${COMPOSE_BIN}" compose up -d
    fi
  ); then
    STARTED=$((STARTED + 1))
  else
    FAILED=$((FAILED + 1))
    echo "Boot recovery: instance '${instance_name}' could not be started." >&2
  fi
done

echo "Boot recovery: ${STARTED} instance(s) started, ${FAILED} failed."
if ((FAILED > 0)); then
  exit 1
fi
