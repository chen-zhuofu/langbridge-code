import os
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_code.ui.tui import run_tui


def main():
    run_tui()


if __name__ == "__main__":
    main()
