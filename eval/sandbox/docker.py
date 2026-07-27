"""Thin wrappers around the docker CLI."""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path


def docker(args, **kwargs):
    return subprocess.run(["docker", *args], capture_output=True, text=True, **kwargs)


def image_exists(image: str) -> bool:
    return docker(["image", "inspect", image]).returncode == 0


def container_exec(name, command, env=None, timeout=None, workdir=None, stdin_path=None):
    args = ["exec", "-i"]
    if workdir:
        args += ["-w", workdir]
    for key, value in (env or {}).items():
        args += ["-e", f"{key}={value}"]
    args += [name, "bash", "-lc", command]

    stdin = open(stdin_path, "rb") if stdin_path else subprocess.DEVNULL
    try:
        return subprocess.run(
            ["docker", *args],
            stdin=stdin,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=timeout,
        )
    finally:
        if stdin_path:
            stdin.close()


def write_and_copy_script(container, script_text: str, remote_path: str, timeout=None):
    """Write a temp script on the host and docker-cp it into the container."""
    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as handle:
        handle.write(script_text)
        local = handle.name
    try:
        result = docker(["cp", local, f"{container}:{remote_path}"])
        if result.returncode != 0:
            raise RuntimeError(
                f"docker cp script failed: {(result.stderr or '').strip()}"
            )
        return container_exec(
            container, f"bash {remote_path}", timeout=timeout
        )
    finally:
        Path(local).unlink(missing_ok=True)
