# Tools and agent access

This package defines LangBridge tool schemas and implementations. Agents do not
all share one toolkit — each role gets a filtered set (and sometimes a guarded
variant of `bash`).

Source of truth for assembly:

| Role | Where the set is built |
|------|------------------------|
| Main agent (LangBridge) | `agents/main_agent.py` → `MAIN_AGENT_TOOL_SCHEMAS` |
| Worker / Reviewer | `agents/worker_reviewer.py` → `CODE_WORKER_*` / `REVIEWER_*` |
| Explorer | `agents/explorer.py` → `EXPLORE_*` |
| Planner | `agents/planner.py` → `PLANNER_*` |
| Goal Evaluator | `agents/goal_evaluator.py` → `EVALUATOR_TOOL_SCHEMAS` |
| Progress-note fork | `agents/common/fork.py` → `fork_session_memory` |
| Memory Writer fork | `tools/memory_writer.py` → `run_memory_writer_agent` |

Shared bundles also live in `tools/__init__.py` (`MAIN_TOOL_SCHEMAS`,
`GOAL_VERIFICATION_TOOL_SCHEMAS`).

## Tool catalog (this package)

| Tool | Module | What it does |
|------|--------|----------------|
| `glob` | `filesystem.py` | Find files by path pattern (`rg --files`) |
| `grep` | `filesystem.py` | Search file contents (`rg`) |
| `read_file` | `filesystem.py` | Read workspace / allowed artifact text |
| `Edit` | `filesystem.py` | Exact string replace in a file |
| `write` | `filesystem.py` | Create or overwrite a whole file |
| `bash` | `execution.py` | Run `bash -c` in the workspace |
| `powershell` | `execution.py` | Run `pwsh -Command` when available |
| `read_webpage` | `web.py` | Fetch a URL as readable text |
| `read_skill` | `skills.py` | Load a role-scoped skill playbook |
| `merge_branch` | `merge_branch.py` | Merge a ready worker feature branch (main only) |
| `ask_user` | `ask_user.py` | Ask the user a clarifying question (main only) |
| `update_session_memory` | `update_session_memory.py` | Schedule an Edit-restricted progress fork |
| `memory_writer` | `memory_writer.py` | Schedule a Memory Writer fork |

Helpers: `common/` (env, proc, runtime binaries, description parameter),
`concurrency.py` (which calls may run in parallel).

Subagent dispatch tools (`agent_planner`, `agent_worker`, `agent_explorer`) are
**not** defined here — they live under `agents/` and are attached only on the
main agent.

## Who has what

Legend: **Y** = available · **RO** = read-only bash (writes/mutators rejected) ·
**—** = not available · **fork** = tool schedules a nested agent with a narrower set

| Tool | Main | Worker | Reviewer | Explorer | Planner | Goal Eval | Progress fork | Memory Writer |
|------|:----:|:------:|:--------:|:--------:|:-------:|:---------:|:-------------:|:-------------:|
| `glob` | Y | Y | Y | Y | Y | Y | —* | — |
| `grep` | Y | Y | Y | Y | Y | Y | —* | — |
| `read_file` | Y | Y | Y | Y | Y | Y | —* | Y |
| `Edit` | Y | Y | — | — | — | Y | Y (session_memory.md only) | Y |
| `write` | Y | Y | — | — | — | Y | —* | Y |
| `bash` | Y | Y | Y | RO | RO | Y | —* | Y |
| `powershell` | Y | Y | Y | — | — | Y | — | — |
| `read_webpage` | Y | — | — | Y | Y | Y | — | — |
| `read_skill` | Y | Y | Y | Y | Y | Y | — | — |
| `merge_branch` | Y | — | — | — | — | — | — | — |
| `ask_user` | Y | — | — | — | — | — | — | — |
| `update_session_memory` | Y (fork) | Y (fork) | Y (fork) | Y (fork) | — | — | — | — |
| `memory_writer` | Y (fork) | Y (fork) | Y (fork) | — | — | Y (fork) | — | — |
| `agent_planner` | Y | — | — | — | — | — | — | — |
| `agent_worker` | Y | — | — | — | — | — | — | — |
| `agent_explorer` | Y | — | — | — | — | — | — | — |

\* Progress-note forks keep the **parent’s tool schemas** for prompt-cache
alignment, but every tool except `Edit` on the target `session_memory.md` is denied
at runtime.

### Role notes

**Main (LangBridge)** — Full orchestration kit: files + shell + web + skills +
`merge_branch` + `ask_user` + `update_session_memory` + `memory_writer` + the three
`agent_*` dispatch tools. In eval finalization, `agent_*` tools are stripped
(`FINALIZATION_TOOL_SCHEMAS`).

**Worker (coder)** — Implement in a worktree: read/write files, shell
(`bash` / `powershell`), `read_skill`, plus `update_session_memory` and
`memory_writer`. No web, no ask_user, no merge, no other subagents.

**Reviewer** — Verify only: read tools + shell + `read_skill` +
`update_session_memory` + `memory_writer`. No `Edit` / `write`.

**Explorer** — Map the codebase: read tools + **read-only** `bash` +
`read_webpage` + `read_skill` + `update_session_memory`. No file writes, no
`powershell`.

**Planner** — Draft plans: same read surface as explorer (read tools +
read-only `bash` + `read_webpage` + `read_skill`). No writes, no
`ask_user` — main agent writes `todo_list.md`.

**Goal Evaluator** (`/goal`) — Same verification surface as main minus
`merge_branch`, plus `memory_writer`. Used to judge completion independently.

**Progress-note fork** — Nested writer started by `update_session_memory`. Effectively
**Edit-only** on that agent’s `session_memory.md`.

**Memory Writer fork** — Nested writer started by `memory_writer`. Tools:
`read_file`, `write`, `Edit`, `bash` inside a staged memory workspace
(`MEMORY_FILE_TOOL_NAMES` in `memory/__init__.py`).

## Quick mental model

```text
Main
├── does light work itself (files / bash / web)
├── ask_user / merge_branch / update_session_memory / memory_writer
└── dispatches
    ├── agent_planner  → read-only research → draft plan text
    ├── agent_explorer → read-only mapping → findings
    └── agent_worker   → worker (write) ⇄ reviewer (read+shell)
```

Prefer dedicated tools over shell for file content (`Edit` / `write`) and for
code search (`grep` / `glob`). Use `bash` for installs, tests, git, and
one-off commands.
