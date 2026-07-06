# LangBridge Code

<img src="assets/Langbridge_Logotype_Horizontal.svg" alt="LangBridge Code" width="360">

A self-evolving coding agent with a **flat workflow** (router → planner → todo →
coder↔reviewer). **Default model: Moonshot Kimi** (`kimi-k2.7-code`); **also
supports OpenAI** (e.g. `gpt-5.1-codex`). Configure in `~/.langbridge-code/config.json`
(legacy `~/.langbridge/` still works) or via env vars — see [Models & providers](#models--providers).

LangBridge Code runs a **flat workflow** pipeline: route chat vs task, plan when needed,
then execute todo items through a Coder↔Reviewer loop (or Presenter for slides).
It can resume previous session history and compacts older context when the
conversation gets long.

Start it:

```bash
uv run langbridge-code
```

## Evolve (self-play training)

LangBridge Code is **self-evolving**: an outer **evolver** improves the team over many
tasks without editing Python source — by updating a shared **policy** (per-role
guidance bullets and evolver-written skills) that each agent folds into its
prompt on the next run. Code lives in `src/langbridge_code/training/`.

Two nested loops:

- **Worker loop** (one task): Coder implements and Reviewer verifies until pass or limits.
- **Optimizer loop** (the evolver / optimizer): across a batch of tasks, mine signals
  from traces, propose policy changes, and **gate** them — keep a change only if
  eval metrics improve and it does not reward-hack the reviewer.

**Today the evolver optimizes Coder and Reviewer only.** `train` reads **coder↔reviewer**
optimizer traces (`agent-state/workflow/optimizer-traces/*.jsonl`), grades with hidden
tests, and updates `coder` / `reviewer` guidance (legacy policy keys `l4` / `l3` are
mirrored for compatibility). Full workflow (`eval --role pm` / `workflow`) trace
mining is still evolving.

Per-role **eval** (hidden **FAIL_TO_PASS / PASS_TO_PASS** tests, **langbridge-bench**
specs in `evals/langbridge-bench/specs/`):

```bash
# L4 implementer only
uv run python -m langbridge_code.training.cli eval --role l4 --limit 5

# L3 reviewer (gold + no-fix cases per task, test-based labels)
uv run python -m langbridge_code.training.cli eval --role l3 --limit 5

# Full L4 ⇄ L3 inner loop (same trace shape train uses today)
uv run python -m langbridge_code.training.cli eval --role loop --limit 5

# Evolver epoch (L4/L3 policy only for now)
uv run python -m langbridge_code.training.cli train --epochs 1 --batch-size 2
```

For a local git repo + custom specs, set `LANGBRIDGE_TARGET_REPO` and use
`--source local`. Full design, guards, and env vars:
`src/langbridge_code/training/README.md`.

## Loop Engineering

LangBridge Code is built around **loop engineering**: instead of a single one-shot
model call, agents run in loops until a task is done.

**One user turn** runs the full workflow to completion:

```
User prompt
  → Router (chat reply OR task)
  → Planner (hard tasks) OR single todo item (easy)
  → Outer loop over todo_list
       [coding]      → Coder ↔ Reviewer (separate sessions, git diff handoff)
       [presentation] → Presenter (.pptx)
       on failure    → Planner refines (splits task)
  → Summary reply
```

Safety brakes: `max_workflow_seconds`, `max_coder_reviewer_rounds`, specialist
step caps, and context compaction.
## LangBridge Code team (workflow roles)

- **Router** — classifies chat vs task (one-shot JSON).
- **Planner** — breaks hard work into a markdown `todo_list`.
- **Coder** — implements a coding todo item; ends with `CODER_STATUS: READY_FOR_REVIEW`.
- **Reviewer** — inspects git diff + coder summary; `REVIEW_VERDICT: PASS|NEEDS_WORK|FAIL`.
- **Presenter** — builds `.pptx` deliverables; `PRESENTER_STATUS: COMPLETE|IN_PROGRESS`.

Legacy names (L4/L3/L5/PM) remain as aliases in policy and training for now.

## How it works

The **Router** handles chat or kicks off a task. The **Planner** maintains the
`todo_list`. For each item, **Coder** and **Reviewer** run in separate sessions
(git diff handoff, no shared worklog). **Presenter** handles slide tasks.

**Planner tools:** `list_dir`, `glob`, `read_file`, `grep`, `update_plan`

**Coder / Reviewer / Presenter tools:** filesystem tools, `bash`, `read_webpage`,
`read_skill`, plus writes (`create_file`, `edit_file`, `delete_file`) for specialists.

File tools are limited to the directory where you start LangBridge Code. Write tools
ask for approval first (unless auto-approve is on).

On-demand skills: specialists see a catalog of playbooks in their prompt and can
call `read_skill(name)` to load one. Bundled skills include Karpathy guidelines
and vendored [Superpowers](https://github.com/obra/superpowers) (`test-driven-development`,
`verification-before-completion`, etc.) under `src/langbridge_code/skills/superpowers/`.
Re-vendor with `scripts/vendor_superpowers.sh`.

Each tool call includes a required `purpose` field: a short, user-visible sentence
explaining why the agent is calling that tool. It feeds the live thinking line in the TUI.

Each run writes readable JSON history under `agent-state/pm/session-history/`. On
startup, you can resume a previous session or start a new one.

### Living agents vs. worklogs (memory)

Within one specialist session an agent stays **alive** across tool steps. Coder and
Reviewer are **fresh sessions** each handoff — they do not share message history.

Worklogs are an audit/debug trail on disk, **not** the agents' working memory:

- **Per-instance worklog** — `agent-state/<role>/worklog/<run>/<role>_<n>.md`
- **Optimizer trace** — `*.optimizer_trace.jsonl` next to each session
- **Session state** — `agent-state/pm/session-history/`, per-session `*.todo_list.md`

### Status tokens (machine-checkable)

- **Coder:** `CODER_STATUS: READY_FOR_REVIEW | IN_PROGRESS`
- **Reviewer:** `REVIEW_VERDICT: PASS | NEEDS_WORK | FAIL`
- **Presenter:** `PRESENTER_STATUS: COMPLETE | IN_PROGRESS`

### Limits

Bounded by `max_workflow_seconds`, `max_coder_reviewer_rounds`, specialist step caps,
and context compaction. On failure the Planner can split the failed todo into smaller tasks.

## Eval (benchmarks & datasets)

The `evals/` tree measures LangBridge Code on real issues and builds new task data.

### SWE-bench e2e (`evals/swe-bench/`)

End-to-end benchmark on published SWE-bench instances: checkout the repo at
`base_commit`, run headless LangBridge Code on the issue text, capture `git diff` as the
patch, then grade with the official harness (hidden tests in Docker).

```bash
# Stage 1 — generate predictions (agent inside the official SWE-bench image)
sg docker -c "uv run python evals/swe-bench/run_eval_docker.py --difficulty lite --count 10"

# Stage 2 — grade (from evals/swe-bench/)
cd evals/swe-bench && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 --run_id langbridge-l4-lite
```

Datasets: `lite` (~300), `verified` (500), `pro` (hard). Details and Pro caveats:
`evals/swe-bench/README.md`.

### langbridge-bench (`evals/langbridge-bench/`)

Self-built benchmark from GitHub PRs: collect merged PRs, validate with reference
tests, then materialize **one JSON per task** under `instances/` and `specs/`.

```bash
uv run python evals/langbridge-bench/collect_prs.py --repo pytest-dev/pytest --max-per-repo 5
uv run python evals/langbridge-bench/reference_test.py --run
uv run python evals/langbridge-bench/materialize.py
```

Training eval/train reads `evals/langbridge-bench/specs/` by default. See
`evals/langbridge-bench/README.md` and `evals/README.md`.

## Run

### Models & providers

LangBridge Code is **not tied to a single vendor**. Package defaults in
`src/langbridge_code/config.json` use **Moonshot Kimi**; you can switch to **OpenAI**
(or point Moonshot at a compatible base URL) without changing agent code.

| Provider (`api.provider`) | Default model | API used | API key (env or `api_keys.*`) |
| --- | --- | --- | --- |
| `moonshot` (default) | `kimi-k2.7-code` | Chat completions (`/v1/chat/completions`) | `MOONSHOT_API_KEY`, `KIMI_API_KEY`, `api_keys.moonshot` |
| `openai` | set in config (e.g. `gpt-5.1-codex`) | OpenAI **Responses** API | `OPENAI_API_KEY`, `api_keys.openai` |

Switch provider:

```bash
# one-off
LANGBRIDGE_API_PROVIDER=openai LANGBRIDGE_MODEL=gpt-5.1-codex uv run langbridge-code

# or persist in ~/.langbridge-code/config.json
```

```json
{
  "model": "gpt-5.1-codex",
  "api": { "provider": "openai", "base_url": "" }
}
```

Back to Kimi (defaults):

```json
{
  "model": "kimi-k2.7-code",
  "api": { "provider": "moonshot", "base_url": "https://api.moonshot.ai/v1" }
}
```

`LANGBRIDGE_MODEL` overrides `model` for any provider. `api.base_url` is optional
(custom OpenAI-compatible endpoint for Moonshot or a proxy).

### API keys

On first run, `langbridge-code` asks for an API key for the **active** provider and
saves it to `~/.langbridge-code/config.json` under `api_keys.<provider>`. Kimi and
OpenAI keys can live side by side:

```json
{
  "api_keys": {
    "moonshot": "sk-...",
    "openai": "sk-..."
  }
}
```

Environment overrides: `MOONSHOT_API_KEY` / `KIMI_API_KEY` (Kimi),
`OPENAI_API_KEY` (OpenAI), `LANGBRIDGE_API_PROVIDER`, `LANGBRIDGE_MODEL`.

Copy any section from `src/langbridge_code/config.json` into
`~/.langbridge-code/config.json` to override limits, paths, or tool budgets.
### Textual UI (default)

The Textual UI launches by default — a clean, command-driven layout (no button
clutter): a welcome banner, a flowing conversation, a multi-line prompt, and a
status bar.

```bash
uv run langbridge-code
```

<img src="assets/tui-screenshot.png" alt="Textual UI" width="720">

While developing locally, prefer `uv run langbridge-code` (editable install) so code
changes take effect immediately. Use `uv sync --reinstall-package langbridge-code
--no-editable` only when you need a non-editable install.

**Commands** (type in the prompt):

| Command | Action |
| --- | --- |
| `/help` | show all commands |
| `/new` | start a new session |
| `/sessions` | open the session picker (scrollable popup, also `Ctrl+R`) |
| `/resume [n]` | open the picker, or resume session number `<n>` |
| `/delete <n>` | delete session number `<n>` |
| `/approve [on\|off]` | approve a pending action, or toggle auto-approve |
| `/deny` | deny a pending action |
| `/pause` | pause / resume the running agent |
| `/stop` | stop the current turn |
| `/exit` | quit |

**Keys**: `Ctrl+A` approve · `Ctrl+D` deny · `Ctrl+P` pause · `Ctrl+S` stop ·
`Ctrl+R` sessions · `Ctrl+C` quit.

**Sessions**: `Ctrl+R` (or `/sessions`) opens a scrollable popup of saved
sessions — move with `↑`/`↓`, `Enter` to resume, `Esc` to cancel.

**Pause** (soft hold): holds the agent at the next step boundary and resumes the
same run in place. It takes effect *between* steps, so an in-flight model call or
tool finishes first; it also works during planner/coder/reviewer steps.

**Stop** (hard abort): aborts the current turn and hands control back, like
Cursor's stop. It cancels the in-flight model request (abandoned in the
background) instead of waiting for it, so control returns almost immediately. The
half-finished turn is discarded so the conversation history stays valid. If a
tool (e.g. `bash`) is mid-execution, Stop waits for that one tool to return
before unwinding — it never leaves a write half-applied.

**Approvals**: when auto-approve is off, the agent posts an inline approval
request for specialist write tools (`create_file`, `edit_file`, `delete_file`,
`bash`). Approve with `Ctrl+A` / `/approve` or deny with `Ctrl+D` / `/deny`.

### One-shot (headless)

Run the agent on a single task without the interactive prompt. It reads the task
from the first argument (or stdin), auto-approves write tools, and exits when the
loop finishes. This is the path the SWE-bench eval drives.

```bash
uv run python -m langbridge_code.headless "fix the failing test in foo/bar.py"
```

Or pipe the task in on stdin:

```bash
echo "add a --verbose flag" | uv run python -m langbridge_code.headless
```

### Debug

Print compact model output lines to stderr (one line per model response):

```bash
LANGBRIDGE_DEBUG_LLM=1 uv run --no-editable langbridge-code
```

Optional line length cap (default `200`):

```bash
LANGBRIDGE_DEBUG_LLM=1 LANGBRIDGE_DEBUG_LLM_MAX_CHARS=500 uv run --no-editable langbridge-code
```
