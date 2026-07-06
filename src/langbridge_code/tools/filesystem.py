import json
import shutil
import subprocess
from pathlib import Path

from langbridge_cli.settings import MAX_FILE_BYTES

WORKSPACE_ROOT = Path.cwd().resolve()
DEFAULT_GLOB_LIMIT = 100
DEFAULT_GREP_LIMIT = 250
RG_TIMEOUT_SECONDS = 60

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "list_dir",
        "description": "List files and directories under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Directory path relative to the current workspace.",
                    "default": ".",
                }
            },
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "glob",
        "description": (
            "Find files by glob pattern under the workspace (powered by ripgrep). "
            "Respects .gitignore. Example patterns: '*.py', '**/*.ts', 'src/**/*.md'."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Glob pattern to match file paths.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory to search relative to the workspace.",
                    "default": ".",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching paths to return.",
                    "default": DEFAULT_GLOB_LIMIT,
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": "Read a text file under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                },
                "start_line": {
                    "type": "integer",
                    "description": "Optional 1-based start line for a partial read.",
                },
                "end_line": {
                    "type": "integer",
                    "description": "Optional 1-based end line for a partial read (inclusive).",
                },
                "function_name": {
                    "type": "string",
                    "description": "Optional Python function name to extract from the file.",
                },
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "grep",
        "description": (
            "Search file contents with ripgrep (regex). Respects .gitignore. "
            "Use output_mode 'content' for matching lines or 'files_with_matches' for paths only."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {
                    "type": "string",
                    "description": "Regular expression to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory path relative to the workspace.",
                    "default": ".",
                },
                "glob_pattern": {
                    "type": "string",
                    "description": "Optional glob to filter which files are searched (e.g. '*.py').",
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches"],
                    "description": "Return matching lines or only file paths.",
                    "default": "content",
                },
                "head_limit": {
                    "type": "integer",
                    "description": "Maximum number of lines or files to return.",
                    "default": DEFAULT_GREP_LIMIT,
                },
                "ignore_case": {
                    "type": "boolean",
                    "description": "Case-insensitive search.",
                    "default": False,
                },
            },
            "required": ["pattern"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "edit_file",
        "description": "Edit a text file by replacing one exact, unique string with another string.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace. It must appear exactly once in the file.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text.",
                },
            },
            "required": ["path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "create_file",
        "description": "Create a new UTF-8 text file under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "New file path relative to the current workspace.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write.",
                },
            },
            "required": ["path", "content"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "delete_file",
        "description": "Delete a file under the current workspace. This does not delete directories.",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def resolve_workspace_path(path):
    target = (WORKSPACE_ROOT / path).resolve()
    try:
        target.relative_to(WORKSPACE_ROOT)
    except ValueError:
        raise ValueError("Path must stay inside the current workspace")
    return target


def _rg_binary():
    rg = shutil.which("rg")
    if not rg:
        raise RuntimeError(
            "ripgrep (rg) is required for grep/glob tools but was not found on PATH. "
            "Install ripgrep or use bash."
        )
    return rg


def _run_rg(args):
    result = subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=RG_TIMEOUT_SECONDS,
    )
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or "rg failed").strip()
        raise RuntimeError(detail)
    return result.stdout


def _relative_workspace_path(path):
    resolved = Path(path).resolve()
    return str(resolved.relative_to(WORKSPACE_ROOT))


@tool("list_dir")
def list_dir(path="."):
    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such directory: {path}")
    if not target.is_dir():
        raise NotADirectoryError(f"Not a directory: {path}")

    entries = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        kind = "directory" if child.is_dir() else "file"
        entries.append({"name": child.name, "type": kind})

    return json.dumps({"path": str(target.relative_to(WORKSPACE_ROOT)), "entries": entries}, indent=2)


@tool("glob")
def glob(pattern, path=".", max_results=DEFAULT_GLOB_LIMIT):
    if not pattern:
        raise ValueError("pattern must not be empty")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such path: {path}")

    max_results = max(1, min(int(max_results), 500))
    stdout = _run_rg([_rg_binary(), "--files", "--glob", pattern, str(target)])

    ranked = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        file_path = Path(line.strip()).resolve()
        try:
            rel = str(file_path.relative_to(WORKSPACE_ROOT))
        except ValueError:
            continue
        ranked.append((file_path.stat().st_mtime, rel))

    ranked.sort(key=lambda item: item[0], reverse=True)
    matches = [rel for _, rel in ranked[:max_results]]
    return json.dumps(
        {
            "pattern": pattern,
            "path": path,
            "matches": matches,
            "truncated": len(ranked) > max_results,
        },
        ensure_ascii=False,
        indent=2,
    )


@tool("read_file")
def read_file(path, start_line=None, end_line=None, function_name=None):
    if function_name and (start_line is not None or end_line is not None):
        raise ValueError("Specify function_name or a line range, not both")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    data = target.read_bytes()
    truncated = len(data) > MAX_FILE_BYTES
    data = data[:MAX_FILE_BYTES]

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {path}")

    if function_name:
        return _format_function_excerpt(path, text, function_name)

    lines = text.splitlines()
    total_lines = len(lines)
    if start_line is not None or end_line is not None:
        start = max(1, int(start_line or 1))
        end = min(total_lines, int(end_line or total_lines))
        if start > end:
            raise ValueError("start_line must be <= end_line")
        selected = lines[start - 1 : end]
        body = "\n".join(f"{index}| {line}" for index, line in enumerate(selected, start=start))
        header = f"# {path} lines {start}-{end} ({total_lines} lines total)"
        if truncated:
            body += f"\n\n[truncated after {MAX_FILE_BYTES} bytes]"
        return f"{header}\n{body}"

    if truncated:
        text += f"\n\n[truncated after {MAX_FILE_BYTES} bytes]"
    return text


def _format_function_excerpt(path, text, function_name):
    import re

    lines = text.splitlines()
    pattern = re.compile(rf"^(\s*)def {re.escape(function_name)}\s*\(")
    start_index = None
    indent = ""
    for index, line in enumerate(lines):
        match = pattern.match(line)
        if match:
            start_index = index
            indent = match.group(1)
            break
    if start_index is None:
        raise ValueError(f"Function {function_name!r} not found in {path}")

    end_index = len(lines)
    for index in range(start_index + 1, len(lines)):
        line = lines[index]
        if not line.strip():
            continue
        if line.startswith(indent) and not line.startswith(indent + " "):
            if line.lstrip().startswith(("def ", "class ", "async def ")):
                end_index = index
                break

    excerpt = "\n".join(lines[start_index:end_index])
    return f"# {path} function `{function_name}`\n{excerpt}"


@tool("grep")
def grep(pattern, path=".", glob_pattern=None, output_mode="content", head_limit=DEFAULT_GREP_LIMIT, ignore_case=False):
    if not pattern:
        raise ValueError("pattern must not be empty")
    if output_mode not in {"content", "files_with_matches"}:
        raise ValueError("output_mode must be 'content' or 'files_with_matches'")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such path: {path}")

    head_limit = max(1, min(int(head_limit), 1000))
    args = [_rg_binary(), "--color=never", "--max-count", str(head_limit)]
    if ignore_case:
        args.append("-i")
    if glob_pattern:
        args.extend(["--glob", glob_pattern])
    if output_mode == "files_with_matches":
        args.append("-l")
    else:
        args.append("-n")
    args.extend([pattern, str(target)])

    stdout = _run_rg(args)
    if output_mode == "files_with_matches":
        matches = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            try:
                matches.append(_relative_workspace_path(line.strip()))
            except ValueError:
                continue
        payload = {"pattern": pattern, "path": path, "files": matches[:head_limit]}
    else:
        lines = []
        for line in stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split(":", 2)
            if len(parts) < 3:
                continue
            file_path, line_number, text = parts
            try:
                rel = _relative_workspace_path(file_path)
            except ValueError:
                continue
            lines.append({"path": rel, "line": int(line_number), "text": text})
            if len(lines) >= head_limit:
                break
        payload = {"pattern": pattern, "path": path, "matches": lines}

    payload["truncated"] = len(stdout.splitlines()) >= head_limit
    return json.dumps(payload, ensure_ascii=False, indent=2)


@tool("edit_file")
def edit_file(path, old_string, new_string):
    if not old_string:
        raise ValueError("old_string must not be empty")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    try:
        text = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {path}")

    matches = text.count(old_string)
    if matches == 0:
        raise ValueError("old_string was not found")
    if matches > 1:
        raise ValueError(f"old_string matched {matches} times; provide a unique replacement target")

    target.write_text(text.replace(old_string, new_string, 1), encoding="utf-8")
    return f"Edited {path}: replaced 1 occurrence."


@tool("create_file")
def create_file(path, content):
    target = resolve_workspace_path(path)
    if target.exists():
        raise FileExistsError(f"File already exists: {path}")
    if not target.parent.exists():
        raise FileNotFoundError(f"Parent directory does not exist: {target.parent.relative_to(WORKSPACE_ROOT)}")

    target.write_text(content, encoding="utf-8")
    return f"Created {path}."


@tool("delete_file")
def delete_file(path):
    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    target.unlink()
    return f"Deleted {path}."
