#!/usr/bin/env bash
set -Eeuo pipefail

# Telegram Assist Bot interactive management menu.
#
# The normal operator experience:  run `tabctl` (or this script) and pick menu
# actions.  All heavy lifting is delegated to the tabctl Python manager and to
# Docker Compose; this script only renders menus and orchestrates commands.
# It never edits configuration files directly and never exposes secrets.

SELF="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PY="${TABCTL_PYTHON:-python3}"
MANAGER="${TABCTL_MANAGER:-}"
INSTANCE_FLAG=""
ACTION=""
SELECTED=""
COLOR=0
[[ -t 1 ]] && COLOR=1

if [[ -z "$MANAGER" ]]; then
  for candidate in \
    "$SELF/tabctl.py" \
    "$SELF/../lib/telegram-assist-bot/tabctl.py" \
    /usr/local/lib/telegram-assist-bot/tabctl.py \
    "$HOME/.local/lib/telegram-assist-bot/tabctl.py" \
    /usr/local/bin/tabctl.py; do
    if [[ -f "$candidate" ]]; then
      MANAGER="$candidate"
      break
    fi
  done
fi

usage() {
  cat <<'EOF'
Usage: menu.sh [--instance NAME] [--action NAME] [--help]

  --instance NAME  Operate on this registered instance (default: single
                   installed instance, otherwise an interactive picker).
  --action NAME    Run one non-interactive action and exit. Actions:
                   instances status services session media-usage queues
                   backups doctor config-check
  --help           Show this help.

Environment:
  TABCTL_MANAGER   Path to the tabctl Python manager (default: auto-detected).
  TABCTL_PYTHON    Python interpreter used for the manager (default: python3).
  TABCTL_INSTANCE  Default instance name when --instance is not passed.
  TABCTL_NO_COLOR  Set to 1 to disable colors even on a TTY.
EOF
}

while (($#)); do
  case "$1" in
    --instance) INSTANCE_FLAG="${2:-}"; shift 2 ;;
    --action) ACTION="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "${TABCTL_NO_COLOR:-0}" == "1" ]]; then
  COLOR=0
fi

if ! command -v "$PY" >/dev/null 2>&1; then
  echo "Python 3 is required for the tabctl manager." >&2
  exit 3
fi
if [[ -z "$MANAGER" || ! -f "$MANAGER" ]]; then
  echo "tabctl manager was not found; reinstall with install.sh." >&2
  exit 3
fi

paint() { # paint CODE TEXT
  local code="$1"
  shift
  if ((COLOR)); then
    printf '\033[%sm%s\033[0m' "$code" "$*"
  else
    printf '%s' "$*"
  fi
}

notice() { paint '1;32' "[OK]"; printf ' %s\n' "$*"; }
warning() { paint '1;33' "[!]"; printf ' %s\n' "$*"; }
error() { paint '1;31' "[ERROR]"; printf ' %s\n' "$*" >&2; }
heading() { paint '1;36' "$*"; printf '\n'; }
divider() { printf '%s\n' '----------------------------------------------------------------'; }

tc_raw() { "$PY" "$MANAGER" "$@"; }

resolve_instance() {
  if [[ -n "$INSTANCE_FLAG" ]]; then
    SELECTED="$INSTANCE_FLAG"
    return
  fi
  if [[ -n "${TABCTL_INSTANCE:-}" ]]; then
    SELECTED="$TABCTL_INSTANCE"
    return
  fi
  local entries count rows i name choice
  entries="$(tc_raw instance list 2>/dev/null || true)"
  count="$(printf '%s\n' "$entries" | grep -c . || true)"
  if ((count == 0)); then
    error "No installed instance was found."
    echo "Install one first with:  bash install.sh" >&2
    exit 3
  fi
  if ((count == 1)); then
    SELECTED="${entries%%$'\t'*}"
    return
  fi
  mapfile -t rows <<<"$entries"
  printf '\nInstalled instances:\n'
  for i in "${!rows[@]}"; do
    name="${rows[$i]%%$'\t'*}"
    printf '  %2d. %s\n' "$((i + 1))" "$name"
  done
  read -r -p "Select instance: " choice
  if [[ ! "$choice" =~ ^[0-9]+$ ]] \
    || ((choice < 1 || choice > ${#rows[@]})); then
    echo "Invalid selection." >&2
    exit 2
  fi
  SELECTED="${rows[$((choice - 1))]%%$'\t'*}"
}

instance_path() { # instance_path NAME -> installation path
  tc_raw instance show "$1" 2>/dev/null |
    "$PY" -c 'import json,sys
try:
    print(json.load(sys.stdin).get("installation_path", ""))
except Exception:
    print("")' || true
}

resolve_instance
SELF_INSTALL_DIR="$(instance_path "$SELECTED")"

tc() { tc_raw --instance "$SELECTED" "$@"; }

confirm() { # confirm PROMPT -> 0 when yes
  local answer
  read -r -p "${1:-Continue}? [y/N]: " answer
  [[ "${answer:-n}" =~ ^[yY](es)?$ ]]
}

need_instance_name() { # require typing the exact instance name
  local given
  printf '\n%s\n' "This action is DESTRUCTIVE and affects ONLY instance: $SELECTED"
  read -r -p "Type the instance name to continue (Enter cancels): " given
  [[ "$given" == "$SELECTED" ]]
}

hidden_prompt() { # hidden_prompt LABEL VAR
  local label="$1" value
  read -r -s -p "${label}: " value
  printf '\n'
  printf -v "$2" '%s' "$value"
}

# ---------------------------------------------------------------- rendering

status_json() { # status_json -> structured status JSON on stdout
  tc status --json 2>/dev/null || true
}

status_to_summary() { # reads TAB_STATUS_JSON
  "$PY" - "$SELECTED" <<'PY'
import json
import os
import sys

name = sys.argv[1]
try:
    data = json.loads(os.environ.get("TAB_STATUS_JSON", ""))
except Exception:
    print("instance=%s" % name)
    print("status=unavailable")
    raise SystemExit(0)


def human(value):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return "unknown"
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            if unit == "B":
                return "%d %s" % (value, unit)
            return "%.1f %s" % (value, unit)
        value /= 1024.0
    return "unknown"


print("instance=%s" % data.get("instance"))
print("application_version=%s" % data.get("application_version"))
state_counts = {}
for item in data.get("containers", []):
    key = str(item.get("state", "unknown"))
    state_counts[key] = state_counts.get(key, 0) + 1
print("containers=%s" % (
    ",".join(
        "%s:%d" % (state, count)
        for state, count in sorted(state_counts.items())
    ) if state_counts else "none"
))
session = data.get("session", {})
print("session=%s" % session.get("state", "unavailable"))
print("mongodb_health=%s" % data.get("mongodb_health", "unknown"))
media = data.get("media", {})
print("media_usage=%s" % (
    human(media.get("media_bytes"))
    if media.get("state") == "available" else "unknown"
))
print("disk_free=%s" % human(data.get("disk_free_bytes")))
config = data.get("config", {})
print("config=%s" % ("valid" if config.get("present") else "missing"))
latest = data.get("latest_backup")
print("last_backup=%s" % (
    latest.get("backup_id", "none") if latest else "none"
))
print("status=available")
PY
}

print_status() {
  local summary line key
  summary="$(TAB_STATUS_JSON="$(status_json)" status_to_summary)"
  divider
  while IFS= read -r line; do
    key="${line%%=*}"
    case "$key" in
      instance) printf '%-22s %s\n' "Instance:" "${line#*=}" ;;
      application_version) printf '%-22s %s\n' "Application:" "${line#*=}" ;;
      containers) printf '%-22s %s\n' "Containers:" "${line#*=}" ;;
      session) printf '%-22s %s\n' "Telegram Session:" "${line#*=}" ;;
      mongodb_health) printf '%-22s %s\n' "MongoDB:" "${line#*=}" ;;
      media_usage) printf '%-22s %s\n' "Media Usage:" "${line#*=}" ;;
      disk_free) printf '%-22s %s\n' "Free Disk:" "${line#*=}" ;;
      config) printf '%-22s %s\n' "Config:" "${line#*=}" ;;
      last_backup) printf '%-22s %s\n' "Last Backup:" "${line#*=}" ;;
      status)
        if [[ "${line#*=}" != "available" ]]; then
          warning "status summary unavailable"
        fi
        ;;
    esac
  done <<<"$summary"
}

print_services() {
  divider
  TAB_STATUS_JSON="$(status_json)" "$PY" - <<'PY'
import json
import os
import sys
try:
    data = json.loads(os.environ.get("TAB_STATUS_JSON", ""))
except Exception:
    print("containers=unavailable")
    raise SystemExit(0)
print("%-8s %-28s %-10s %-12s" % ("SERVICE", "NAME", "STATE", "HEALTH"))
for item in sorted(
    data.get("containers", []), key=lambda c: str(c.get("service"))
):
    print("%-8s %-28s %-10s %-12s" % (
        str(item.get("service", "?")),
        str(item.get("name", "?")).replace("/", "", 1),
        str(item.get("state", "?")),
        str(item.get("health", item.get("state", "?"))),
    ))
PY
}

print_session() {
  tc session status
}

print_media_usage() {
  divider
  tc media usage
  printf 'Free disk: %s\n' "$(status_json |
    "$PY" -c 'import json,sys
try:
    d=json.load(sys.stdin); v=d.get("disk_free_bytes")
    print(("%.1f GB" % (v/1073741824.0)) if v else "unknown")
except Exception:
    print("unknown")')"
}

print_queues() {
  divider
  printf '%s\n' "== Pending publications =="
  tc queue inspect --kind publication --status pending --limit 25 || true
  printf '%s\n' "== Pending approvals =="
  tc queue inspect --kind approval --status pending --limit 25 || true
}

print_backups() {
  divider
  if ! tc backup list 2>/dev/null | grep -q .; then
    printf '%s\n' "No backups yet."
    return
  fi
  tc backup list
}

# ---------------------------------------------------------------- doctor

DOCTOR_FAIL=0

doctor_ok() { notice "$1"; }
doctor_fail() {
  DOCTOR_FAIL=1
  paint '1;31' "[FAIL]"
  printf ' %s\n' "$1"
}

run_doctor() {
  DOCTOR_FAIL=0
  divider
  heading "Doctor report for instance '$SELECTED'"
  local app_image mongodb_health session_state env_mode
  app_image="$(status_json |
    "$PY" -c 'import json,sys
try:
    print(json.load(sys.stdin).get("application_image", ""))
except Exception:
    print("")')"
  mongodb_health="$(status_json |
    "$PY" -c 'import json,sys
try:
    print(json.load(sys.stdin).get("mongodb_health", "unknown"))
except Exception:
    print("unknown")')"
  session_state="$(tc session status 2>/dev/null |
    awk -F= '/^state=/{ print $2 }')"
  session_state="${session_state:-unavailable}"
  env_mode="$(stat -c %a "$SELF_INSTALL_DIR/.env" 2>/dev/null || true)"

  if [[ -n "$(uname -s)" ]]; then
    doctor_ok "OS detected: $(uname -s)"
  else
    doctor_fail "Operating system could not be detected"
  fi
  if command -v docker >/dev/null 2>&1; then
    doctor_ok "Docker executable present"
  else
    doctor_fail "Docker executable is missing"
  fi
  if docker version >/dev/null 2>&1; then
    doctor_ok "Docker daemon is reachable"
  else
    doctor_fail "Docker daemon is not reachable"
  fi
  if docker compose version >/dev/null 2>&1; then
    doctor_ok "Docker Compose is available"
  else
    doctor_fail "Docker Compose is unavailable"
  fi
  if [[ -f "$SELF_INSTALL_DIR/config/configuration.json" ]]; then
    doctor_ok "Configuration file present"
  else
    doctor_fail "Configuration file is missing"
  fi
  if [[ "$env_mode" == "600"* ]]; then
    doctor_ok ".env exists with restricted permissions"
  else
    doctor_fail ".env is missing or world-readable (mode: ${env_mode:-none})"
  fi
  if tc_raw instance list >/dev/null 2>&1; then
    doctor_ok "Instance registry is readable"
  else
    doctor_fail "Instance registry cannot be read"
  fi
  if [[ -n "$app_image" ]] && docker image inspect "$app_image" >/dev/null 2>&1; then
    doctor_ok "Application image is present locally"
  else
    doctor_fail "Application image is missing locally (select 'pull image')"
  fi
  if [[ "$mongodb_health" == "healthy" ]]; then
    doctor_ok "MongoDB is healthy"
  else
    doctor_fail "MongoDB is not healthy (state: $mongodb_health)"
  fi
  if [[ "$session_state" == "present" ]]; then
    doctor_ok "Telegram session is present"
  else
    doctor_fail "Telegram session is absent (run login)"
  fi
  if ((DOCTOR_FAIL)); then
    divider
    error "One or more checks failed."
    echo "Repair options are available from the Services and Update menus." >&2
    return 1
  fi
  notice "All checks passed."
}

# ---------------------------------------------------------------- actions

run_action() {
  case "$ACTION" in
    instances) print_instances ;;
    status) print_status ;;
    services) print_services ;;
    session) print_session ;;
    media-usage) print_media_usage ;;
    queues) print_queues ;;
    backups) print_backups ;;
    doctor) run_doctor ;;
    config-check)
      tc config check
      ;;
    "")
      return
      ;;
    *)
      echo "Unknown action: $ACTION" >&2
      exit 2
      ;;
  esac
}

print_instances() {
  tc_raw instance list
}

# ---------------------------------------------------------------- services

services_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Service Management" \
      " 1. Start all services" \
      " 2. Stop all services" \
      " 3. Restart all services" \
      " 4. Status" \
      " 5. Recreate containers" \
      " 6. Runtime (start/stop/restart)" \
      " 7. Approval bot (start/stop/restart)" \
      " 8. Media cleanup worker (start/stop/restart)" \
      " 9. MongoDB status" \
      "10. Docker Compose status" \
      "11. Configuration validation" \
      " 0. Back"
    local choice s
    read -r -p "Select: " choice
    case "$choice" in
      1) tc start ;;
      2) tc stop ;;
      3) tc restart ;;
      4) print_services ;;
      5) tc service recreate all ;;
      6)
        read -r -p "runtime: start(1) stop(2) restart(3): " s
        case "$s" in
          1) tc service start runtime ;;
          2) tc service stop runtime ;;
          3) tc service restart runtime ;;
        esac
        ;;
      7)
        read -r -p "approval-bot: start(1) stop(2) restart(3): " s
        case "$s" in
          1) tc service start approval-bot ;;
          2) tc service stop approval-bot ;;
          3) tc service restart approval-bot ;;
        esac
        ;;
      8)
        read -r -p "media-cleanup-worker: start(1) stop(2) restart(3): " s
        case "$s" in
          1) tc service start media-cleanup-worker ;;
          2) tc service stop media-cleanup-worker ;;
          3) tc service restart media-cleanup-worker ;;
        esac
        ;;
      9)
        status_json |
          "$PY" -c 'import json,sys
try:
    d=json.load(sys.stdin)
    print("mongodb_health=%s" % d.get("mongodb_health"))
except Exception:
    print("mongodb_health=unknown")'
        ;;
      10) tc status ;;
      11) tc config check ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- session

session_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Telegram Session" \
      " 1. Login (or re-login) to the Telegram User API" \
      " 2. Check session status" \
      " 3. Remove/reset the current session" \
      " 0. Back"
    local choice
    read -r -p "Select: " choice
    case "$choice" in
      1) tc login ;;
      2) print_session ;;
      3)
        warning "This deletes the session for instance '$SELECTED' only."
        if need_instance_name && confirm "Reset the Telegram session"; then
          tc session reset --yes
          echo "Log in again with the login action."
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- bot

bot_settings_summary() {
  tc env list 2>/dev/null || true
  "$PY" - "$SELF_INSTALL_DIR/config/configuration.json" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    print("config=unreadable")
    raise SystemExit(0)
print("approval_chat_id=%s" % data.get("telegram", {}).get("bot", {}).get("approval_chat_id"))
print("admins=%d" % len(data.get("admins", [])))
print("source_channels=%d" % len(data.get("source_channels", [])))
print("destination_channels=%d" % len(data.get("destination_channels", [])))
PY
}

bot_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Telegram Bot Settings" \
      " 1. Set/change Bot Token (hidden input)" \
      " 2. Show bot settings (token is never printed)" \
      " 3. Set approval chat ID" \
      " 4. Administrators" \
      " 5. Validate bot configuration" \
      " 0. Back"
    local choice token confirm_token chat_id
    read -r -p "Select: " choice
    case "$choice" in
      1)
        hidden_prompt "New Bot Token (hidden)" token
        hidden_prompt "Repeat Bot Token" confirm_token
        if [[ "$token" == "$confirm_token" ]]; then
          printf '%s\n' "$token" | tc env set TAB_TELEGRAM_BOT_TOKEN
          if confirm "Restart the approval bot now"; then
            tc service restart approval-bot
          fi
        else
          error "Tokens do not match; nothing was changed."
        fi
        ;;
      2) bot_settings_summary ;;
      3)
        read -r -p "Approval chat ID (for example -1001234567890): " chat_id
        if confirm "Apply approval chat change"; then
          tc config set approval-chat "$chat_id"
        fi
        ;;
      4) admins_menu ;;
      5) tc config check ;;
      0) return ;;
    esac
  done
}

admins_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Administrators" \
      " 1. List administrators" \
      " 2. Add administrator(s) (comma separated IDs)" \
      " 3. Remove administrator" \
      " 4. Enable administrator" \
      " 5. Disable administrator" \
      " 0. Back"
    local choice value
    read -r -p "Select: " choice
    case "$choice" in
      1) tc admin list ;;
      2)
        read -r -p "Administrator IDs (comma separated): " value
        tc admin add "$value"
        ;;
      3)
        read -r -p "Administrator ID: " value
        tc admin remove "$value"
        ;;
      4)
        read -r -p "Administrator ID: " value
        tc admin enable "$value"
        ;;
      5)
        read -r -p "Administrator ID: " value
        tc admin disable "$value"
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- channels

sources_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Source Channels" \
      " 1. List source channels" \
      " 2. Add source channel(s) (username or t.me link)" \
      " 3. Remove source channel" \
      " 4. Enable source channel" \
      " 5. Disable source channel" \
      " 0. Back"
    local choice value
    read -r -p "Select: " choice
    case "$choice" in
      1) tc source list ;;
      2)
        read -r -p "Usernames (comma separated, e.g. @One,t.me/Two): " value
        tc source add "$value"
        ;;
      3)
        read -r -p "Source username: " value
        tc source remove "$value"
        ;;
      4)
        read -r -p "Source username: " value
        tc source enable "$value"
        ;;
      5)
        read -r -p "Source username: " value
        tc source disable "$value"
        ;;
      0) return ;;
    esac
  done
}

destinations_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Destination Channels" \
      " 1. List destination channels" \
      " 2. Add destination" \
      " 3. Remove destination" \
      " 4. Enable destination" \
      " 5. Disable destination" \
      " 0. Back"
    local choice name cid username
    read -r -p "Select: " choice
    case "$choice" in
      1) tc destination list ;;
      2)
        read -r -p "Destination name: " name
        read -r -p "Telegram channel ID (negative number): " cid
        read -r -p "Username (optional): " username
        if confirm "Add destination '$name'"; then
          if [[ -n "$username" ]]; then
            tc destination add --name "$name" --id "$cid" --username "$username"
          else
            tc destination add --name "$name" --id "$cid"
          fi
        fi
        ;;
      3)
        read -r -p "Destination name: " name
        tc destination remove "$name"
        ;;
      4)
        read -r -p "Destination name: " name
        tc destination enable "$name"
        ;;
      5)
        read -r -p "Destination name: " name
        tc destination disable "$name"
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- config

config_settings() {
  "$PY" - "$SELF_INSTALL_DIR/config/configuration.json" <<'PY'
import json
import sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        data = json.load(handle)
except Exception:
    print("config=unreadable")
    raise SystemExit(0)
media = data.get("media", {})
bot = data.get("telegram", {}).get("bot", {})
print("timezone=%s" % data.get("timezone"))
print("logging_level=%s" % data.get("logging", {}).get("level"))
print("media_retention_days=%s" % media.get("retention_days"))
print("media_preview_enabled=%s" % media.get("preview_enabled"))
print("media_cleanup_interval_seconds=%s" % media.get("cleanup_interval_seconds"))
print("approval_chat_id=%s" % bot.get("approval_chat_id"))
PY
}

config_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Configuration" \
      " 1. Show current settings" \
      " 2. Set timezone" \
      " 3. Set media retention days" \
      " 4. Set media cleanup interval (seconds)" \
      " 5. Enable/disable media previews" \
      " 6. Set logging level" \
      " 7. Set approval chat ID" \
      " 8. Advanced: manually edit configuration.json" \
      " 9. Validate configuration and restart services" \
      " 0. Back"
    local choice value
    read -r -p "Select: " choice
    case "$choice" in
      1) config_settings ;;
      2)
        read -r -p "IANA timezone (e.g. Asia/Tehran, UTC): " value
        if confirm "Apply timezone '$value'"; then
          tc config set timezone "$value"
        fi
        ;;
      3)
        read -r -p "Retention days (1..3650): " value
        if confirm "Apply retention '$value' days"; then
          tc config set retention "$value"
        fi
        ;;
      4)
        read -r -p "Cleanup interval in seconds (60..604800): " value
        if confirm "Apply cleanup interval '$value'"; then
          tc config set cleanup-interval "$value"
        fi
        ;;
      5)
        read -r -p "Enable previews? [y/N]: " value
        if [[ "${value:-n}" =~ ^[yY](es)?$ ]]; then
          tc config set preview true
        else
          tc config set preview false
        fi
        ;;
      6)
        read -r -p "Logging level (INFO/DEBUG/WARNING/ERROR): " value
        case "$value" in
          INFO|DEBUG|WARNING|ERROR) tc config set logging "$value" ;;
          *) warning "Level must be INFO, DEBUG, WARNING or ERROR." ;;
        esac
        ;;
      7)
        read -r -p "Approval chat ID (for example -1001234567890): " value
        if confirm "Apply approval chat change"; then
          tc config set approval-chat "$value"
        fi
        ;;
      8)
        echo "Advanced option requested." >&2
        echo "Stop services, edit $SELF_INSTALL_DIR/config/configuration.json" >&2
        echo "with a UTF-8 editor, then validate with 'tabctl config check'." >&2
        ;;
      9) tc config check ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- logs

logs_menu() {
  local service="all"
  while true; do
    divider
    printf 'Service: %s\n' "$service"
    printf '%s\n' \
      "Logs" \
      " 1. Select service (runtime/approval-bot/media-cleanup-worker/mongodb/all)" \
      " 2. Last 50 lines" \
      " 3. Last 100 lines" \
      " 4. Last 500 lines" \
      " 5. Follow live logs (Ctrl+C to stop)" \
      " 6. Errors/warnings only" \
      " 7. Last 6 hours" \
      " 8. Export redacted diagnostics archive" \
      " 0. Back"
    local choice
    read -r -p "Select: " choice
    case "$choice" in
      1)
        read -r -p "Service (runtime/approval-bot/media-cleanup-worker/mongodb/all): " service
        case "$service" in
          runtime|approval-bot|media-cleanup-worker|mongodb|all) ;;
          *) warning "Unknown service." ;;
        esac
        ;;
      2) tc logs --service "$service" --tail 50 ;;
      3) tc logs --service "$service" --tail 100 ;;
      4) tc logs --service "$service" --tail 500 ;;
      5) tc logs --service "$service" --tail 100 --follow || true ;;
      6) tc logs --service "$service" --tail 500 --errors-only ;;
      7) tc logs --service "$service" --tail 500 --since 6h ;;
      8) tc diagnostics export ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- queues

queues_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Publication and Approval Queues" \
      " 1. Pending publications" \
      " 2. Publication retry / failed view" \
      " 3. Pending approvals" \
      " 4. Approval retry / failed view" \
      " 5. Cancel one publication job" \
      " 6. Retry one approval delivery" \
      " 7. Recover pre-send publication (document recovery)" \
      " 8. Recover failed immediate publication (dry-run first)" \
      " 9. Recover rejected document deliveries (dry-run first)" \
      " 0. Back"
    local choice value requeue from_time to_time
    read -r -p "Select: " choice
    case "$choice" in
      1) tc queue inspect --kind publication --status pending --limit 25 ;;
      2)
        read -r -p "Status (retry/permanent-failed/completed): " value
        tc queue inspect --kind publication --status "$value" --limit 25
        ;;
      3) tc queue inspect --kind approval --status pending --limit 25 ;;
      4)
        read -r -p "Status (retry/permanent-failed/completed): " value
        tc queue inspect --kind approval --status "$value" --limit 25
        ;;
      5)
        read -r -p "Publication job ID: " value
        if confirm "Cancel publication job '$value'"; then
          tc queue cancel --job-id "$value"
        fi
        ;;
      6)
        read -r -p "Approval post ID: " value
        if confirm "Retry approval delivery for '$value'"; then
          tc queue retry --approval-post-id "$value"
        fi
        ;;
      7)
        read -r -p "Approval post ID: " value
        if confirm "Run document recovery (pre-send) for '$value'"; then
          tc queue recover presend --approval-post-id "$value"
        fi
        ;;
      8)
        read -r -p "Approval post ID: " value
        echo "Running DRY-RUN first:"
        tc queue recover immediate --approval-post-id "$value" --dry-run || true
        if confirm "Execute the immediate-publication recovery"; then
          read -r -p "Requeue failed jobs instead of canceling? [y/N]: " requeue
          if [[ "${requeue:-n}" =~ ^[yY](es)?$ ]]; then
            tc queue recover immediate --approval-post-id "$value" --requeue
          else
            tc queue recover immediate --approval-post-id "$value"
          fi
        fi
        ;;
      9)
        read -r -p "Approval post ID (empty for time range): " value
        if [[ -n "$value" ]]; then
          echo "Running DRY-RUN first:"
          tc queue recover documents --approval-post-id "$value" --dry-run || true
          if confirm "Execute document recovery for '$value'"; then
            tc queue recover documents --approval-post-id "$value"
          fi
        else
          read -r -p "From time (ISO-8601, e.g. 2026-08-01T00:00:00+03:30): " from_time
          read -r -p "To time (ISO-8601): " to_time
          echo "Running DRY-RUN first:"
          tc queue recover documents --from-time "$from_time" --to-time "$to_time" \
            --dry-run || true
          if confirm "Execute document recovery for this range"; then
            tc queue recover documents --from-time "$from_time" --to-time "$to_time"
          fi
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- media

media_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Media Management" \
      " 1. Show media usage" \
      " 2. Run safe media cleanup now" \
      " 3. Set media retention days" \
      " 4. Set cleanup interval (seconds)" \
      " 5. Enable/disable media previews" \
      " 6. Media cleanup worker status" \
      " 7. Filesystem free space" \
      " 8. DESTRUCTIVE: clear all local media" \
      " 0. Back"
    local choice value
    read -r -p "Select: " choice
    case "$choice" in
      1) print_media_usage ;;
      2)
        if confirm "Run one bounded safe cleanup batch"; then
          tc media cleanup
        fi
        ;;
      3)
        read -r -p "Retention days (1..3650): " value
        tc config set retention "$value"
        ;;
      4)
        read -r -p "Cleanup interval in seconds (60..604800): " value
        tc config set cleanup-interval "$value"
        ;;
      5)
        read -r -p "Enable previews? [y/N]: " value
        if [[ "${value:-n}" =~ ^[yY](es)?$ ]]; then
          tc config set preview true
        else
          tc config set preview false
        fi
        ;;
      6)
        status_json |
          "$PY" -c 'import json,sys
try:
    d = json.load(sys.stdin)
    for item in d.get("containers", []):
        if item.get("service") == "media-cleanup-worker":
            print("state=%s health=%s" % (item.get("state"), item.get("health")))
except Exception:
    print("state=unknown")'
        ;;
      7)
        status_json |
          "$PY" -c 'import json,sys
try:
    v = json.load(sys.stdin).get("disk_free_bytes")
    print("free_disk=%.1f GB" % (v / 1073741824.0)) if v else print("free_disk=unknown")
except Exception:
    print("free_disk=unknown")'
        ;;
      8)
        warning "This permanently deletes ALL locally stored media for '$SELECTED'."
        echo "Telegram source/destination posts are NOT touched."
        if confirm "Create a full backup (including media) first - recommended"; then
          tc backup create
        fi
        if need_instance_name && confirm "Clear all local media now"; then
          tc media clear --yes
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- backup

backup_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Backup and Restore" \
      " 1. Full backup (config + database + session + media + credentials)" \
      " 2. Full backup without media" \
      " 3. Core backup (config + metadata + database)" \
      " 4. Encrypted full backup" \
      " 5. List backups" \
      " 6. Verify a backup" \
      " 7. Restore a backup into this instance" \
      " 8. Restore a backup into another instance" \
      " 9. Export a backup archive (for moving servers)" \
      "10. Import a backup archive" \
      "11. Delete a backup" \
      " 0. Back"
    local choice id passphrase output archive target
    read -r -p "Select: " choice
    case "$choice" in
      1)
        if confirm "Create a full backup"; then
          tc backup create
        fi
        ;;
      2)
        if confirm "Create a full backup without media"; then
          tc backup create --exclude-media
        fi
        ;;
      3)
        if confirm "Create a core backup"; then
          tc backup create --mode core
        fi
        ;;
      4)
        if confirm "Create an encrypted full backup"; then
          hidden_prompt "Backup passphrase (hidden)" passphrase
          TAB_BACKUP_PASSPHRASE="$passphrase" tc backup create --encrypt
        fi
        ;;
      5) print_backups ;;
      6)
        read -r -p "Backup ID: " id
        hidden_prompt "Passphrase if encrypted (hidden; Enter if not)" passphrase
        TAB_BACKUP_PASSPHRASE="$passphrase" tc backup verify "$id"
        ;;
      7)
        print_backups
        read -r -p "Backup ID to restore: " id
        if need_instance_name && confirm "Restore '$id' into '$SELECTED'"; then
          hidden_prompt "Passphrase if encrypted (hidden; Enter if not)" passphrase
          TAB_BACKUP_PASSPHRASE="$passphrase" tc backup restore "$id" --yes
        fi
        ;;
      8)
        print_instances
        read -r -p "Target instance name: " target
        read -r -p "Backup ID to restore: " id
        if [[ "$target" == "$SELECTED" ]]; then
          warning "Use option 7 for the same instance."
        elif confirm "Restore '$id' into instance '$target'"; then
          hidden_prompt "Passphrase if encrypted (hidden; Enter if not)" passphrase
          TAB_BACKUP_PASSPHRASE="$passphrase" \
            tc backup restore "$id" --yes --to-instance "$target"
        fi
        ;;
      9)
        read -r -p "Backup ID to export: " id
        read -r -p "Output path (empty for default): " output
        if [[ -n "$output" ]]; then
          tc backup export "$id" --output "$output"
        else
          tc backup export "$id"
        fi
        ;;
      10)
        read -r -p "Archive path (.tar.gz): " archive
        if confirm "Import backup archive '$archive'"; then
          tc backup import --file "$archive"
        fi
        ;;
      11)
        print_backups
        read -r -p "Backup ID to delete: " id
        if need_instance_name && confirm "Delete backup '$id'"; then
          tc backup delete "$id" --yes
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- docker

docker_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Docker Management (scoped to this project)" \
      " 1. Container states" \
      " 2. Show project images and sizes" \
      " 3. Pull the configured application image" \
      " 4. Recreate containers" \
      " 5. Show Compose configuration" \
      " 6. Docker disk usage (informational)" \
      " 7. Remove one old application image (safe, confirmed)" \
      " 8. Rollback update" \
      " 0. Back"
    local choice app_image line inst_path project image_name
    read -r -p "Select: " choice
    app_image="$(status_json |
      "$PY" -c 'import json,sys
try:
    print(json.load(sys.stdin).get("application_image", ""))
except Exception:
    print("")')"
    case "$choice" in
      1) print_services ;;
      2)
        printf '%s\n' "== Application and MongoDB images =="
        docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' \
          | grep -E "^${app_image%:*}|mongo" || true
        ;;
      3)
        if [[ -n "$app_image" ]] && confirm "Pull $app_image"; then
          docker pull "$app_image"
        fi
        ;;
      4) tc service recreate all ;;
      5)
        if confirm "Render Compose configuration"; then
          inst_path="$(instance_path "$SELECTED")"
          project="$(tc_raw instance show "$SELECTED" 2>/dev/null |
            "$PY" -c 'import json,sys
try:
    print(json.load(sys.stdin).get("compose_project_name", ""))
except Exception:
    print("")' || true)"
          if [[ -n "$inst_path" && -f "$inst_path/compose.yaml" ]]; then
            docker compose --project-name "$project" \
              --env-file "$inst_path/.env" \
              --file "$inst_path/compose.yaml" config
          else
            warning "Instance files are incomplete."
          fi
        fi
        ;;
      6) docker system df ;;
      7)
        if [[ -n "$app_image" ]]; then
          printf '%s\n' "== Images from this project's repository =="
          docker images --format '{{.Repository}}:{{.Tag}}\t{{.ID}}\t{{.Size}}' \
            | grep "^${app_image%:*}:" || true
        fi
        read -r -p "Image to REMOVE (must belong to this project's image): " image_name
        case "$image_name" in
          "${app_image%:*}:*")
            if need_instance_name && confirm "Remove Docker image '$image_name'"; then
              docker rmi "$image_name"
            fi
            ;;
          *)
            warning "Only images of this project's repository may be removed."
            ;;
        esac
        ;;
      8)
        if confirm "Roll back to the previously running image"; then
          tc update --rollback
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- update

update_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Update / Rollback" \
      " 1. Check configured application image" \
      " 2. Update to a pinned version (auto backup + rollback on failure)" \
      " 3. Rollback to the previous image" \
      " 0. Back"
    local choice version
    read -r -p "Select: " choice
    case "$choice" in
      1) tc update --check ;;
      2)
        read -r -p "Exact version X.Y.Z (e.g. 1.1.3): " version
        if confirm "Update to '$version' (a backup is created first)"; then
          tc update --version "$version"
        fi
        ;;
      3)
        if confirm "Rollback the application image and configuration"; then
          tc update --rollback
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- instances

instances_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Instance Management" \
      " 1. List instances" \
      " 2. Status of all instances" \
      " 3. Create a new instance" \
      " 4. Import an existing instance" \
      " 5. Select the working instance" \
      " 6. Backup menu (current instance)" \
      " 7. Restore menu (current instance)" \
      " 8. Uninstall containers (keep data)" \
      " 9. PURGE instance (delete volumes and data)" \
      " 0. Back"
    local choice name path row
    read -r -p "Select: " choice
    case "$choice" in
      1) print_instances ;;
      2)
        while IFS=$'\t' read -r name _ _; do
          printf '\n== %s ==\n' "$name"
          "$PY" "$MANAGER" --instance "$name" status 2>/dev/null \
            | head -20 || true
        done < <(tc_raw instance list)
        ;;
      3)
        if [[ -f "$SELF/../install.sh" ]]; then
          echo "Running the installer; follow its prompts."
          bash "$SELF/../install.sh"
        else
          echo "Installer not available in this checkout."
          echo "Run:  bash <(curl -fsSL https://raw.githubusercontent.com/"
          echo "HamedSanaei/telegram-assist-bot/main/install.sh)"
        fi
        ;;
      4)
        read -r -p "Instance name: " name
        read -r -p "Absolute installation path: " path
        tc_raw instance import --path "$path" --name "$name"
        ;;
      5) resolve_instance ;;
      6) backup_menu ;;
      7) backup_menu ;;
      8)
        if confirm "Stop containers of '$SELECTED' (data is preserved)"; then
          tc uninstall --yes
        fi
        ;;
      9)
        warning "This deletes ALL data of instance '$SELECTED'."
        if need_instance_name && confirm "Purge instance '$SELECTED'"; then
          tc purge --yes
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- diagnostics

diagnostics_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Diagnostics / Doctor" \
      " 1. Run the full doctor report" \
      " 2. Export redacted diagnostics archive" \
      " 3. Repair permissions (bounded repair plan)" \
      " 4. Start all services" \
      " 5. Validation: configuration check" \
      " 0. Back"
    local choice
    read -r -p "Select: " choice
    case "$choice" in
      1) run_doctor || true ;;
      2) tc diagnostics export ;;
      3)
        tc repair --dry-run
        if confirm "Apply the bounded repair plan"; then
          tc repair --apply --yes
        fi
        ;;
      4) tc start ;;
      5) tc config check ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- uninstall

uninstall_menu() {
  while true; do
    divider
    printf '%s\n' \
      "Uninstall" \
      " 1. Uninstall containers (configuration and volumes are preserved)" \
      " 2. PURGE instance (delete containers, volumes and all data)" \
      " 0. Back"
    local choice
    read -r -p "Select: " choice
    case "$choice" in
      1)
        if confirm "Stop and remove only the containers of '$SELECTED'"; then
          tc uninstall --yes
        fi
        ;;
      2)
        warning "This permanently deletes every volume of '$SELECTED'."
        if need_instance_name && confirm "Purge instance '$SELECTED'"; then
          tc purge --yes
        fi
        ;;
      0) return ;;
    esac
  done
}

# ---------------------------------------------------------------- main

main_menu() {
  while true; do
    divider
    printf '%s\n' "Telegram Assist Bot"
    printf '%-22s %s\n' "Instance:" "$SELECTED"
    if [[ -t 0 ]]; then
      print_status
    fi
    divider
    printf '%s\n' \
      " 1. Service Management" \
      " 2. Telegram User Login / Session" \
      " 3. Telegram Bot Settings" \
      " 4. Source Channels" \
      " 5. Destination Channels" \
      " 6. Administrators / Approval Settings" \
      " 7. Configuration" \
      " 8. Status & Health" \
      " 9. Logs" \
      "10. Publication / Approval Queues" \
      "11. Media Management" \
      "12. Backup & Restore" \
      "13. Docker Management" \
      "14. Update / Rollback" \
      "15. Instance Management" \
      "16. Diagnostics / Repair" \
      "17. Uninstall" \
      "18. Exit"
    local choice
    read -r -p "Select [1-18]: " choice
    case "$choice" in
      1) services_menu ;;
      2) session_menu ;;
      3) bot_menu ;;
      4) sources_menu ;;
      5) destinations_menu ;;
      6) admins_menu ;;
      7) config_menu ;;
      8) print_status ;;
      9) logs_menu ;;
      10) queues_menu ;;
      11) media_menu ;;
      12) backup_menu ;;
      13) docker_menu ;;
      14) update_menu ;;
      15) instances_menu ;;
      16) diagnostics_menu ;;
      17) uninstall_menu ;;
      18)
        echo "Goodbye."
        exit 0
        ;;
      *) warning "Invalid choice." ;;
    esac
  done
}

if [[ -n "$ACTION" ]]; then
  run_action
  exit "$?"
fi

if [[ ! -t 0 ]]; then
  echo "Interactive menu requires a terminal; use --action for automation." >&2
  exit 2
fi

main_menu