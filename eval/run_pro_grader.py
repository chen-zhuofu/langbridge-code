"""Run Scale's official SWE-bench Pro grader with stable local Docker limits.

The upstream grader remains the source of task scripts and pass/fail logic.
This wrapper only caps grader concurrency and injects the resource controls
that eliminated local amd64 Go compiler/GCC/go-vet segmentation faults.

Example:

    uv run python eval/run_pro_grader.py \
      --grader-script /path/to/SWE-bench_Pro-os/swe_bench_pro_eval.py \
      --raw_sample_path sample.jsonl \
      --patch_path predictions-pro.json \
      --output_dir official-grade \
      --dockerhub_username jefzda \
      --scripts_dir /path/to/SWE-bench_Pro-os/scripts/run_scripts \
      --use_local_docker --block_network
"""

from __future__ import annotations

import argparse
import runpy
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator


MAX_GRADER_WORKERS = 2
GRADER_NANO_CPUS = 2_000_000_000
GRADER_PLATFORM = "linux/amd64"
GRADER_GO_ENV = {
    "GOFLAGS": "-p=1",
    "GOMAXPROCS": "2",
}


def clamp_grader_workers(
    argv: list[str], *, max_workers: int = MAX_GRADER_WORKERS
) -> list[str]:
    """Return official-grader argv with ``--num_workers`` capped."""
    output: list[str] = []
    found = False
    index = 0
    while index < len(argv):
        argument = argv[index]
        if argument == "--num_workers":
            if index + 1 >= len(argv):
                raise ValueError("--num_workers requires an integer")
            requested = int(argv[index + 1])
            output.extend(
                ["--num_workers", str(max(1, min(requested, max_workers)))]
            )
            found = True
            index += 2
            continue
        if argument.startswith("--num_workers="):
            requested = int(argument.split("=", 1)[1])
            output.append(f"--num_workers={max(1, min(requested, max_workers))}")
            found = True
            index += 1
            continue
        output.append(argument)
        index += 1
    if not found:
        output.extend(["--num_workers", str(max_workers)])
    return output


def resource_safe_run_kwargs(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Inject build scheduling limits without changing grader semantics."""
    safe = dict(kwargs)
    environment = safe.get("environment")
    if environment is None:
        environment = {}
    if not isinstance(environment, dict):
        raise TypeError("official grader Docker environment must be a mapping")
    safe["environment"] = {**environment, **GRADER_GO_ENV}
    safe["nano_cpus"] = GRADER_NANO_CPUS
    safe.setdefault("platform", GRADER_PLATFORM)
    return safe


@contextmanager
def patched_docker_run() -> Iterator[None]:
    """Patch Docker SDK container creation only for the official grader run."""
    from docker.models.containers import ContainerCollection

    original = ContainerCollection.run

    def resource_safe_run(self, *args, **kwargs):
        return original(self, *args, **resource_safe_run_kwargs(kwargs))

    ContainerCollection.run = resource_safe_run
    try:
        yield
    finally:
        ContainerCollection.run = original


def run_official_grader(grader_script: Path, grader_args: list[str]) -> None:
    script = grader_script.expanduser().resolve()
    if not script.is_file():
        raise FileNotFoundError(f"official Pro grader script not found: {script}")
    previous_argv = sys.argv
    previous_path = list(sys.path)
    sys.argv = [str(script), *clamp_grader_workers(grader_args)]
    sys.path.insert(0, str(script.parent))
    try:
        with patched_docker_run():
            runpy.run_path(str(script), run_name="__main__")
    finally:
        sys.argv = previous_argv
        sys.path[:] = previous_path


def parse_args(argv: list[str] | None = None) -> tuple[Path, list[str]]:
    parser = argparse.ArgumentParser(
        description="Resource-safe launcher for Scale's official Pro grader.",
    )
    parser.add_argument("--grader-script", required=True, type=Path)
    known, grader_args = parser.parse_known_args(argv)
    if grader_args[:1] == ["--"]:
        grader_args = grader_args[1:]
    return known.grader_script, grader_args


def main(argv: list[str] | None = None) -> int:
    grader_script, grader_args = parse_args(argv)
    run_official_grader(grader_script, grader_args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
