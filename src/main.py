import os
import shutil
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):
    # Editable install maps src/ → langbridge_code; for bare script runs, put
    # the repo root on path after `uv sync` (preferred) or fail clearly.
    sys.path.append(str(Path(__file__).resolve().parents[1]))

from langbridge_code.settings import INSTALL_ROOT, ensure_api_credentials

TUI_DIST = INSTALL_ROOT / "tui" / "dist" / "cli.js"


def _node_executable() -> str | None:
    override = os.environ.get("LANGBRIDGE_NODE")
    if override:
        return override
    found = shutil.which("node")
    if found:
        return found
    fallback = Path.home() / ".local" / "node" / "bin" / "node"
    if fallback.exists():
        return str(fallback)
    return None


def main():
    node = _node_executable()
    if node is None:
        print(
            "Node.js not found. Install Node.js (or set LANGBRIDGE_NODE to the node binary).",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not TUI_DIST.exists():
        print(
            "TUI build missing. Build it with: cd tui && npm install && npm run build",
            file=sys.stderr,
        )
        raise SystemExit(1)
    # Ask / validate API credentials on a plain TTY before Ink takes over stdin.
    try:
        ensure_api_credentials()
    except ValueError as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from error
    raise SystemExit(subprocess.run([node, str(TUI_DIST)], cwd=os.getcwd()).returncode)


if __name__ == "__main__":
    main()
