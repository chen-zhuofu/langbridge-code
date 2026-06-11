# langbridge-cli

A tiny interactive coding-agent CLI backed by a Codex model.

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

Before each tool call, the CLI prints a concise user-facing rationale and the
selected action. It can use an API reasoning summary as a fallback, but does
not expose the model's raw chain-of-thought.

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
