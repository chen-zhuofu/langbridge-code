"""Internal Docker network + LLM-only egress proxy for langbridge-bench eval."""
from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse

from .docker import docker

EVAL_NETWORK = "lb-eval-net"
PROXY_NAME = "lb-eval-proxy"
PROXY_PORT = 3128
PROXY_URL = f"http://{PROXY_NAME}:{PROXY_PORT}"
PROXY_SCRIPT = Path(__file__).resolve().parent / "egress_proxy.py"


def active_api_host() -> str:
    from langbridge_code.settings import API_BASE_URL

    host = urlparse(API_BASE_URL).hostname or ""
    if not host:
        sys.exit("Cannot determine LLM API host from settings.API_BASE_URL.")
    return host


def ensure_internal_network() -> None:
    """Create the --internal eval network; recreate if a non-internal one exists."""
    inspect = docker(["network", "inspect", "-f", "{{.Internal}}", EVAL_NETWORK])
    if inspect.returncode == 0:
        if (inspect.stdout or "").strip() == "true":
            return
        removed = docker(["network", "rm", EVAL_NETWORK])
        if removed.returncode != 0:
            sys.exit(
                f"Docker network {EVAL_NETWORK!r} exists but is not internal, and "
                f"could not be removed (containers attached?): {removed.stderr.strip()}"
            )
    created = docker(["network", "create", "--internal", EVAL_NETWORK])
    if created.returncode != 0:
        sys.exit(f"docker network create {EVAL_NETWORK} failed: {created.stderr.strip()}")


def ensure_egress_guard(image: str) -> tuple[list[str], str]:
    """Start the allowlist proxy; return (docker args for agents, api_host)."""
    api_host = active_api_host()
    ensure_internal_network()

    docker(["rm", "-f", PROXY_NAME])
    created = docker(
        [
            "create",
            "--name",
            PROXY_NAME,
            "--restart",
            "unless-stopped",
            "-e",
            f"ALLOWED_HOSTS={api_host}",
            "-e",
            f"PORT={PROXY_PORT}",
            image,
            "python3",
            "-u",
            "/egress_proxy.py",
        ]
    )
    if created.returncode != 0:
        sys.exit(f"docker create {PROXY_NAME} failed: {created.stderr.strip()}")
    copied = docker(["cp", str(PROXY_SCRIPT), f"{PROXY_NAME}:/egress_proxy.py"])
    if copied.returncode != 0:
        sys.exit(f"docker cp egress_proxy.py failed: {copied.stderr.strip()}")
    connected = docker(["network", "connect", EVAL_NETWORK, PROXY_NAME])
    if connected.returncode != 0:
        sys.exit(f"docker network connect failed: {connected.stderr.strip()}")
    started = docker(["start", PROXY_NAME])
    if started.returncode != 0:
        sys.exit(f"docker start {PROXY_NAME} failed: {started.stderr.strip()}")

    return ["--network", EVAL_NETWORK], api_host


def verify_network_lockdown(image: str, net_args: list[str], api_host: str) -> None:
    """Canary from the agent network: API via proxy works, everything else doesn't."""

    def probe(cmd: str) -> bool:
        return docker(["run", "--rm", *net_args, image, "bash", "-c", cmd]).returncode == 0

    if probe("timeout 5 bash -c '</dev/tcp/github.com/443'"):
        sys.exit(
            f"Network guard check failed: direct github.com:443 IS reachable from "
            f"{EVAL_NETWORK} — the network is not internal."
        )
    if not probe(
        f"curl -s -o /dev/null --connect-timeout 8 -x {PROXY_URL} https://{api_host}/"
    ):
        sys.exit(
            f"Network guard check failed: {api_host} unreachable via {PROXY_NAME}.\n"
            f"Check: docker logs {PROXY_NAME}"
        )
    if probe(
        f"curl -s -o /dev/null --connect-timeout 8 -x {PROXY_URL} https://github.com/"
    ):
        sys.exit(
            f"Network guard check failed: proxy tunnelled github.com — allowlist broken."
        )
