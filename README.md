<img src="assets/Langbridge_Logotype_Horizontal.svg" alt="langbridge-cli" width="360">

A self-evolving, multi-agent coding CLI backed by a Codex model.

LangBridge runs a PM-led, multi-agent coding loop. The PM inspects the
workspace, plans the work, and delegates implementation to specialist agents
(an L4 feature engineer and an L5 senior engineer), each verified by an L3 test
engineer. It can resume previous session history and compacts older context
when the conversation gets long.

Start it:

```bash
uv run langbridge 
```

## Loop Engineering

LangBridge is built around **loop engineering**: instead of a single one-shot
model call, agents run in loops, and loops are nested inside loops. Each agent
keeps thinking, calling tools, and reading results until it decides its job is
done.

There are three nested loops:

- **Outer loop (PM):** The PM runs its own agentic loop. On each step it can
  inspect the workspace or delegate work, read the result, and decide the next
  move.
- **Inner loop (L4 ⇄ L3):** When the PM delegates a normal task, that single
  delegation is itself a full agentic loop. The L4 engineer reads files, edits
  code, runs tests, fixes failures, and re-runs — many turns — then L3 verifies
  it, and the two trade review turns until the work passes.
- **Nested Ralph loop (L5 ⇄ L3):** For a HARD task the PM delegates to the L5
  senior engineer, which splits the work into technical sub-tasks and conquers
  them one at a time, each verified by L3 — a loop of loops.

So one PM action can trigger an entire L4 or L5 run. An "agent tool" is a loop,
not a single call.

Every loop has safety brakes and quality controls:

- **Step caps:** the PM loop is bounded by `MAX_AGENT_STEPS` / `MAX_PM_LOOPS`;
  one specialist turn by `MAX_SPECIALIST_AGENT_STEPS`; a review by
  `MAX_L4_L3_TURNS`; the L5 Ralph loop by `MAX_L5_RALPH_TURNS`. None can spin
  forever (wall-clock and context caps back these up).
- **Verification gate:** when L4 or L5 reports `READY_FOR_REVIEW`, the runtime
  deterministically runs the L3 test engineer to verify the work before the PM
  accepts it.
- **Recovery path:** if L3 returns `NEEDS_WORK`, that feedback goes back to the
  same (still-alive) L4/L5 so it can address it.

```
PM agentic loop                          (caps: MAX_AGENT_STEPS, MAX_PM_LOOPS)
  ├─ ask_l4_engineer ─► L4 ⇄ L3 review loop      (cap: MAX_L4_L3_TURNS)
  │                       └─ NEEDS_WORK ─► back to L4;  push-back ─► 2-juror jury
  └─ ask_l5_engineer ─► L5 Ralph loop            (cap: MAX_L5_RALPH_TURNS)
                          └─ per sub-task: L5 ⇄ L3 review  (same jury rules)
```
## LangBridge Coding Team

LangBridge is organized as a small coding-agent team. The current team has four
active roles:

- **PM (outer loop)**: turns user needs into a `todo_list` of component-level
  subtasks, routes each to L4 or L5, verifies the delivery, and tracks progress.
- **L4 feature engineer**: implements a normal `component_task` and its focused
  unit tests.
- **L5 senior engineer**: takes a HARD `component_task`, plans it into
  technical sub-tasks, and builds them one at a time (a Ralph loop).
- **L3 test engineer**: verifies L4/L5 work — reviews code and test quality and
  runs the tests. Shared inside both the L4 and L5 loops.

We are hiring more agent roles. Current openings:
- **Designer**: UI design and front-end specs.
- **PM (cross-functional)**: collaboration with design, data science, product,
  and marketing.
- **L6 engineer**: large-scale, high-concurrency system design and cross-team
  collaboration with other coding-agent teams.
- **Manager**: keeps agents aligned, unblocks work, and improves team execution.

## How the team works

The PM leads a multi-agent loop with machine-checkable status tokens. The original
design notes are in `Thoughts.md`.

### Roles and loops

- **PM (outer loop):** breaks the `user_task` into a `todo_list` of
  `component_task`s (product-level, not deeply technical), routes each to L4 or
  L5, verifies the delivery, and marks progress. The **last `component_task` is
  always an e2e test** for the whole product.
- **L4:** implements a normal `component_task` and its tests.
- **L5 (Ralph loop):** implements a HARD `component_task` by divide-and-conquer.
  It writes a `component_task_plan` (one file per component) that splits the work
  into `technical_sub_task`s; the **last one is always an integration test**.
  Each Ralph turn spawns a fresh L5 that reads the plan and continues from the
  next unfinished sub-task. 
- **L3:** the tester, shared inside both the L4 and L5 review loops.


### How LangBridge works

LangBridge is an engineered, multi-agent:

The PM works read-only on the workspace and delegates all writes to specialists.
PM tools:

- `list_dir`, `find_files`, `read_file`, `search_files`: inspect the workspace
- `execute_program`: run a non-interactive program (e.g. bring the app up)
- `read_webpage`: fetch the text of a URL (docs, an issue, reference material)
- `update_plan`: write or update the `todo_list`
- `ask_l4_engineer`: delegate a normal `component_task` to the L4 engineer
- `ask_l5_engineer`: delegate a HARD `component_task` to the L5 senior engineer

Specialists get the write and test tools. L4 and L5 share `edit_file`,
`create_file`, `delete_file`, `run_tests`, `execute_program`, and `read_skill`
on top of the read-only file tools; L3 gets the read-only file tools plus
`run_tests`. Both delegations trigger PM-driven L3 review when the work is ready.

File tools are limited to the directory where you start the CLI. The write tools
(`create_file`, `edit_file`, `delete_file`, `install_python_packages`) and the
`ask_l4_engineer` / `ask_l5_engineer` delegations ask for approval first.

On-demand skills: L4 and L5 see a catalog of skills (short playbooks) in their
prompt and can call `read_skill(name)` to load one before starting. The bundled
`karpathy` skill captures the team's engineering discipline.

Each tool call includes a required `purpose` field: a short, user-visible
sentence explaining why the agent is calling that tool. It is not private
chain-of-thought; it feeds the live thinking line in the TUI.

The prompt uses `prompt_toolkit`, so deletion, cursor movement, and command
history work like a normal interactive shell.

Each CLI run writes readable JSON history under `agent-state/pm/session-history/`. On startup,
you can resume a previous session or start a new one.

### Living agents vs. worklogs (memory)

Within one loop an agent stays **alive**: an L4 (or L5, or L3) keeps its full
message history across the review rounds, so it remembers its own tool calls and
the prior exchange. A new loop spawns a **fresh** agent with no memory of the
previous one, and jurors are always fresh.

Worklogs are an audit/debug trail on disk, **not** the agents' working memory:

- **Per-instance worklog** — `agent-state/<role>/worklog/<run>/<role>_<n>.md`:
  each agent instance's own record of what it received, the tools it called, what
  came back, and its final report. A review can spin up several L3s (the reviewer
  plus fresh jurors) and each PM round is a fresh PM, so every instance gets its
  own file.
- **Shared negotiation ledger** — `agent-state/l4/worklog/<run>/l34_share_<n>.md`
  (and `l5/.../l45_share_<n>.md`): the L4↔L3 (or L5↔L3) conversation. Each turn
  ends with a `WORKLOG_TOKEN`, which is what the loop routes on.
- **Chat, task, component_task state** — session history (`agent-state/pm/session-history/`), the
  per-session `todo_list` (`agent-state/pm/session-history/<run>.todo_list.md`, so a new
  session starts fresh), and L5 component plans (`agent-state/l5/component-plans/`).

### Status tokens (machine-checkable, not prose)

Reports start with a fixed status line so a loop can act on them deterministically:

- **L4 / L5:** `L4_STATUS:` / `L5_STATUS:` — one of `READY_FOR_REVIEW`,
  `IN_PROGRESS`, `BLOCKED`, `PUSH_BACK`.
- **L3:** `REVIEW_VERDICT:` — one of `PASS`, `FAIL`, `NEEDS_WORK`.
- The runtime appends `PM_REVIEW_STATUS: OK | NEEDS_WORK` to a delivery, and the
  PM ends each round with `BUG_STATUS: OPEN | NONE`, which drives the outer loop.

The shared ledger tracks the negotiation with its own `WORKLOG_TOKEN`s: `ready`,
`concern exist`, `push back`, `pass`, `needs pm` (escalate to PM), and `failure`.

### Disputes: a neutral jury, not a self-judge

When the worker posts `push back` and L3 still objects, L3 does **not** decide
alone — that would be judging a complaint about its own test. Instead a **jury of
2 fresh, independent testers** each verifies the implementation and votes:

- **Both PASS** → `pass` (deliver, or mark the sub-task done).
- **Otherwise** → `failure`.

### Limits, escalation, and recovery

- **Bounded everywhere:** each loop has a step cap, a wall-clock timeout, and
  (for LLM loops) a context cap — `MAX_AGENT_STEPS` / `MAX_PM_LOOPS` for the PM,
  `MAX_SPECIALIST_AGENT_STEPS` for one specialist turn, `MAX_L4_L3_TURNS` for a
  review, and `MAX_L5_RALPH_TURNS` for L5. Whichever trips first ends the loop.
- **Escalation:** an L4 or L5 failure returns to the PM (retry, re-scope, or
  reassign). When the PM exhausts its own limits, it reports a clear blocker to
  the user.
- **Final check:** after all `component_task`s pass, if the project is runnable
  the PM brings it up and debugs by hand. A bug found this way becomes a **new
  `component_task`**; a clean run ships to the user.

## Evolve (self-play training)

The **evolve** subsystem lives in `src/langbridge_cli/training/`. It improves the
team over many tasks without editing Python source — by updating a shared
**policy** (per-role guidance bullets and evolver-written skills) that each agent
folds into its prompt on the next run.

Two nested loops:

- **Inner loop** (the CLI above): for one task, L4 or L5 implements and L3
  reviews until the work passes or limits trip.
- **Outer loop** (the evolver): across a batch of tasks, mine signals from
  traces, propose policy changes, and **gate** them — keep a change only if eval
  metrics improve and it does not reward-hack the reviewer.

Eval types cover all roles: `l4`, `l5`, `l3` (reviewer), `pm`, and the full
`loop`. Grading uses hidden **FAIL_TO_PASS / PASS_TO_PASS** tests (same idea as
SWE-bench). The **L3 reviewer eval** expands each task into two cases — the gold
fix (should pass) and no fix (should fail) — labels them with the test grader,
then asks L3 alone to approve or reject each patch.

Quick start (default task source is the validated dataset in `evals/dataset/`):

```bash
# L4 implementer only
uv run python -m langbridge_cli.training.cli eval --role l4 --limit 5

# L3 reviewer only (gold + no-fix cases per task, test-based labels)
uv run python -m langbridge_cli.training.cli eval --role l3 --limit 5

# Full L4 ⇄ L3 inner loop
uv run python -m langbridge_cli.training.cli eval --role loop --limit 5

# Run one evolver epoch (self-play)
uv run python -m langbridge_cli.training.cli train --epochs 1 --batch-size 2
```

For a local git repo + custom specs, set `LANGBRIDGE_TARGET_REPO` and use
`--source local`. Full design, guards, and env vars:
`src/langbridge_cli/training/README.md`.

## Eval (benchmarks & datasets)

The `evals/` tree measures LangBridge on real issues and builds new task data.

### SWE-bench e2e (`evals/swebench/`)

End-to-end benchmark on published SWE-bench instances: checkout the repo at
`base_commit`, run the headless CLI on the issue text, capture `git diff` as the
patch, then grade with the official harness (hidden tests in Docker).

```bash
# Stage 1 — generate predictions (agent inside the official SWE-bench image)
sg docker -c "uv run python evals/swebench/run_eval_docker.py --difficulty lite --count 10"

# Stage 2 — grade (from evals/swebench/)
cd evals/swebench && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Lite \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 --run_id langbridge-l4-lite
```

Datasets: `lite` (~300), `verified` (500), `pro` (hard). Details and Pro caveats:
`evals/swebench/README.md`.

### Dataset pipeline (`evals/dataset/`)

Build **SWE-bench-style** training instances from GitHub: collect merged PRs that
link an issue and change both code and tests, run pre-fix / post-fix reference
tests, and keep only tasks with a **FAIL_TO_PASS** signal.

```bash
uv run python evals/dataset/collect_prs.py --repo pytest-dev/pytest --max-per-repo 5
uv run python evals/dataset/reference_test.py --run
```

A small validated sample ships in `evals/dataset/sample_validated.jsonl`. Pipeline
steps and scaling notes: `evals/dataset/README.md`.

## Run

On first run, `langbridge-cli` asks for your Codex API key and saves it to
`~/.langbridge/config.json`. You can still override it with `OPENAI_API_KEY`.
Use `LANGBRIDGE_MODEL` to override the default model.

### Textual UI (default)

The Textual UI launches by default — a clean, command-driven layout (no button
clutter): a welcome banner, a flowing conversation, a multi-line prompt, and a
status bar.

```bash
uv run langbridge
```

<img src="assets/tui-screenshot.png" alt="Textual UI" width="720">

While developing locally, prefer `uv run langbridge` (editable install) so code
changes take effect immediately. Use `uv sync --reinstall-package langbridge-cli
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
tool finishes first; it also works while the PM is delegating to L4/L3.

**Stop** (hard abort): aborts the current turn and hands control back, like
Cursor's stop. It cancels the in-flight model request (abandoned in the
background) instead of waiting for it, so control returns almost immediately. The
half-finished turn is discarded so the conversation history stays valid. If a
tool (e.g. `run_tests`) is mid-execution, Stop waits for that one tool to return
before unwinding — it never leaves a write half-applied.

**Approvals**: when auto-approve is off, the agent posts an inline approval
request for PM delegate calls (`ask_l4_engineer`, `ask_l5_engineer`) and for
specialist write tools (`create_file`, `edit_file`, `delete_file`). Approve with
`Ctrl+A` / `/approve` or deny with `Ctrl+D` / `/deny`.

### Plain terminal CLI

Set `LANGBRIDGE_TERMINAL=1` to use the plain REPL instead of the Textual UI:

```bash
LANGBRIDGE_TERMINAL=1 uv run --no-editable langbridge
```

Override the default model:

```bash
LANGBRIDGE_TERMINAL=1 LANGBRIDGE_MODEL=gpt-5.1-codex uv run --no-editable langbridge
```

Install locally to get the `langbridge` command:

```bash
uv sync --no-editable
source .venv/bin/activate
LANGBRIDGE_TERMINAL=1 langbridge
```

The plain REPL runs the exact same agent loop as the Textual UI — one growing
conversation (compacted when long) plus the PM review loop — so the two behave
identically apart from the UI. At an approval prompt, answering `N` stops the
current turn and returns you to the prompt for the next message. Type `/exit` to
quit; the plain REPL has no pause button, so use **Ctrl+C** to interrupt.

### One-shot (headless)

Run the agent on a single task without the interactive prompt. It reads the task
from the first argument (or stdin), auto-approves write tools, and exits when the
loop finishes. This is the path the SWE-bench eval drives.

```bash
uv run python -m langbridge_cli.headless "fix the failing test in foo/bar.py"
```

Or pipe the task in on stdin:

```bash
echo "add a --verbose flag to the CLI" | uv run python -m langbridge_cli.headless
```

### Debug

Print compact PM/L4/L5/L3 output lines to stderr (one line per model response,
`message` and `function_call` only):

```bash
LANGBRIDGE_DEBUG_LLM=1 uv run --no-editable langbridge
```

Optional line length cap (default `200`):

```bash
LANGBRIDGE_DEBUG_LLM=1 LANGBRIDGE_DEBUG_LLM_MAX_CHARS=500 uv run --no-editable langbridge
```
