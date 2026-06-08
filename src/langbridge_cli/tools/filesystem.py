import json
from pathlib import Path


MAX_FILE_BYTES = 20_000
MAX_SEARCH_FILE_BYTES = 200_000
WORKSPACE_ROOT = Path.cwd().resolve()
SEARCH_SKIP_DIRS = {".git", ".venv", "__pycache__", "build", "dist", "session-history"}


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
        "name": "find_files",
        "description": "Find files and directories by name under the current workspace.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Case-insensitive text to match against file or directory names.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file path relative to the current workspace.",
                    "default": ".",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching paths to return.",
                    "default": 50,
                },
            },
            "required": ["query"],
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
                }
            },
            "required": ["path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "search_files",
        "description": "Search UTF-8 text files under the current workspace for an exact text query.",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Exact text to search for.",
                },
                "path": {
                    "type": "string",
                    "description": "Directory or file path relative to the current workspace.",
                    "default": ".",
                },
                "max_results": {
                    "type": "integer",
                    "description": "Maximum number of matching lines to return.",
                    "default": 50,
                },
            },
            "required": ["query"],
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


@tool("find_files")
def find_files(query, path=".", max_results=50):
    if not query:
        raise ValueError("query must not be empty")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such path: {path}")

    max_results = max(1, min(int(max_results), 200))
    query_lower = query.lower()
    matches = []
    paths = [target] if target.is_file() else iter_paths(target)

    for item in paths:
        if query_lower in item.name.lower():
            kind = "directory" if item.is_dir() else "file"
            matches.append({"path": str(item.relative_to(WORKSPACE_ROOT)), "type": kind})
            if len(matches) >= max_results:
                break

    return json.dumps(
        {"query": query, "path": path, "matches": matches, "truncated": len(matches) >= max_results},
        ensure_ascii=False,
        indent=2,
    )


@tool("read_file")
def read_file(path):
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

    if truncated:
        text += f"\n\n[truncated after {MAX_FILE_BYTES} bytes]"
    return text


@tool("search_files")
def search_files(query, path=".", max_results=50):
    if not query:
        raise ValueError("query must not be empty")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such path: {path}")

    max_results = max(1, min(int(max_results), 200))
    files = [target] if target.is_file() else iter_search_files(target)
    matches = []

    for file_path in files:
        if len(matches) >= max_results:
            break
        matches.extend(search_file(file_path, query, max_results - len(matches)))

    return json.dumps(
        {"query": query, "path": path, "matches": matches, "truncated": len(matches) >= max_results},
        ensure_ascii=False,
        indent=2,
    )


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


def iter_paths(directory):
    for child in sorted(directory.iterdir(), key=lambda item: item.name.lower()):
        if child.is_dir():
            if child.name not in SEARCH_SKIP_DIRS:
                yield child
                yield from iter_paths(child)
        elif child.is_file():
            yield child


def iter_search_files(directory):
    for child in iter_paths(directory):
        if child.is_file():
            yield child


def search_file(file_path, query, remaining):
    if file_path.stat().st_size > MAX_SEARCH_FILE_BYTES:
        return []

    try:
        lines = file_path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return []

    matches = []
    relative_path = str(file_path.relative_to(WORKSPACE_ROOT))
    for line_number, line in enumerate(lines, start=1):
        if query in line:
            matches.append({"path": relative_path, "line": line_number, "text": line})
            if len(matches) >= remaining:
                break
    return matches
