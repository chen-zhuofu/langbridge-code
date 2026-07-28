import os
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Editable install maps src/ → langbridge_code; for bare script runs, put
    # the repo root on path after `uv sync` (preferred) or fail clearly.
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_code.settings import INSTALL_ROOT, ensure_api_credentials
from langbridge_code.tools.common.runtime import (
    RuntimeBootstrapError,
    ensure_tui_tools,
    managed_binary,
)

TUI_DIR = INSTALL_ROOT / "tui"
TUI_DIST = INSTALL_ROOT / "tui" / "dist" / "cli.js"


def ensure_tui_built(npm: str) -> None:
    """Install locked TUI dependencies and build them on the first launch."""
    if TUI_DIST.exists():
        return
    if not (TUI_DIR / "package-lock.json").is_file():
        raise RuntimeBootstrapError(f"TUI source is missing from {TUI_DIR}.")

    print("First run: installing and building the terminal UI...", file=sys.stderr)
    commands = ([npm, "ci", "--no-audit", "--no-fund"], [npm, "run", "build"])
    for command in commands:
        try:
            completed = subprocess.run(command, cwd=TUI_DIR, check=False)
        except OSError as error:
            raise RuntimeBootstrapError(
                f"Could not run {' '.join(command)}: {error}"
            ) from error
        if completed.returncode != 0:
            raise RuntimeBootstrapError(
                f"TUI setup command failed ({completed.returncode}): {' '.join(command)}"
            )
    if not TUI_DIST.is_file():
        raise RuntimeBootstrapError(f"TUI build did not create {TUI_DIST}.")


def main():
    try:
        ensure_tui_tools()
        node = managed_binary("node")
        ensure_tui_built(managed_binary("npm"))
    except RuntimeBootstrapError as error:
        print(f"LangBridge setup failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    # Ask / validate API credentials on a plain TTY before Ink takes over stdin.
    try:
        ensure_api_credentials()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    raise SystemExit(subprocess.run([node, str(TUI_DIST)], cwd=os.getcwd()).returncode)


if __name__ == "__main__":
    main()
