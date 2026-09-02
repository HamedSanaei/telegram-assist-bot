#!/usr/bin/env bash
set -Eeuo pipefail

# Global `tabctl` entry point.
#
# With no arguments it opens the interactive Bash management menu.
# With arguments it delegates to the tabctl Python manager so every existing
# non-interactive command keeps working for automation.

SELF_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
MANAGER="${TABCTL_MANAGER:-}"

if [[ -z "$MANAGER" ]]; then
  for candidate in \
    "$SELF_DIR/tabctl.py" \
    "$SELF_DIR/../lib/telegram-assist-bot/tabctl.py" \
    /usr/local/lib/telegram-assist-bot/tabctl.py \
    "$HOME/.local/lib/telegram-assist-bot/tabctl.py" \
    /usr/local/bin/tabctl.py; do
    if [[ -f "$candidate" ]]; then
      MANAGER="$candidate"
      break
    fi
  done
fi

if [[ -z "$MANAGER" || ! -f "$MANAGER" ]]; then
  echo "tabctl manager was not found; reinstall with install.sh." >&2
  exit 3
fi

if (($#)); then
  exec python3 "$MANAGER" "$@"
fi

MENU="${TABCTL_MENU:-}"
if [[ -z "$MENU" ]]; then
  for candidate in \
    "$SELF_DIR/menu.sh" \
    "$SELF_DIR/../lib/telegram-assist-bot/menu.sh" \
    /usr/local/lib/telegram-assist-bot/menu.sh \
    "$HOME/.local/lib/telegram-assist-bot/menu.sh"; do
    if [[ -f "$candidate" ]]; then
      MENU="$candidate"
      break
    fi
  done
fi

if [[ -z "$MENU" || ! -f "$MENU" ]]; then
  echo "Management menu was not found; reinstall with install.sh." >&2
  exit 3
fi

exec bash "$MENU"