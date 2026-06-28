"""Training subsystem: eval + evolver for the PM / L4 / L5 / L3 loop.

Ported (distilled) from the neighbouring coder/reviewer self-play worktrial and
mapped onto this repo's four roles:

  neighbour          this repo
  ---------          ---------
  coder              L4 (normal task) and L5 (hard task, divide-and-conquer)
  reviewer           L3 (tester)
  loop               L4<->L3 and L5<->L3 inner review loops
  (no analog)        PM (top-level decomposition + routing + e2e), evaluated too

Pieces:
  metrics.py  — compute_metrics + record/report for the five eval types
  signals.py  — trajectory signals (responsiveness/alignment/calibration) + batch
                pattern mining used by the evolver
  bench.py    — pluggable ground-truth grader (F2P/P2P over hidden tests)
  gate.py     — acceptance-gate scoring + applying an evolver proposal to a policy
  evals/      — eval runners that drive the real agents (injectable for tests)
  evolver.py  — the outer self-play loop that improves the agents via the policy
"""
