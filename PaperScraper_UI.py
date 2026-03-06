"""Entry point for PaperScraper."""

import sys
import os

# When running as a PyInstaller bundle, sys._MEIPASS is set.
# Ensure the bundled package root is on sys.path.
if getattr(sys, "frozen", False):
    base = sys._MEIPASS  # type: ignore[attr-defined]
    if base not in sys.path:
        sys.path.insert(0, base)

from app.gui.main_window import MainWindow


def main():
    app = MainWindow()
    app.mainloop()


if __name__ == "__main__":
    main()
