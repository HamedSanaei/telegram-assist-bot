"""PyInstaller launcher for the main application (``src.main``).

PyInstaller needs a plain script entrypoint; this file only delegates
to the real module entrypoint.
"""

from src.main import main

if __name__ == "__main__":
    main()
