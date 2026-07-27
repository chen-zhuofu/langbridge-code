"""Portable Python 3.12 for LangBridge inside official SWE images.

Installs under ``/opt/lb-venv`` and must **not** be prepended onto PATH so that
bash ``python`` / ``pytest`` keep using the image toolchain.
"""
from __future__ import annotations

from .docker import container_exec

CONTAINER_VENV = "/opt/lb-venv"
AGENT_PYTHON = f"{CONTAINER_VENV}/bin/python"

BOOTSTRAP_SCRIPT = f"""
set -e
export PATH="/root/.local/bin:$PATH"
if [ ! -x {AGENT_PYTHON} ]; then
  if ! command -v uv >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh
  fi
  uv python install 3.12
  uv venv {CONTAINER_VENV} --python 3.12
  uv pip install --python {AGENT_PYTHON} openai httpx numpy
fi
{AGENT_PYTHON} -c "import langbridge_code.headless"
"""


def bootstrap_agent_venv(container: str, *, pythonpath: str, timeout: int = 900):
    """Install/verify the agent venv; return the container_exec result."""
    return container_exec(
        container,
        f"export PYTHONPATH={pythonpath} && {BOOTSTRAP_SCRIPT}",
        timeout=timeout,
        env={"PYTHONPATH": pythonpath},
    )
