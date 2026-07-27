"""Package discovery for flat ``src/`` layout under the ``langbridge_code`` import name."""

from setuptools import find_packages, setup

langbridge_packages = ["langbridge_code"] + [
    f"langbridge_code.{name}" for name in find_packages(where="src")
]
eval_packages = find_packages(where="eval", include=["util*", "util.*", "sandbox*", "sandbox.*"])

setup(
    package_dir={
        "langbridge_code": "src",
        "util": "eval/util",
        "sandbox": "eval/sandbox",
    },
    packages=langbridge_packages + eval_packages,
)
