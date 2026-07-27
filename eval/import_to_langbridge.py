"""Convert SWE-bench instances into langbridge-bench style under data/swe-bench.

Writes per difficulty (``lite``, ``verified``, ``pro``):
  data/swe-bench/<difficulty>/specs/<id>.json
  data/swe-bench/<difficulty>/docker-images/<id>/Dockerfile
  data/swe-bench/<difficulty>/drop/drop.json

Dockerfiles wrap official instance images so the langbridge-bench runner can treat
them as ``lb-task:<id>`` (/work/repo + .refvenv).

  uv run python eval/import_to_langbridge.py --difficulty lite --count 10
  uv run python eval/import_to_langbridge.py --difficulty verified --prefer-local --pull --count 10
  uv run python eval/import_to_langbridge.py --difficulty pro --pull --count 10
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from datasets import load_dataset
from swebench.harness.test_spec.test_spec import make_test_spec

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SWE_BENCH_ROOT = PROJECT_ROOT / "data" / "swe-bench"
HARNESS_CONTEXT = SWE_BENCH_ROOT / "_harness"

TEST_FILE_RE = re.compile(r"^\+\+\+ b/(.+)$", re.MULTILINE)

DATASETS = {
    "lite": "princeton-nlp/SWE-bench_Lite",
    "verified": "princeton-nlp/SWE-bench_Verified",
    "pro": "ScaleAI/SWE-bench_Pro",
}

PRO_DOCKERHUB_REPO = "jefzda/sweap-images"


def _parse_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return list(value)
    if isinstance(value, str):
        value = value.strip()
        if not value:
            return []
        try:
            parsed = json.loads(value)
            return list(parsed) if isinstance(parsed, list) else []
        except json.JSONDecodeError:
            return []
    return []


def test_files_in_patch(test_patch: str) -> list[str]:
    return TEST_FILE_RE.findall(test_patch or "")


def docker_image_exists(tag: str) -> bool:
    return (
        subprocess.run(
            ["docker", "image", "inspect", tag],
            capture_output=True,
            text=True,
        ).returncode
        == 0
    )


def out_dirs(difficulty: str) -> tuple[Path, Path, Path]:
    root = SWE_BENCH_ROOT / difficulty
    return root / "specs", root / "docker-images", root / "drop"


def local_swebench_instance_ids(namespace: str = "swebench") -> set[str]:
    """Parse instance ids from local ``swebench/sweb.eval.*`` image tags."""
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    ids: set[str] = set()
    # e.g. swebench/sweb.eval.x86_64.astropy_1776_astropy-12907:latest
    # → astropy__astropy-12907
    pattern = re.compile(rf"^{re.escape(namespace)}/sweb\.eval\.[^.]+\.(.+):")
    for line in (result.stdout or "").splitlines():
        match = pattern.match(line.strip())
        if not match:
            continue
        body = match.group(1)
        parts = re.split(r"_\d+_", body, maxsplit=1)
        if len(parts) != 2:
            continue
        owner_key, name_pr = parts
        ids.add(f"{owner_key}__{name_pr}")
    return ids


def local_pro_instance_ids(repo: str = PRO_DOCKERHUB_REPO) -> set[str]:
    """Parse instance ids from local ``jefzda/sweap-images:*`` tags."""
    result = subprocess.run(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    prefix = f"{repo}:"
    ids: set[str] = set()
    for line in (result.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith(prefix):
            continue
        tag = line[len(prefix) :]
        ids.add(f"instance_{tag}-vnan")
    return ids


def select_rows(
    rows: list,
    *,
    count: int,
    instance_ids: list[str] | None,
    prefer_local: bool,
    namespace: str,
    difficulty: str,
):
    if instance_ids:
        wanted = set(instance_ids)
        return [r for r in rows if r["instance_id"] in wanted]
    if prefer_local:
        if difficulty == "pro":
            local_tags = {
                row.get("dockerhub_tag")
                for row in rows
                if row.get("dockerhub_tag")
                and docker_image_exists(f"{PRO_DOCKERHUB_REPO}:{row['dockerhub_tag']}")
            }
            local_rows = [r for r in rows if r.get("dockerhub_tag") in local_tags]
            remote_rows = [r for r in rows if r.get("dockerhub_tag") not in local_tags]
            print(f"Local Pro images matched {len(local_rows)} dataset instances")
        else:
            local_ids = local_swebench_instance_ids(namespace)
            local_rows = [r for r in rows if r["instance_id"] in local_ids]
            remote_rows = [r for r in rows if r["instance_id"] not in local_ids]
            print(f"Local SWE-bench images matched {len(local_rows)} dataset instances")
        return (local_rows + remote_rows)[:count]
    return rows[:count]


def upstream_image_tag(row: dict, *, difficulty: str, namespace: str = "swebench") -> str:
    if difficulty == "pro":
        tag = (row.get("dockerhub_tag") or "").strip()
        if not tag:
            raise RuntimeError(f"missing dockerhub_tag for {row.get('instance_id')}")
        return f"{PRO_DOCKERHUB_REPO}:{tag}"
    spec = make_test_spec(row, namespace=namespace)
    return getattr(spec, "instance_image_key", None) or getattr(
        spec, "instance_image_tag", None
    )


HARNESS_IMAGE = "lb-swebench-harness:py312"


def render_harness_dockerfile() -> str:
    return """# Shared harness layer for SWE-bench → lb-task adapters.
FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \\
    && rm -rf /var/lib/apt/lists/* \\
    && curl -LsSf https://astral.sh/uv/install.sh | sh \\
    && export PATH="/root/.local/bin:$PATH" \\
    && ln -sfn /root/.local/bin/uv /usr/local/bin/uv \\
    && uv pip install --system openai httpx numpy prompt_toolkit textual pytest
"""


def render_swebench_adapter_dockerfile(upstream: str) -> str:
    """Wrap official SWE-bench image for langbridge-bench layout."""
    return f"""# Adapter: SWE-bench official image → langbridge-bench lb-task layout.
# Upstream already has /testbed at base_commit + conda env ``testbed``.
FROM {HARNESS_IMAGE} AS harness
FROM {upstream}

USER root

# langbridge-bench expects the repo at /work/repo with a .refvenv for grading.
RUN mkdir -p /work \\
    && ln -sfn /testbed /work/repo \\
    && mkdir -p /work/repo/.refvenv/bin \\
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python \\
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python3 \\
    && (ln -sfn /opt/miniconda3/envs/testbed/bin/pytest /work/repo/.refvenv/bin/pytest || true)

# Copy Python 3.12 + harness deps from shared layer (avoid reinstall per task).
COPY --from=harness /usr/local /usr/local
COPY --from=harness /root/.local /root/.local

# Django SWE-bench images often lack pytest in the conda env; grading needs it.
RUN /opt/miniconda3/envs/testbed/bin/python -c "import pytest" 2>/dev/null \\
    || /opt/miniconda3/envs/testbed/bin/pip install -q pytest \\
    && mkdir -p /work/repo/.refvenv/bin \\
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python \\
    && ln -sfn /opt/miniconda3/envs/testbed/bin/python /work/repo/.refvenv/bin/python3 \\
    && ln -sfn /opt/miniconda3/envs/testbed/bin/pytest /work/repo/.refvenv/bin/pytest

ENV PATH="/usr/local/bin:/root/.local/bin:/opt/miniconda3/envs/testbed/bin:${{PATH}}"
WORKDIR /work/repo
"""


def render_pro_adapter_dockerfile(upstream: str) -> str:
    """Wrap SWE-bench Pro image (/app repo) for langbridge-bench layout."""
    return f"""# Adapter: SWE-bench Pro image → langbridge-bench lb-task layout.
# Upstream repo lives at /app (not /testbed).
FROM {HARNESS_IMAGE} AS harness
FROM {upstream}

USER root

RUN mkdir -p /work \\
    && ln -sfn /app /work/repo \\
    && mkdir -p /work/repo/.refvenv/bin \\
    && ln -sfn /usr/bin/python3 /work/repo/.refvenv/bin/python \\
    && ln -sfn /usr/bin/python3 /work/repo/.refvenv/bin/python3

COPY --from=harness /usr/local /usr/local
COPY --from=harness /root/.local /root/.local

ENV PATH="/usr/local/bin:/root/.local/bin:${{PATH}}"
WORKDIR /work/repo
"""


def render_adapter_dockerfile(upstream: str, *, difficulty: str) -> str:
    if difficulty == "pro":
        return render_pro_adapter_dockerfile(upstream)
    return render_swebench_adapter_dockerfile(upstream)


def ensure_harness_image(*, rebuild: bool) -> None:
    if not rebuild and docker_image_exists(HARNESS_IMAGE):
        return
    context = HARNESS_CONTEXT
    context.mkdir(parents=True, exist_ok=True)
    (context / "Dockerfile").write_text(render_harness_dockerfile(), encoding="utf-8")
    print(f"  building {HARNESS_IMAGE} ...")
    result = subprocess.run(
        [
            "docker",
            "build",
            "-t",
            HARNESS_IMAGE,
            "-f",
            str(context / "Dockerfile"),
            str(context),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {HARNESS_IMAGE}:\n"
            f"{(result.stdout or '')[-1500:]}\n{(result.stderr or '')[-1500:]}"
        )


def row_to_spec(row: dict, *, upstream_image: str, difficulty: str, dataset: str) -> dict:
    task_id = row["instance_id"]
    f2p = _parse_list(row.get("FAIL_TO_PASS") or row.get("fail_to_pass"))
    p2p = _parse_list(row.get("PASS_TO_PASS") or row.get("pass_to_pass"))
    test_patch = row.get("test_patch") or ""
    test_files = test_files_in_patch(test_patch)
    if not test_files and difficulty == "pro":
        test_files = _parse_list(row.get("selected_test_files_to_run"))
    difficulty_label = {
        "lite": "easy",
        "verified": "medium",
        "pro": "hard",
    }[difficulty]
    metadata = {
        "source": dataset,
        "swebench_instance_id": task_id,
        "swebench_image": upstream_image,
        "swebench_variant": difficulty,
    }
    if difficulty == "pro":
        metadata.update(
            {
                "dockerhub_tag": row.get("dockerhub_tag"),
                "before_repo_set_cmd": row.get("before_repo_set_cmd") or "",
                "repo_language": row.get("repo_language") or "",
            }
        )
    else:
        metadata.update(
            {
                "version": row.get("version"),
                "created_at": row.get("created_at"),
                "environment_setup_commit": row.get("environment_setup_commit"),
                "hints_text": row.get("hints_text") or "",
            }
        )
    return {
        "task_id": task_id,
        "status": "ok",
        "repo": row["repo"],
        "base_commit": row["base_commit"],
        "problem_statement": row.get("problem_statement") or "",
        "test_files": test_files,
        "test_patch": test_patch,
        "gold_code_patch": row.get("patch") or "",
        "fail_to_pass": f2p,
        "pass_to_pass": p2p,
        "hard": len(f2p) >= 2,
        "difficulty": difficulty_label,
        "task_type": "bug_fix",
        "problem_statement_source": "swe-bench",
        "docker_image": f"lb-task:{task_id}",
        "metadata": metadata,
    }


def ensure_upstream(image: str, *, pull: bool) -> None:
    if docker_image_exists(image):
        return
    if not pull:
        raise RuntimeError(f"missing upstream image {image} (pass --pull)")
    print(f"  pulling {image} ...")
    result = subprocess.run(["docker", "pull", image], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"docker pull failed for {image}:\n{(result.stderr or '')[-1500:]}"
        )


def build_lb_task(task_id: str, context: Path, *, rebuild: bool) -> None:
    tag = f"lb-task:{task_id}"
    if not rebuild and docker_image_exists(tag):
        print(f"  image exists: {tag}")
        return
    print(f"  building {tag} ...")
    result = subprocess.run(
        ["docker", "build", "-t", tag, "-f", str(context / "Dockerfile"), str(context)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"docker build failed for {tag}:\n"
            f"{(result.stdout or '')[-1500:]}\n{(result.stderr or '')[-1500:]}"
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--difficulty",
        choices=list(DATASETS),
        default="lite",
        help="SWE-bench variant: lite, verified, or pro",
    )
    parser.add_argument("--split", default="test")
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument(
        "--instance-id",
        action="append",
        dest="instance_ids",
        help="Explicit instance id (repeatable). Overrides --count ordering.",
    )
    parser.add_argument(
        "--prefer-local",
        action="store_true",
        help="Prefer instances whose official swebench image is already local.",
    )
    parser.add_argument("--pull", action="store_true", help="docker pull missing upstream images")
    parser.add_argument("--rebuild", action="store_true", help="rebuild lb-task adapters")
    parser.add_argument("--namespace", default="swebench")
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Only write specs/Dockerfiles; do not build lb-task images",
    )
    args = parser.parse_args()

    difficulty = args.difficulty
    dataset = DATASETS[difficulty]
    specs_dir, docker_dir, drop_dir = out_dirs(difficulty)
    rows = select_rows(
        list(load_dataset(dataset, split=args.split)),
        count=args.count,
        instance_ids=args.instance_ids,
        prefer_local=args.prefer_local,
        namespace=args.namespace,
        difficulty=difficulty,
    )

    if not rows:
        raise SystemExit("No instances selected")

    specs_dir.mkdir(parents=True, exist_ok=True)
    docker_dir.mkdir(parents=True, exist_ok=True)
    drop_dir.mkdir(parents=True, exist_ok=True)
    drop_path = drop_dir / "drop.json"
    if not drop_path.exists():
        drop_path.write_text("[]\n", encoding="utf-8")

    if not args.skip_build:
        ensure_harness_image(rebuild=args.rebuild)

    written = []
    for index, row in enumerate(rows, start=1):
        task_id = row["instance_id"]
        print(f"[{index}/{len(rows)}] {task_id}")
        upstream = upstream_image_tag(row, difficulty=difficulty, namespace=args.namespace)
        ensure_upstream(upstream, pull=args.pull or args.prefer_local)

        spec = row_to_spec(row, upstream_image=upstream, difficulty=difficulty, dataset=dataset)
        (specs_dir / f"{task_id}.json").write_text(
            json.dumps(spec, indent=2) + "\n", encoding="utf-8"
        )

        context = docker_dir / task_id
        context.mkdir(parents=True, exist_ok=True)
        (context / "Dockerfile").write_text(
            render_adapter_dockerfile(upstream, difficulty=difficulty), encoding="utf-8"
        )
        if not args.skip_build:
            build_lb_task(task_id, context, rebuild=args.rebuild)
        written.append(task_id)

    print(f"\nWrote {len(written)} specs under {specs_dir}")
    print(f"Docker contexts under {docker_dir}")
    print(
        "Run eval with:\n"
        f"  uv run python eval/run_eval.py --bench-dir {specs_dir.parent} "
        "--limit 10 --open-network"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
