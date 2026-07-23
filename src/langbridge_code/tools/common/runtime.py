"""Repo-local managed runtime for agent tool dependencies.

LangBridge tools must not be advertised and then fail because an executable is
missing.  This module installs native command-line dependencies into
``<workspace>/.langbridge/runtime`` and prepends that prefix to tool subprocess
environments.  There is deliberately no feature fallback: bootstrap either
produces a working toolchain or fails before the agent starts.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
from pathlib import Path

NATIVE_PACKAGES = {
    "rg": "ripgrep",
    "git": "git",
    "bash": "bash",
}
CONFIGURED_BINARY_ENV = {
    "rg": "LANGBRIDGE_RG_PATH",
}
MICROMAMBA_BASE_URL = "https://micro.mamba.pm/api/micromamba"
INSTALL_TIMEOUT_SECONDS = 900
RUNTIME_IGNORE_PATTERN = ".langbridge/runtime/"


class RuntimeBootstrapError(RuntimeError):
    """Raised when the managed tool runtime cannot be prepared."""


def runtime_root() -> Path:
    override = os.environ.get("LANGBRIDGE_RUNTIME_DIR")
    if override:
        return Path(override).expanduser().resolve()
    from langbridge_code.settings import WORKSPACE_ROOT

    return Path(WORKSPACE_ROOT).resolve() / ".langbridge" / "runtime"


def _ensure_runtime_ignored() -> None:
    """Keep the generated runtime out of git without editing tracked files."""
    from langbridge_code.settings import WORKSPACE_ROOT

    exclude = Path(WORKSPACE_ROOT).resolve() / ".git" / "info" / "exclude"
    if not exclude.parent.is_dir():
        return
    try:
        existing = exclude.read_text(encoding="utf-8") if exclude.exists() else ""
        if RUNTIME_IGNORE_PATTERN not in existing.splitlines():
            exclude.parent.mkdir(parents=True, exist_ok=True)
            suffix = "" if not existing or existing.endswith("\n") else "\n"
            exclude.write_text(
                existing + suffix + RUNTIME_IGNORE_PATTERN + "\n",
                encoding="utf-8",
            )
    except OSError as error:
        raise RuntimeBootstrapError(
            f"Could not add {RUNTIME_IGNORE_PATTERN} to {exclude}: {error}"
        ) from error


def runtime_prefix() -> Path:
    return runtime_root() / "prefix"


def runtime_bin_dir() -> Path:
    prefix = runtime_prefix()
    if os.name == "nt":
        return prefix / "Library" / "bin"
    return prefix / "bin"


def test_venv_dir() -> Path:
    return runtime_root() / "test-venv"


def _prepend_path(env: dict[str, str], directory: Path) -> None:
    entries = [entry for entry in env.get("PATH", "").split(os.pathsep) if entry]
    value = str(directory)
    if value not in entries:
        env["PATH"] = os.pathsep.join([value, *entries])


def inject_runtime_env(env: dict[str, str]) -> dict[str, str]:
    """Add the managed toolchain to an environment."""
    _prepend_path(env, runtime_bin_dir())
    return env


def activate_runtime() -> None:
    """Activate the managed runtime for direct subprocess users."""
    inject_runtime_env(os.environ)


def _platform_name() -> str:
    machine = platform.machine().lower()
    if sys.platform.startswith("linux"):
        return "linux-aarch64" if machine in {"aarch64", "arm64"} else "linux-64"
    if sys.platform == "darwin":
        return "osx-arm64" if machine in {"aarch64", "arm64"} else "osx-64"
    if os.name == "nt":
        return "win-64"
    raise RuntimeBootstrapError(
        f"Managed runtime is not supported on {sys.platform!r}/{machine!r}."
    )


def _micromamba_path() -> Path:
    name = "micromamba.exe" if os.name == "nt" else "micromamba"
    return runtime_root() / "bootstrap" / name


def _safe_extract_member(archive: tarfile.TarFile, member: tarfile.TarInfo, target: Path) -> None:
    destination = (target / member.name).resolve()
    try:
        destination.relative_to(target.resolve())
    except ValueError as error:
        raise RuntimeBootstrapError(
            f"Unsafe path in micromamba archive: {member.name}"
        ) from error
    archive.extract(member, target)


def _install_micromamba() -> Path:
    binary = _micromamba_path()
    if binary.exists():
        return binary

    root = runtime_root()
    root.mkdir(parents=True, exist_ok=True)
    archive_path = root / "micromamba.tar.bz2"
    extract_dir = root / "micromamba-extract"
    url = f"{MICROMAMBA_BASE_URL}/{_platform_name()}/latest"
    try:
        urllib.request.urlretrieve(url, archive_path)
        extract_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive_path, "r:bz2") as archive:
            members = [
                member
                for member in archive.getmembers()
                if Path(member.name).name in {"micromamba", "micromamba.exe"}
            ]
            if not members:
                raise RuntimeBootstrapError(
                    "The micromamba archive did not contain its executable."
                )
            for member in members:
                _safe_extract_member(archive, member, extract_dir)
        extracted = next(
            path
            for path in extract_dir.rglob("*")
            if path.name in {"micromamba", "micromamba.exe"} and path.is_file()
        )
        binary.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(extracted, binary)
        binary.chmod(binary.stat().st_mode | 0o111)
    except (OSError, tarfile.TarError, StopIteration) as error:
        raise RuntimeBootstrapError(
            f"Could not install micromamba into {root}: {error}"
        ) from error
    finally:
        archive_path.unlink(missing_ok=True)
        shutil.rmtree(extract_dir, ignore_errors=True)
    return binary


def _run_checked(command: list[str], *, env: dict[str, str] | None = None) -> None:
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=INSTALL_TIMEOUT_SECONDS,
            check=False,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeBootstrapError(
            f"Runtime setup command failed to start: {' '.join(command)}: {error}"
        ) from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout or "").strip()
        raise RuntimeBootstrapError(
            f"Runtime setup command failed ({completed.returncode}): "
            f"{' '.join(command)}\n{detail}"
        )


def _binary_available(name: str) -> bool:
    configured = _configured_binary(name)
    if configured:
        return True
    env = inject_runtime_env(dict(os.environ))
    return shutil.which(name, path=env.get("PATH")) is not None


def _configured_binary(name: str) -> str | None:
    """Resolve a binary shipped by a frontend, such as the TypeScript TUI."""
    env_name = CONFIGURED_BINARY_ENV.get(name)
    value = os.environ.get(env_name, "").strip() if env_name else ""
    if not value:
        return None
    candidate = Path(value).expanduser()
    if candidate.is_file() and os.access(candidate, os.X_OK):
        return str(candidate.resolve())
    return None


def ensure_native_tools() -> None:
    """Install missing rg/git/bash into the repo-local conda prefix."""
    missing = [name for name in NATIVE_PACKAGES if not _binary_available(name)]
    if not missing:
        activate_runtime()
        return

    micromamba = _install_micromamba()
    packages = [NATIVE_PACKAGES[name] for name in missing]
    command = [
        str(micromamba),
        "create" if not (runtime_prefix() / "conda-meta").exists() else "install",
        "--yes",
        "--prefix",
        str(runtime_prefix()),
        "--channel",
        "conda-forge",
        *packages,
    ]
    env = dict(os.environ)
    env["MAMBA_ROOT_PREFIX"] = str(runtime_root() / "mamba")
    _run_checked(command, env=env)
    activate_runtime()

    still_missing = [name for name in missing if not _binary_available(name)]
    if still_missing:
        raise RuntimeBootstrapError(
            "Managed runtime installation completed but these tools are still "
            f"unavailable: {', '.join(still_missing)}"
        )


def managed_binary(name: str) -> str:
    """Resolve a required native tool, installing the managed runtime if needed."""
    if name not in NATIVE_PACKAGES:
        raise ValueError(f"Unknown managed native tool: {name}")
    configured = _configured_binary(name)
    if configured:
        return configured
    ensure_native_tools()
    env = inject_runtime_env(dict(os.environ))
    found = shutil.which(name, path=env.get("PATH"))
    if not found:
        raise RuntimeBootstrapError(
            f"Managed tool {name!r} is unavailable after runtime bootstrap."
        )
    return found


def _venv_python(directory: Path) -> Path:
    return directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ensure_managed_test_python() -> str:
    """Return a repo-local Python that always has pytest installed."""
    directory = test_venv_dir()
    python = _venv_python(directory)
    if python.exists():
        check = subprocess.run(
            [str(python), "-c", "import pytest"],
            capture_output=True,
            text=True,
            check=False,
        )
        if check.returncode == 0:
            return str(python)

    # Do not rely on the host's python3-venv/ensurepip packages. Micromamba
    # provides a complete Python + pytest prefix under the repository.
    shutil.rmtree(directory, ignore_errors=True)
    micromamba = _install_micromamba()
    env = dict(os.environ)
    env["MAMBA_ROOT_PREFIX"] = str(runtime_root() / "mamba")
    _run_checked(
        [
            str(micromamba),
            "create",
            "--yes",
            "--prefix",
            str(directory),
            "--channel",
            "conda-forge",
            "python",
            "pytest",
        ],
        env=env,
    )
    if not python.exists():
        raise RuntimeBootstrapError(
            f"Managed pytest environment did not create {python}."
        )
    return str(python)


def ensure_test_python(preferred: str) -> str:
    """Ensure pytest exists, preserving a workspace venv when one is present."""
    check = subprocess.run(
        [preferred, "-c", "import pytest"],
        capture_output=True,
        text=True,
        check=False,
    )
    if check.returncode == 0:
        return preferred

    from langbridge_code.settings import WORKSPACE_ROOT

    workspace_venv = Path(WORKSPACE_ROOT).resolve() / ".venv"
    try:
        Path(preferred).resolve().relative_to(workspace_venv)
    except ValueError:
        return ensure_managed_test_python()

    _run_checked([preferred, "-m", "pip", "install", "pytest"])
    return preferred


def bootstrap_runtime() -> None:
    """Prepare all dependencies needed by advertised agent tools."""
    _ensure_runtime_ignored()
    ensure_native_tools()
    ensure_managed_test_python()

