# langbridge-cli

An interactive coding-agent CLI backed by a Codex model.

LangBridge runs a PM-led coding-agent loop. The PM can inspect the workspace,
delegate implementation to specialist agents, resume previous JSON session
history, and compact older context when the conversation gets long.

## Loop Engineering

LangBridge is built around **loop engineering**: instead of a single one-shot
model call, agents run in loops, and loops are nested inside loops. Each agent
keeps thinking, calling tools, and reading results until it decides its job is
done.

There are two levels:

- **Outer loop (PM):** The PM runs its own agentic loop. On each step it can
  inspect the workspace or delegate work, read the result, and decide the next
  move.
- **Inner loop (L4 / L3):** When the PM delegates a task, that single delegation
  is itself a full agentic loop. The L4 engineer reads files, edits code, runs
  tests, fixes failures, and re-runs — many turns — before returning a report.
  The same is true for the L3 test engineer.

So one PM action can trigger an entire L4 or L3 run. An "agent tool" is a loop,
not a single call.

Both levels have safety brakes and quality controls:

- **Step caps:** the PM loop is bounded by `MAX_AGENT_STEPS`; the specialist
  loops are bounded by `MAX_SPECIALIST_AGENT_STEPS`. Neither can spin forever.
- **Verification gate:** when L4 reports `READY_FOR_REVIEW`, the runtime
  deterministically runs the L3 test engineer to verify the work before the PM
  accepts it.
- **Recovery path:** if L3 returns `NEEDS_WORK`, that feedback is routed back
  into a fresh L4 run so the engineer can address it.

```
PM agentic loop                       (cap: MAX_AGENT_STEPS)
  └─ ask_l4_engineer  ──►  L4 agentic loop   (cap: MAX_SPECIALIST_AGENT_STEPS)
  └─ deterministic    ──►  L3 agentic loop   (cap: MAX_SPECIALIST_AGENT_STEPS)
                              │
                              └─ NEEDS_WORK ──► feedback back to L4
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
  routes scoped work to L4, instead of one generic agent bootstrapping the plan.
- **Checkable status tokens instead of free text.** Reports start with fixed
  lines like `L4_STATUS: READY_FOR_REVIEW` and the runtime emits
  `PM_REVIEW_STATUS: OK` / `NEEDS_WORK`, so a loop can act on them deterministically.
- **Bounded loops.** `MAX_AGENT_STEPS` and `MAX_SPECIALIST_AGENT_STEPS` stop the
  loops from spinning forever — the guard a bare `while true` lacks.

The CLI can call local tools in the current workspace:

- `list_dir`: list files and directories
- `find_files`: find files and directories by name
- `read_file`: read UTF-8 text files
- `create_file`: create new UTF-8 text files
- `edit_file`: edit UTF-8 text files with exact string replacement
- `search_files`: search UTF-8 text files for exact text matches
- `run_tests`: run Python unit tests with a timeout
- `install_python_packages`: install Python packages with `uv add`
- `ask_l4_engineer`: delegate scoped implementation work to an L4 feature
  engineer, with PM-triggered L3 review when the work is ready

File tools are limited to the directory where you start the CLI. Write tools ask
for approval before changing files or packages.

Each tool call includes a required `purpose` field: a short, user-visible
sentence explaining why the agent is calling that tool. This is not private
chain-of-thought; it powers the live thought display in the CLI and TUI.

The prompt uses `prompt_toolkit`, so deletion, cursor movement, and command
history work like a normal interactive shell.

Each CLI run writes readable JSON history under `agent-state/pm/session-history/`. On startup,
you can resume a previous session or start a new one.

## LangBridge Coding Team

LangBridge is organized as a small coding-agent team. The current team has five
roles:

- **PM**: defines what the product should look like and turns user needs into a
  clear product brief.
- **L4 feature implementation engineer**: implements features and the matching
  unit tests.
- **L3 test engineer**: checks the tests implemented by L4 and L5, reviews their
  quality, and runs unit and end-to-end tests.

We are hiring more agent roles. Current openings:
- **Designer**: designs good UI interfaces and provides front-end specs.
- **TL / L5 senior engineer**: plans the technical approach from the PM's product
  definition, decides the components required, implements the framework or MVP,
  and adds end-to-end tests. See the [Roadmap](#l5-senior-engineer-an-agentic-ralph-layer-between-pm-and-l4)
  for the planned L5 design.
- **PM**: cross-functional collaboration with design, data science, product, and
  marketing.
- **L6 engineer**: large-scale and high-concurrency system design,
  implementation, and cross-team collaboration with other coding-agent teams.
- **Manager**: keeps agents aligned, unblocks work, and improves team execution.

## Roadmap

The next milestone turns LangBridge into a fuller PM-led team with an **L5
senior engineer**, an explicit turn state machine, a neutral dispute jury, and
clear escalation and recovery paths. The full design is captured in
`Thoughts.md`.

### Roles and loops

- **PM (outer loop):** breaks the `user_task` into a `todo_list` of
  `component_task`s (product-level, not deeply technical), routes each to L4 or
  L5, verifies the delivery, and marks progress. The **last `component_task` is
  always an e2e test** for the whole product.
- **L4:** implements a normal `component_task`.
- **L5 (Ralph loop):** implements a hard `component_task` by divide-and-conquer.
  It writes a `component_task_plan` (uniquely named per component) that splits
  the work into `technical_sub_task`s; the **last one is always an integration
  test**. Each Ralph turn re-runs a fresh L5 with the same prompt; the L5 reads
  the plan and continues from the next unfinished sub-task. See
  [Ralph loops](#outer-loop-flavors-repl-ralph-and-agentic).
- **L3:** the tester, shared inside both the L4 and L5 loops.

### Worklogs (the memory)

Each L4/L5 loop keeps a `shared_worklog` (the L4↔L3 or L5↔L3 conversation) plus
private `l4_worklog` / `l5_worklog` / `l3_worklog`. A worker reads its own log
plus the shared log; L3 reads its own log plus the shared log.

### Turn routing (a small state machine)

Within a loop, exactly one side is active each turn, decided by the last token
in `shared_worklog`:

- empty or `concern exist` → **worker's turn** (implement / fix)
- `ready` or `push back` → **L3's turn** (test / re-judge)

Three tokens keep the loop going (`ready`, `concern exist`, `push back`) and two
end it (`pass`, and a `failure` outcome).

### Disputes: a neutral jury, not a self-judge

When the worker posts `push back` and L3 thinks it is unreasonable, L3 does
**not** decide alone — that would be judging a complaint about its own test.
Instead a **jury of 2 fresh, independent testers** each writes its own tests and
votes:

- **Both vote yes** → `pass` (deliver, or mark the sub-task done).
- **Otherwise** → `failure`.

### Limits, escalation, and recovery

- **Three limits per loop:** context length (for LLM loops), a wall-clock
  timeout, and a max loop count. Whichever trips first ends the loop as a
  `failure`.
- **Escalation:** an L4 failure goes to the PM (retry or reassign to L5); an L5
  failure goes to the PM (re-scope or re-plan the `component_task`). When the PM
  exhausts its own limits, it reports a clear blocker to the user.
- **Final check:** after all `component_task`s pass, if the project is runnable
  the PM brings it up and debugs by hand. A bug found this way becomes a **new
  `component_task`**; a clean run ships to the user.

## Run

On first run, `langbridge-cli` asks for your Codex API key and saves it to
`~/.langbridge/config.json`. You can still override it with `OPENAI_API_KEY`.
Use `LANGBRIDGE_MODEL` to override the default model.

### Textual UI (recommended)

Use the Textual UI for the richest terminal experience. It is a clean,
command-driven layout (no button clutter): a welcome banner, a flowing
conversation, a multi-line prompt, and a status bar.

```bash
LANGBRIDGE_TUI=1 uv run langbridge
```

While developing locally, prefer `uv run langbridge` (editable install) so code
changes take effect immediately. Use `uv sync --reinstall-package langbridge-cli
--no-editable` only when you need a non-editable install.

**Layout**:

- **Welcome banner** (top): directory, current session, model, and version.
- **Conversation**: your message is marked `✦`, the assistant reply `●`, and the
  agent's live thoughts / tool actions appear inline in dim text.
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
request for PM delegate calls (`ask_l4_engineer`) and L4 write tools
(`edit_file`, `create_file`, `delete_file`). Approve with `Ctrl+A` / `/approve`
or deny with `Ctrl+D` / `/deny`.

### Plain CLI

```bash
uv run --no-editable langbridge
```

Override the default model:

```bash
LANGBRIDGE_MODEL=gpt-5.1-codex uv run --no-editable langbridge
```

Install locally to get the `langbridge` command:

```bash
uv sync --no-editable
source .venv/bin/activate
langbridge
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

Print compact PM/L4/L3 output lines to stderr (one line per model response,
`message` and `function_call` only):

```bash
LANGBRIDGE_DEBUG_LLM=1 uv run --no-editable langbridge
```

Optional line length cap (default `200`):

```bash
LANGBRIDGE_DEBUG_LLM=1 LANGBRIDGE_DEBUG_LLM_MAX_CHARS=500 uv run --no-editable langbridge
```
