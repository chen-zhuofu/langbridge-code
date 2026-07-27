"""Put data-pipeline (for ``_lib``, stages) and bench root (for harness) on path."""
from __future__ import annotations

import sys
from pathlib import Path

_BENCH = Path(__file__).resolve().parents[1]
_PIPELINE = _BENCH / "data-pipeline"
for _p in (_PIPELINE, _BENCH):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
