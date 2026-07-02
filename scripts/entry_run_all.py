"""PyInstaller launcher for the all-in-one entrypoint (``src.run_all``).

PyInstaller needs a plain script entrypoint; this file only delegates
to the real module entrypoint.
"""

from src.run_all import main

if __name__ == "__main__":
    main()
