<img src="assets/Langbridge_Logotype_Horizontal.svg" alt="langbridge-cli" width="360">

An interactive coding-agent CLI backed by a Codex model.

LangBridge runs a PM-led, multi-agent coding loop. The PM inspects the
workspace, plans the work, and delegates implementation to specialist agents
(an L4 feature engineer and an L5 senior engineer), each verified by an L3 test
engineer. It can resume previous JSON session history and compacts older context
when the conversation gets long.

Start it (the Textual UI launches by default):

```bash
uv run langbridge
LANGBRIDGE_TERMINAL=1 uv run langbridge # plain terminal REPL
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

### Outer-loop flavors: REPL, Ralph, and agentic

An "outer loop" is the loop that decides what the next task is. The key
question is: **who picks the next input each round?** There are three flavors.

- **Human-driven (REPL):** a person types the next message each round. This is
  the LangBridge CLI today — the `while True` prompt loop in `main.py` waits for
  you. The agent does not choose what comes next; you do.
- **Dumb agent-driven (vanilla Ralph loop):** a fixed script re-feeds the *same*
  prompt every round, with no human and no decision in the loop itself.
- **Agentic:** an LLM reads the current state and decides the next task each
  round. The outer loop itself becomes "smart."

### How a vanilla Ralph loop actually works

The classic Ralph loop is famously dumb — roughly:

```bash
while true; do
  cat prompt.md | agent
done
```

The trick is that progress does **not** live in the prompt; it lives in files.

- The **prompt stays the same** every round. It never names a task. It says
  something like: *"Read the plan and the code, do the next unfinished task,
  then update the plan."*
- The **files on disk change** every round. The plan file and the code are the
  real memory.
- Each round is usually a **fresh agent with an empty context window**. It
  "remembers" only by re-reading the files. This sidesteps context limits and
  keeps the agent sharp on long jobs.

So the loop makes progress because the notebook (files) grows, not because the
instruction changes. On round 1 there is no plan, so the agent writes one; on
later rounds the plan exists, so the agent continues it. The branching comes
from the state of the files, not the prompt.

### Stop signals: a dumb loop cannot read prose

A shell loop cannot understand "I'm finished" written in English. It needs a
**machine-checkable signal** to know when to quit — for example a sentinel
string, an exit code, a marker file, an empty to-do list, or passing tests.
Pure Ralph often skips this entirely and just runs until a human stops it.

### How LangBridge differs from vanilla Ralph

LangBridge is an engineered, multi-agent take on the same idea:

- **A dedicated planner, not "whoever runs first."** The PM owns planning and
  routes scoped work to L4 or L5, instead of one generic agent bootstrapping the
  plan. (The L5 sub-task loop is itself a Ralph loop driven by a plan file.)
- **Checkable status tokens instead of free text.** Reports start with fixed
  lines like `L4_STATUS: READY_FOR_REVIEW` / `L5_STATUS: READY_FOR_REVIEW` and
  the runtime emits `PM_REVIEW_STATUS: OK` / `NEEDS_WORK`, so a loop can act on
  them deterministically.
- **Bounded loops.** `MAX_AGENT_STEPS`, `MAX_SPECIALIST_AGENT_STEPS`,
  `MAX_L4_L3_TURNS`, and `MAX_L5_RALPH_TURNS` stop the loops from spinning
  forever — the guard a bare `while true` lacks.

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

The PM leads a multi-agent loop with machine-checkable status tokens, an in-loop
L3 review, a neutral dispute jury, and clear escalation paths. The original
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
  next unfinished sub-task. See
  [Ralph loops](#outer-loop-flavors-repl-ralph-and-agentic).
- **L3:** the tester, shared inside both the L4 and L5 review loops.

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
- **PM state** — session history (`agent-state/pm/session-history/`), the
  `todo_list` (`agent-state/pm/todo_list.md`), and L5 component plans
  (`agent-state/l5/component-plans/`).

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

**Layout**:

- **Welcome banner** (top): directory, current session, model, and version.
- **Conversation**: your message is marked `✦` and the assistant reply `●`. While
  the agent works, its current thinking shows on a single live line that updates
  in place and clears when the reply arrives.
- **Status bar** (bottom): `model · state · cwd · git branch` on the left, and a
  **context-usage meter** `context X% (used/max)` on the right. The state shows
  `ready`, `thinking`, `working`, `paused`, `waiting for approval`, or `stopping`.

**Input box** (multi-line):

- **Enter** sends the message; **Shift+Enter** inserts a newline.
- Pasting keeps every line, so you can drop in a multi-paragraph task spec and it
  is sent as one message (the old single-line box truncated paste to the first line).

**Commands** (type in the prompt):

| Command | Action |
| --- | --- |
| `/help` | show all commands |
| `/new` | start a new session |
| `/sessions` | list saved sessions (numbered) |
| `/resume <n>` | resume session number `<n>` |
| `/delete <n>` | delete session number `<n>` |
| `/approve [on\|off]` | approve a pending action, or toggle auto-approve |
| `/deny` | deny a pending action |
| `/pause` | pause / resume the running agent |
| `/stop` | stop the current turn |
| `/exit` | quit |

**Keys**: `Ctrl+A` approve · `Ctrl+D` deny · `Ctrl+P` pause · `Ctrl+S` stop ·
`Ctrl+C` quit.

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

Inside the CLI, type `/exit` to quit. The plain REPL has no pause button; use
**Ctrl+C** to interrupt.

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
