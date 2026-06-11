# langbridge-cli

An interactive coding-agent CLI backed by a Codex model.

LangBridge runs a PM-led coding-agent loop. The PM can inspect the workspace,
delegate implementation to specialist agents, resume previous JSON session
history, and compact older context when the conversation gets long.

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

Each CLI run writes readable JSON history under `session-history/`. On startup,
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
  and adds end-to-end tests.
- **PM**: cross-functional collaboration with design, data science, product, and
  marketing.
- **L6 engineer**: large-scale and high-concurrency system design,
  implementation, and cross-team collaboration with other coding-agent teams.
- **Manager**: keeps agents aligned, unblocks work, and improves team execution.

## Run

```bash
uv run --no-editable langbridge
```

On first run, `langbridge-cli` asks for your Codex API key and saves it to `~/.langbridge/config.json`.
You can still override it with `OPENAI_API_KEY`.

Use `LANGBRIDGE_MODEL` to override the default model:

```bash
LANGBRIDGE_MODEL=gpt-5.1-codex uv run --no-editable langbridge
```

Install locally to get the `langbridge` command:

```bash
uv sync --no-editable
source .venv/bin/activate
langbridge
```

Inside the CLI, type `/exit` to quit.

### Textual UI

Use the Textual UI for a richer terminal experience: live agent thoughts,
session management, and L4 write approvals.

```bash
LANGBRIDGE_TUI=1 uv run langbridge
```

While developing locally, prefer `uv run langbridge` (editable install) so code
changes take effect immediately. Use `uv sync --reinstall-package langbridge-cli
--no-editable` only when you need a non-editable install.

**Session bar** (top):

- **Session dropdown**: pick an existing session from `session-history/`.
- **Resume**: load the selected session and continue appending to its log.
- **Delete**: remove the selected session JSON file.
- On launch, a new session is started automatically; use Resume to switch.

**Thought display**:

- Shows only the latest **thought** (`purpose`) from PM, L4, or L3 — not tool
  calls — in muted text.
- Clears when the assistant reply is shown.
- **Ctrl+T** or click the thought bar to expand full thought + action history
  for the current turn.

**Approvals**:

- **Always approve: off/on**: toggle to auto-approve all write tools.
- When off, a yellow approval bar appears above the input for:
  - PM delegate requests (`ask_l4_engineer`)
  - L4 write tools (`edit_file`, `create_file`, `delete_file`)
- Use **Approve** / **Deny**, or **Ctrl+A** / **Ctrl+D**.

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
