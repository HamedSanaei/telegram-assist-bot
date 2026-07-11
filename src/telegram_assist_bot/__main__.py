"""Execute the worker-free foundation startup check via ``python -m``."""

from telegram_assist_bot.bootstrap import main

if __name__ == "__main__":
    raise SystemExit(main())
