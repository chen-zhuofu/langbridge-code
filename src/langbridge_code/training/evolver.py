"""evolver.py — the outer self-play loop that improves the agents.

This is the "trainer". It does NOT touch agent code; it edits the shared policy
(guidance + skills) that the roles fold into their prompts. One epoch walks the
task set in batches; for each batch it:

  1. runs the inner review loop on every task (fixed policy within a batch),
  2. grades each final diff with the hidden tests (offline; falls back to a jury
     when there is no ground truth),
  3. mines signals + recurring patterns from the batch traces,
  4. asks the evolver LLM for a policy proposal,
  5. applies it (reviewer levers gated by a trustworthy correctness anchor),
  6. runs the ACCEPTANCE GATE: re-runs the batch under the new policy and keeps the
     change only if the total penalty score improves; otherwise rolls back,
  7. checkpoints the policy.

Everything is injectable (loop_fn, grade, evolve_fn, jury_fn, judge) so the loop
is unit-testable with stubs and free of any hard LLM dependency. The default
evolve_fn / jury_fn that drive real models live behind make_* factories.
"""
import copy
import datetime
import json

from langbridge_cli import policy
from langbridge_cli.settings import (
    TRAIN_DEFAULT_BATCH_SIZE,
    TRAIN_DEFAULT_CHECKPOINT_EVERY,
    TRAIN_DEFAULT_EPOCHS,
)
from langbridge_cli.training import gate, signals


EVOLVER_SYSTEM = """You improve a team of coding agents by editing their shared policy, not their code.

The team has four roles:
- PM: breaks a user_task into component_tasks, routes each to L4 (normal) or L5 (hard), and runs a final e2e check.
- L4: implements a normal component_task and writes tests.
- L5: implements a HARD component_task by divide-and-conquer.
- L3: the tester/reviewer that verifies L4/L5 work and votes PASS/FAIL.

You are given a batch of recent traces, the recurring problems across the batch,
and the current policy. Propose concrete, GENERAL improvements that fix the BASE
behaviour — not one-off fixes for a single task.

Rules:
- Never reference signals the agents cannot see at run time (hidden tests, ground
  truth, the jury, pass/fail labels). Guidance must be actionable from what the
  agent can observe.
- Prefer refining/removing a stale bullet over endlessly stacking new ones.
- Only tighten or loosen L3 (the reviewer) when the evidence shows a real
  calibration error.

Reply with ONLY a JSON object with any of these optional fields:
{
  "diagnosis": "one sentence on the systemic issue",
  "pm_guidance_add": ["..."], "pm_guidance_remove": ["..."], "pm_guidance_replace": [{"old":"...","new":"..."}],
  "l4_guidance_add": ["..."], "l4_guidance_remove": ["..."], "l4_guidance_replace": [{"old":"...","new":"..."}],
  "l5_guidance_add": ["..."], "l5_guidance_remove": ["..."], "l5_guidance_replace": [{"old":"...","new":"..."}],
  "l3_guidance_add": ["..."], "l3_guidance_remove": ["..."], "l3_guidance_replace": [{"old":"...","new":"..."}],
  "new_skills": [{"name":"...","target":"l4|l5|l3|pm|implementer|all","when":"...","content":"markdown playbook"}]
}
"""


def _chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def _passed_signal(trace):
    """Best correctness signal for the gate: real tests, else a trusted jury."""
    passed, _src = signals.trace_oracle(trace)
    return passed


def _finalize(spec, loop_fn, grade, jury_fn=None):
    """Run one inner loop, grade it offline, fall back to a jury, return the trace."""
    trace = loop_fn(spec)
    diff = trace.get("final_diff", "")
    g = grade(spec["task_id"], diff)
    approved = bool(trace.get("approved"))
    if g.get("status") == "graded":
        gt = bool(g.get("resolved"))
        trace["labels"] = {
            "gt_pass": gt,
            "reward_hack": approved and not gt,
            "false_block": (not approved) and gt,
            "source": "tests",
        }
    elif jury_fn is not None:
        verdict = jury_fn(spec, trace)  # {"jury_pass": bool|None, "verified": bool}
        trace["jury_convened"] = True
        trace["jury_pass"] = verdict.get("jury_pass")
        trace["jury_verified"] = verdict.get("verified", False)
    return trace


def _gate_row(trace):
    return {"approved": bool(trace.get("approved")), "passed": bool(_passed_signal(trace))}


def _batch_evidence(traces, judge=None):
    """A compact, model-readable summary of a batch for the evolver prompt."""
    blocks = []
    for t in traces:
        passed, src = signals.trace_oracle(t)
        resp = signals.responsiveness(t)["score"]
        algn = signals.alignment(t, judge)["score"]
        blocks.append({
            "task": t.get("task", "")[:200],
            "worker": t.get("worker"),
            "rounds": len(t.get("rounds", [])),
            "approved": bool(t.get("approved")),
            "correct": passed,
            "correct_source": src,
            "calibration": signals.calibration(t),
            "responsiveness": resp,
            "alignment": algn,
            "last_comments": (t.get("rounds", [{}])[-1].get("comments", "") or "")[:300],
        })
    return blocks


def build_evolver_prompt(traces, judge=None, p=None):
    p = p or policy.load()
    evidence = _batch_evidence(traces, judge)
    patterns = signals.batch_patterns(traces, judge)
    payload = {
        "batch": evidence,
        "recurring_patterns": patterns,
        "current_policy": {r: p.get(r, {}).get("guidance", []) for r in policy.ROLES},
        "skills": [s.get("id") for s in p.get("skills", [])],
    }
    return json.dumps(payload, indent=2)


def process_batch(specs, *, loop_fn, grade, evolve_fn, jury_fn=None, judge=None,
                  do_gate=True, log=None):
    """Run + grade a batch, evolve once, gate the change. Returns a result dict."""
    # 1-2-3: run, grade, collect traces (under the CURRENT policy).
    traces = [_finalize(s, loop_fn, grade, jury_fn) for s in specs]
    old_rows = [_gate_row(t) for t in traces]

    anchor = any(
        (t.get("labels") and t["labels"].get("gt_pass") is not None)
        or (t.get("jury_convened") and t.get("jury_pass") is not None)
        for t in traces
    )

    # 4: ask the evolver for a proposal.
    prompt = build_evolver_prompt(traces, judge)
    proposal = evolve_fn(prompt) or {}

    # 5: apply (with the reviewer anchor gate), recording + snapshotting first.
    snapshot = copy.deepcopy(policy.load())
    p = policy.load()
    changes = gate.apply_proposal(p, proposal, allow_reviewer=anchor)
    version = policy.record(p, {"changes": changes,
                                "patterns": signals.batch_patterns(traces, judge),
                                "anchor": anchor})
    policy.save(p)

    result = {"version": version, "changes": changes, "anchor": anchor,
              "accepted": True, "old_total": gate.gate_total(old_rows),
              "new_total": gate.gate_total(old_rows)}

    # 6: acceptance gate — re-run the batch under the new policy, keep only if it
    # improves the total penalty score; otherwise roll back to the snapshot.
    if do_gate and _has_changes(changes):
        new_traces = [_finalize(s, loop_fn, grade, jury_fn) for s in specs]
        new_rows = [_gate_row(t) for t in new_traces]
        accepted, old_total, new_total = gate.accept_change(old_rows, new_rows)
        result.update(accepted=accepted, old_total=old_total, new_total=new_total)
        if not accepted:
            policy.save(snapshot)  # roll back
    if log is not None:
        log(result)
    return result


def run(specs, *, loop_fn, grade, evolve_fn, jury_fn=None, judge=None,
        epochs=TRAIN_DEFAULT_EPOCHS, batch_size=TRAIN_DEFAULT_BATCH_SIZE,
        do_gate=True, checkpoint_every=TRAIN_DEFAULT_CHECKPOINT_EVERY, log=None):
    """Drive the full outer loop. Returns a list of per-batch results."""
    results = []
    for epoch in range(1, epochs + 1):
        for bi, batch in enumerate(_chunks(specs, batch_size)):
            res = process_batch(batch, loop_fn=loop_fn, grade=grade,
                                 evolve_fn=evolve_fn, jury_fn=jury_fn, judge=judge,
                                 do_gate=do_gate, log=log)
            res["epoch"] = epoch
            results.append(res)
            if checkpoint_every == "batch":
                ids = "-".join(str(s["task_id"]) for s in batch)[:60]
                policy.checkpoint(f"e{epoch}_b{bi}_{ids}",
                                  {"accepted": res["accepted"], "version": res["version"]})
        if checkpoint_every == "epoch":
            policy.checkpoint(f"epoch{epoch}", {"batches": len(results)})
    return results


def _has_changes(changes):
    return bool(changes.get("guidance") or changes.get("skills_added"))


# --------------------------------------------------------------------------- #
# Default evolver LLM (wired, not unit-tested — needs an API key/model).       #
# --------------------------------------------------------------------------- #
def make_evolve_fn(api_key, model):
    """Return evolve_fn(prompt) -> proposal dict using the configured LLM API."""
    from langbridge_cli.llm.client import create_model_response

    def evolve_fn(prompt):
        data = create_model_response(
            api_key,
            model,
            [
                {"role": "system", "content": EVOLVER_SYSTEM},
                {"role": "user", "content": prompt},
            ],
            label="evolver",
        )
        text = _extract_text(data)
        return _parse_json(text)

    return evolve_fn


def _extract_text(data):
    parts = []
    for item in data.get("output", []):
        if item.get("type") == "message":
            for c in item.get("content", []):
                if c.get("type") in ("output_text", "text"):
                    parts.append(c.get("text", ""))
    return "".join(parts)


def _parse_json(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return {}
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return {}
