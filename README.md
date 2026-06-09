# langbridge-cli

A tiny interactive coding agent CLI backed by a Codex model.

It runs a small ReAct loop and can call local read-only tools to inspect the
current workspace:

- `list_dir`: list files and directories
- `find_files`: find files and directories by name
- `read_file`: read UTF-8 text files
- `edit_file`: edit UTF-8 text files with exact string replacement
- `search_files`: search UTF-8 text files for exact text matches
- `run_tests`: run Python unit tests with a timeout

File tools are limited to the directory where you start the CLI.

The prompt uses `prompt_toolkit`, so deletion, cursor movement, and command
history work like a normal interactive shell.

Each CLI run writes every loop's `agent_input` to a readable JSON file under
`session-history/`. The log path is printed when the CLI starts.

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
