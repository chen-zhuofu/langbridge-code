"""Package discovery for flat ``src/`` layout under the ``langbridge_code`` import name."""

from __future__ import annotations

import shutil
from pathlib import Path

from setuptools import find_packages, setup
from setuptools.command.build_py import build_py as _build_py

ROOT = Path(__file__).resolve().parent
TUI_SRC = ROOT / "tui"
TUI_IGNORE_NAMES = frozenset({"node_modules", "dist", ".git"})

langbridge_packages = ["langbridge_code"] + [
    f"langbridge_code.{name}" for name in find_packages(where="src")
]
eval_packages = find_packages(
    where="eval", include=["util*", "util.*", "sandbox*", "sandbox.*"]
)


def _ignore_tui_build_artifacts(_directory: str, names: list[str]) -> set[str]:
    return {name for name in names if name in TUI_IGNORE_NAMES}


def _copy_tui_into_build(build_lib: str) -> None:
    """Bundle repo-root ``tui/`` into the installed ``langbridge_code`` package."""
    if not (TUI_SRC / "package-lock.json").is_file():
        raise SystemExit(f"TUI source missing or incomplete: {TUI_SRC}")
    dest = Path(build_lib) / "langbridge_code" / "tui"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(TUI_SRC, dest, ignore=_ignore_tui_build_artifacts)


class build_py(_build_py):
    def run(self) -> None:
        super().run()
        _copy_tui_into_build(self.build_lib)


setup(
    package_dir={
        "langbridge_code": "src",
        "util": "eval/util",
        "sandbox": "eval/sandbox",
    },
    packages=langbridge_packages + eval_packages,
    cmdclass={"build_py": build_py},
)
