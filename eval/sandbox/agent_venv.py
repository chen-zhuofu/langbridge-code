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
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | sh
    else
      _bootstrap_python="$(command -v python3 || command -v python || true)"
      if [ -z "$_bootstrap_python" ]; then
        echo "uv bootstrap needs curl, wget, or Python" >&2
        exit 1
      fi
      if ! "$_bootstrap_python" -m pip --version >/dev/null 2>&1; then
        "$_bootstrap_python" -m ensurepip --user
      fi
      PIP_BREAK_SYSTEM_PACKAGES=1 "$_bootstrap_python" -m pip install \
        --user --disable-pip-version-check \
        --index-url https://pypi.org/simple uv
    fi
  fi
  uv python install 3.12
  uv venv {CONTAINER_VENV} --python 3.12
  uv pip install --python {AGENT_PYTHON} openai httpx numpy
fi
for _import_attempt in 1 2 3; do
  if {AGENT_PYTHON} -c "import langbridge_code.headless"; then
    exit 0
  fi
  sleep 1
done
echo "LangBridge import failed after 3 attempts" >&2
exit 1
"""


def bootstrap_agent_venv(container: str, *, pythonpath: str, timeout: int = 900):
    """Install/verify the agent venv; return the container_exec result."""
    return container_exec(
        container,
        f"export PYTHONPATH={pythonpath} && {BOOTSTRAP_SCRIPT}",
        timeout=timeout,
        env={"PYTHONPATH": pythonpath},
    )
