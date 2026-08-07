
<img src="assets/Langbridge_Logotype_Horizontal.svg" alt="LangBridge Code" width="360">

A self-evolving coding agent with a **main agent + specialist subagents** workflow.
**Default model: Moonshot Kimi** (`kimi-k2.7-code`); **also supports OpenAI**
(`gpt-5.6`) and **DeepSeek** (`deepseek-v4-pro`). Configure in
`~/.langbridge-code/config.json` or via environment variables — see
[Models & providers](#models--providers).

LangBridge Code runs a **flat orchestration pipeline**: the **LangBridge** main agent
decides when to chat vs delegate, calls **Planner** to build a markdown `todo_list`,
then dispatches each unblocked subtask to an isolated **Worker↔Reviewer** loop.
Independent subtasks may run in parallel. It compacts
long context automatically and can resume prior sessions and interrupted subtasks.

Start it (requires [`uv`](https://docs.astral.sh/uv/)):

```bash
uv tool install git+https://github.com/chen-zhuofu/langbridge-code.git
langbridge-code
```

LangBridge also prepares a managed tool runtime under
`<workspace>/.langbridge/runtime/`. Missing Node.js/npm, `rg`, Git, and Bash are
installed into a repo-local micromamba prefix; pytest is provided by a local
test venv.
The directory is added to the
repository's local git exclude file and must not be committed. There is no
reduced-functionality fallback: if the runtime cannot be downloaded or
validated (for example, the machine is offline or the workspace is read-only),
LangBridge exits before starting an agent. Set `LANGBRIDGE_RUNTIME_DIR` to
override the runtime location.

## Eval (langbridge-bench)

Public e2e runs the full main agent in Docker — one container per task
(agent + in-container grade) over specs in `eval/data/langbridge-bench/specs/`:

```bash
uv run python eval/run_eval.py --workers 4 --limit 5
```

Outputs land under `artifacts/evals/<run_id>/`. Dataset pipeline: `data/README.md`.
Eval docs: `eval/README.md`.

## Loop Engineering

LangBridge Code is built around **loop engineering**: instead of a single one-shot
model call, agents run in loops until a task is done.

**One user turn** can drive the workflow through multiple delegated tasks until
completion or a configured stop condition:

```
User prompt
  → LangBridge (chat reply OR delegate)
  → agent_planner (draft plan) when needed → LangBridge writes todo_list.md
  → agent_worker (one unchecked subtask contract per call)
       → Worker ↔ Reviewer (separate sessions, git diff handoff)
       on pass   → LangBridge merges the branch and marks the todo [x]
       on stop   → turn ends; a later turn can re-dispatch the same task_name
       on block  → LangBridge resolves the contract or splits the todo
  → Summary reply (full project complete only when all todos are [x])
```

Safety brakes: `max_workflow_seconds`, worker/reviewer step caps, context compaction,
and optional `/goal` autonomous rounds with a Goal Evaluator.

## LangBridge Code team (workflow roles)

- **LangBridge** — main agent; handles light, well-understood work directly,
  coordinates specialists for larger work, and owns `todo_list.md`.
- **Planner** — researches the repo and returns a plan DRAFT (it writes no files).
- **Worker** — implements one assigned subtask from its pinned contract (never
  reads the plan file); reports ready, in progress, or blocked.
- **Reviewer** — inspects the worker summary plus Git diff;
  `REVIEW_VERDICT: PASS|NEEDS_WORK|FAIL`.
- **Explorer** — read-only codebase investigation (`agent_explorer`).

## How it works

The **LangBridge** main agent handles chat or kicks off multi-step work. The
**Planner** researches the repo and returns a plan draft (Desired end state,
Success criteria, todos with verify commands); LangBridge reviews it and writes
the final plan to the current session artifact `todo_list.md` with regular file
tools. No `todo_list.md` is retained in the workspace root.
For each unchecked item, LangBridge calls **agent_worker** with a **focused
subtask prompt** (not the whole plan) carrying all needed context. After review
passes, LangBridge merges the ready branch and marks that line `[x]` itself.
When every todo is checked, LangBridge may report the project finished.

Each delegated task has a stable `task_name`. If a Worker↔Reviewer loop stops
before approval, its partial work stays in its isolated worktree (normal
non-PASS returns are committed; a hard Stop leaves completed edits in place).
The branch and worktree path are derived from that stable name: one coding task,
one worktree. The worktree is recorded as failed/resumable. On a later turn,
LangBridge leaves that branch unmerged and can call `agent_worker` again with
the same `task_name`, the unchanged task contract, and the previous return in
`supplemental_context`. The new worker resumes the same worktree instead of
starting over. Only Reviewer-PASS (`ready`) branches can be merged, one at a
time; each successful merge cleans only that task's worktree. A changed contract
uses a fresh `task_name`.

**Main agent tools include:** filesystem, shell, tests,
`merge_branch`, `read_webpage`, `read_skill`, `ask_user`,
`note_progress`, `memory_writer`, and the subagent tools (`agent_planner`,
`agent_worker`, `agent_explorer`). Git operations other than `merge_branch`
go through the shell (`bash`).

**Planner tools:** read-only filesystem and `read_skill`;
the main agent writes the plan file.

**Worker tools:** filesystem reads/writes, shell, tests, and
`read_skill`. **Reviewer tools:** read-only filesystem,
tests, and `read_skill`.

File tools are limited to the directory where you start LangBridge Code. Routine
writes run directly. Approval is reserved for high-risk or difficult-to-reverse
operations such as recursive deletion, force push, privilege escalation, raw disk
writes, and writes inside protected state directories.

On-demand skills: specialists see a catalog of playbooks in their prompt and can
call `read_skill(name)` to load one. Bundled skills include Karpathy guidelines
and vendored [Superpowers](https://github.com/obra/superpowers) under
`src/skills/_external/superpowers/`.

Each tool call includes a required `description` field: a short, user-visible sentence
explaining why the agent is calling that tool. It feeds the live thinking line in the TUI.

Each run writes session artifacts under the installation root, grouped by
project (the directory you launched from):
`<install-root>/artifacts/{project}/session-{slug}-{timestamp}/` with
`todo_list.md`, `progress.md`, `progress-{task-slug}.md`, `traces.md`, `traces/`,
and `worktrees.json`. In a development checkout, `<install-root>` is the
repository root. Override it with `LANGBRIDGE_ARTIFACTS_DIR` or
`paths.artifacts_dir`.
Isolated Git worktrees live separately under
`<workspace>/agent-state/workflow/worktrees/` by default.
After a successful merge, LangBridge removes every worktree from that session
whose branch is already contained in the main branch.
On startup you can resume a previous session or start a new one.

### Living agents vs. traces (memory)

Within one chat session the **main agent stays alive** across user messages.
Within one Worker↔Reviewer loop each specialist stays alive across its own tool
steps and handoffs. Worker and Reviewer have separate message histories. A later
re-dispatch creates fresh model sessions but restores the unfinished task from disk.

Main-agent cold-start uses full `traces.md` when it fits the resume budget;
otherwise it uses `progress.md` plus traces after the last progress boundary.
Worker, Reviewer, and Explorer dispatches similarly use one shared
`{task-slug}/progress.md` (written by Worker/Explorer) plus the prior raw trace
tail for that role. Reusing the same Worker task's `task_name` also reuses its
failed worktree branch, so notes, conversation evidence, and code resume
together.

The session artifact directory keeps the main agent's files at its root and
gives each subagent task its own directory:

```
session-{slug}-{timestamp}/
├── progress.md          main-agent progress note
├── traces.md            main-agent raw rounds (markdown + json blocks)
├── session.md           unified human-readable activity log (all agents)
├── attachments/         oversized payloads linked from session.md / audits
├── compactions.jsonl    progress-note merge audit
└── {task-slug}/         one directory per subagent task
    ├── progress.md      task note, shared across dispatches and roles
    └── {role}-{n}.md    raw trace of dispatch instance n (same format as traces.md)
```

On a later Worker, Reviewer, or Explorer dispatch of the same task, that
role's previous traces are loaded when they fit the resume budget; otherwise
its progress note is combined with the newest complete raw rounds.

Long-term memory uses two indexes which are both considered on every prefetch:
`~/.langbridge-code/memory.md` (global user scope) and
`<project>/.langbridge/memory.md` (repository scope). Global entries may be
typed `user`, `feedback`, or `reference`; project scope also permits `project`.
Individual entries live beside each index under `memory/`, use YAML frontmatter
(`name`, `description`, `type`), and are semantically deduplicated on write.
Override the indexes with `LANGBRIDGE_USER_MEMORY_PATH`,
`LANGBRIDGE_PROJECT_MEMORY_PATH`, or matching `paths.*` user-config keys.

### Context forks

Progress notes and memory maintenance both fork the live conversation prefix so
providers can reuse prompt cache.

`note_progress` uses `fork_progress_note`: an Edit-restricted `fork_agent` that
may only rewrite `progress.md` (other tools are denied). The main agent, Worker,
and Explorer all use this path.

Memory maintenance uses `fork_agent` inside a restricted temporary Memory
workspace (read/write/Edit/bash). The main agent can invoke `memory_writer`
during a turn; if it does not, the same Memory Writer is scheduled in the
background when the turn ends.

Context compaction and memory prefetch are separate mechanisms.

### Status tokens (machine-checkable)

- **Worker:** `WORKER_STATUS: READY_FOR_REVIEW | IN_PROGRESS | BLOCKED`
- **Reviewer:** `REVIEW_VERDICT: PASS | NEEDS_WORK | FAIL`

### Limits

Bounded by workflow time limits, worker/reviewer step caps, and context compaction.
After a stop before approval, a later LangBridge turn can re-dispatch the same
task to resume its branch, progress note, and trace tail. It edits or splits the
todo only when the contract itself is blocked or needs to change.

## Eval (benchmarks & datasets)

The `eval/` tree measures LangBridge Code on real issues and builds new task data.

### SWE-bench e2e (`eval/`)

End-to-end benchmark on published SWE-bench instances: run headless LangBridge
Code inside each instance's official Docker image (repo already at
`base_commit`, dependencies installed), capture `git diff` as the patch, then
grade with the official harness.

```bash
# Once: install datasets + swebench
uv sync --group eval

# Stage 1 — generate predictions (agent inside the official SWE-bench image)
sg docker -c "uv run python eval/run_public_eval.py --difficulty verified --count 10"

# Stage 2 — grade (from eval/ so grader logs land under eval/)
cd eval && uv run python -m swebench.harness.run_evaluation \
  --dataset_name princeton-nlp/SWE-bench_Verified \
  --predictions_path out/predictions.jsonl \
  --max_workers 4 --run_id langbridge-verified
```

Datasets: `verified` (500) and `pro` (731 public, hard). Lite is not supported.
Pro uses Scale's grading harness; see `eval/README.md`.

### langbridge-bench (`eval/data/` + `eval/`)

Self-built benchmark from GitHub PRs. Pipeline under
`eval/data-pipeline/`; eval-ready specs under
`eval/data/langbridge-bench/specs/` (Dockerfiles under
`eval/data/langbridge-bench/docker-images/`).

```bash
uv run python eval/data-pipeline/run_pipeline.py
uv run python eval/run_eval.py --workers 4 --limit 5
```

See `eval/data-pipeline/README.md` and `eval/README.md`.

## Run

### Models & providers

LangBridge Code is **not tied to a single vendor**. Package defaults in
`src/config.json` use **Moonshot Kimi**; OpenAI and DeepSeek are
also built in.

| Provider (`api.provider`) | Default model | API used | API key (env or `api_keys.*`) |
| --- | --- | --- | --- |
| `moonshot` (default) | `kimi-k2.7-code` | Chat completions (`/v1/chat/completions`) | `MOONSHOT_API_KEY`, `KIMI_API_KEY`, `api_keys.moonshot` |
| `openai` | `gpt-5.6` | OpenAI **Responses** API | `OPENAI_API_KEY`, `api_keys.openai` |
| `deepseek` | `deepseek-v4-pro`; Explorer: `deepseek-v4-flash` | OpenAI-compatible chat completions | `DEEPSEEK_API_KEY`, `api_keys.deepseek` |

Switch provider:

```bash
# one-off
LANGBRIDGE_API_PROVIDER=openai LANGBRIDGE_MODEL=gpt-5.6 uv run langbridge-code

# or persist in ~/.langbridge-code/config.json
```

```json
{
  "api": { "provider": "openai" }
}
```

Use DeepSeek with its packaged per-agent defaults:

```json
{
  "api": { "provider": "deepseek" }
}
```

Provider models and base URLs live under `api.providers.<provider>`. A top-level
`model` in user config overrides that provider's default. `LANGBRIDGE_MODEL`
overrides both the session model and every per-agent model; use
`LANGBRIDGE_API_BASE_URL` for a one-off compatible endpoint override.

### API keys

Choose a provider with `LANGBRIDGE_API_PROVIDER` or
`~/.langbridge-code/config.json`. When provider selection runs directly on a TTY
with no explicit choice, it offers Moonshot, OpenAI, and DeepSeek and saves the
answer; non-interactive launches use the packaged Moonshot default. A missing API
key is requested and saved under `api_keys.<provider>`. Provider keys can live
side by side:

```json
{
  "api_keys": {
    "moonshot": "sk-...",
    "openai": "sk-...",
    "deepseek": "sk-..."
  }
}
```

Environment overrides: `MOONSHOT_API_KEY` / `KIMI_API_KEY` (Kimi),
`OPENAI_API_KEY` (OpenAI), `DEEPSEEK_API_KEY` (DeepSeek),
`LANGBRIDGE_API_PROVIDER`, `LANGBRIDGE_MODEL`, and `LANGBRIDGE_API_BASE_URL`.

Copy any section from `src/config.json` into
`~/.langbridge-code/config.json` to override limits, paths, or tool budgets.

### TypeScript TUI (default)

The TUI is a TypeScript/Ink app (`tui/`) that talks to the Python agent engine
over a JSONL stdio bridge (`src/ui/bridge.py`) — a clean,
command-driven layout: a welcome banner, a flowing conversation, a multi-line
prompt, and a status bar.

```bash
uv run langbridge-code
```

`langbridge-code` launches the TypeScript TUI. On first launch it installs a
managed Node.js/npm when necessary, runs `npm ci`, and builds `tui/dist`.
Point at specific Node, npm, or Python binaries with `LANGBRIDGE_NODE`,
`LANGBRIDGE_NPM`, or `LANGBRIDGE_PYTHON`.
`LANGBRIDGE_BRIDGE_MODULE` overrides the Python bridge module,
Mouse wheel scrolling is on by default. Terminals cannot do native
drag-select and app wheel-scroll at once. Press `Ctrl+E` for select
mode, then drag to copy; `Ctrl+E` again restores the wheel.
`Ctrl+O` or `/copy` copies the last assistant reply via the terminal
clipboard. `PageUp`/`PageDown` and `Ctrl+↑`/`Ctrl+↓` also scroll.
Set `LANGBRIDGE_TUI_MOUSE=0` to start in select mode.
`LANGBRIDGE_TUI_DEBUG=<path>` records bridge JSONL for debugging.

While developing locally, prefer `uv run langbridge-code` (editable install) so code
changes take effect immediately. Use `uv sync --reinstall-package langbridge-code
--no-editable` only when you need a non-editable install.
Development and test dependencies are intentionally excluded from a normal
launch; install them with `uv sync --group dev`.

**Commands** (type in the prompt):

| Command | Action |
| --- | --- |
| `/help` | show all commands |
| `/new` | start a new session |
| `/sessions` | open the session picker (scrollable popup, also `Ctrl+R`) |
| `/resume [n]` | open the picker, or resume session number `<n>` |
| `/delete <n>` | delete session number `<n>` |
| `/approve [on\|off]` | approve a pending action, or toggle auto-approve |
| `/yolo [on\|off]` | toggle yolo mode (auto-approve eligible operations) |
| `/deny` | deny a pending action |
| `/pause` | pause / resume the running agent |
| `/stop` | stop the current turn |
| `/queue` | show queued messages waiting to run |
| `/queue clear` | drop all queued messages |
| `/goal <condition>` | work autonomously until the condition is met |
| `/goal` | show active goal status |
| `/goal clear` | remove the current goal |
| `/goal pause` | pause goal auto-continue |
| `/goal resume` | resume a paused goal |
| `/banner [on\|off]` | show or hide the header box |
| `/exit` | quit |
| `/quit` | alias for `/exit` |

**Keys**: `Ctrl+A` approve · `Ctrl+D` deny · `Ctrl+Y` yolo · `Ctrl+P` pause ·
`Ctrl+S` stop · `Ctrl+R` sessions · `Ctrl+B` header · `Ctrl+J` newline ·
`PageUp`/`PageDown` scroll · `Ctrl+C` quit.

**Sessions**: `Ctrl+R` (or `/sessions`) opens a scrollable popup of saved
sessions — move with `↑`/`↓`, `Enter` to resume, `n` for a new session, and
`Esc` to cancel.

**Queue**: while a turn is running you can keep typing — messages wait in the
queue and run after the current turn finishes successfully; queued messages do
not auto-run after Stop or an error. Each started turn gets the next id when
processing begins; session + progress notes are written when the main agent loop
ends (success, stop, timeout, or error).

**Pause** (soft hold): holds the agent at the next step boundary and resumes the
same run in place. It takes effect *between* steps, so an in-flight model call or
tool finishes first; it also works during planner/coder/reviewer steps.

**Stop** (hard abort): aborts the current turn and hands control back, like
Cursor's stop. It cancels the in-flight model request (abandoned in the
background) instead of waiting for it, so control returns almost immediately. The
half-finished model round is discarded so the conversation history stays valid.
Long-running shell and test tools are stop-aware: their process group is killed
and the run unwinds immediately. Completed traces and progress notes (written by
`note_progress` as a full override of `progress.md`) remain available for resume.

**Approvals**: routine edits, commits, and ordinary shell commands run without a
prompt. High-risk calls post an inline approval request; approve with `Ctrl+A` /
`/approve` or deny with `Ctrl+D` / `/deny`. Root/home recursive deletion remains
behind a circuit breaker even in yolo mode.

### One-shot (headless)

Run the agent on a single task without the interactive prompt. It reads the task
from the first argument (or stdin), approves every requested operation, and exits
after one main-agent turn. It returns zero after that turn even when the task was
not resolved; non-zero is reserved for runtime setup failure or missing input.
This is the path the SWE-bench eval drives.

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
