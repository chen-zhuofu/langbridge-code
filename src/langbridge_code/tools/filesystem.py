import json
import re
import subprocess
from pathlib import Path

from langbridge_code.settings import MAX_FILE_BYTES
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER
from langbridge_code.tools.common.runtime import managed_binary
from langbridge_code.util.read_file_in_range import (
    FileTooLargeError,
    add_line_numbers,
    read_file_in_range,
)

WORKSPACE_ROOT = Path.cwd().resolve()

from langbridge_code.agents.common.workspace import (  # noqa: E402
    get_plan_file_override,
    get_workspace_root,
)

DEFAULT_GLOB_LIMIT = 100
DEFAULT_GREP_HEAD_LIMIT = 250
MAX_LINES_TO_READ = 2000
RG_TIMEOUT_SECONDS = 60
VCS_DIRECTORIES_TO_EXCLUDE = (".git", ".svn", ".hg", ".bzr", ".jj", ".sl")

TOOL_SCHEMAS = [
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
                "purpose": PURPOSE_PARAMETER,
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
            "required": ["purpose", "pattern"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "read_file",
        "description": (
            "Read a text file under the current workspace. "
            f"By default reads from the beginning of the file (up to {MAX_LINES_TO_READ} lines "
            f"or {MAX_FILE_BYTES} bytes). Use offset and limit for partial reads of large files."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "The 1-based line number to start reading from. "
                        "Only provide if the file is too large to read at once."
                    ),
                },
                "limit": {
                    "type": "integer",
                    "description": (
                        "The number of lines to read. "
                        "Only provide if the file is too large to read at once."
                    ),
                },
            },
            "required": ["purpose", "path"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "grep",
        "description": (
            "Search file contents with ripgrep (regex). Respects .gitignore. "
            "Use output_mode 'content' for matching lines, 'files_with_matches' for paths only "
            "(default), or 'count' for match counts per file."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "pattern": {
                    "type": "string",
                    "description": "The regular expression pattern to search for in file contents.",
                },
                "path": {
                    "type": "string",
                    "description": "File or directory to search in. Defaults to workspace root.",
                    "default": ".",
                },
                "glob": {
                    "type": "string",
                    "description": 'Glob pattern to filter files (e.g. "*.js", "*.{ts,tsx}").',
                },
                "output_mode": {
                    "type": "string",
                    "enum": ["content", "files_with_matches", "count"],
                    "description": (
                        'Output mode: "content" shows matching lines (supports -A/-B/-C, -n), '
                        '"files_with_matches" shows file paths (default), '
                        '"count" shows match counts.'
                    ),
                    "default": "files_with_matches",
                },
                "-B": {
                    "type": "integer",
                    "description": (
                        'Lines before each match (rg -B). Requires output_mode: "content".'
                    ),
                },
                "-A": {
                    "type": "integer",
                    "description": (
                        'Lines after each match (rg -A). Requires output_mode: "content".'
                    ),
                },
                "-C": {
                    "type": "integer",
                    "description": (
                        'Lines before and after each match (rg -C). Requires output_mode: "content".'
                    ),
                },
                "context": {
                    "type": "integer",
                    "description": "Alias for -C.",
                },
                "-n": {
                    "type": "boolean",
                    "description": (
                        'Show line numbers (rg -n). Requires output_mode: "content". Defaults to true.'
                    ),
                    "default": True,
                },
                "-i": {
                    "type": "boolean",
                    "description": "Case insensitive search (rg -i).",
                    "default": False,
                },
                "type": {
                    "type": "string",
                    "description": (
                        "File type to search (rg --type). Common types: js, py, rust, go, java."
                    ),
                },
                "head_limit": {
                    "type": "integer",
                    "description": (
                        "Limit output to first N lines/entries. Defaults to 250. Pass 0 for unlimited."
                    ),
                    "default": DEFAULT_GREP_HEAD_LIMIT,
                },
                "offset": {
                    "type": "integer",
                    "description": (
                        "Skip first N lines/entries before applying head_limit. Defaults to 0."
                    ),
                    "default": 0,
                },
                "multiline": {
                    "type": "boolean",
                    "description": (
                        "Enable multiline mode where . matches newlines (rg -U --multiline-dotall)."
                    ),
                    "default": False,
                },
            },
            "required": ["purpose", "pattern"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "Edit",
        "description": (
            "Edit a text file by replacing exact text. By default old_string must "
            "appear exactly once; set replace_all=true to replace every occurrence. "
            "For multiple independent edits, call Edit several times."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                },
                "old_string": {
                    "type": "string",
                    "description": "Exact text to replace.",
                },
                "new_string": {
                    "type": "string",
                    "description": "Replacement text (must differ from old_string).",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences of old_string (default false).",
                    "default": False,
                },
            },
            "required": ["purpose", "path", "old_string", "new_string"],
            "additionalProperties": False,
        },
    },
    {
        "type": "function",
        "name": "write",
        "description": (
            "Write a whole UTF-8 text file under the current workspace. "
            "Creates the file or overwrites it if it already exists."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "path": {
                    "type": "string",
                    "description": "File path relative to the current workspace.",
                },
                "content": {
                    "type": "string",
                    "description": "Full file content to write.",
                },
            },
            "required": ["purpose", "path", "content"],
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
    candidate = Path(path)
    plan_override = get_plan_file_override()
    if (
        plan_override is not None
        and not candidate.is_absolute()
        and candidate.parts == ("todo_list.md",)
    ):
        return plan_override
    target = (get_workspace_root() / path).resolve()
    try:
        target.relative_to(get_workspace_root())
    except ValueError:
        raise ValueError("Path must stay inside the current workspace")
    return target


def _rg_binary():
    return managed_binary("rg")


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
    return str(resolved.relative_to(get_workspace_root()))


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
            rel = str(file_path.relative_to(get_workspace_root()))
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
def read_file(path, offset=None, limit=None, start_line=None, end_line=None, function_name=None):
    """Read tool — ported from Claude Code Read (offset/limit, line-numbered output)."""
    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such file: {path}")
    if not target.is_file():
        raise IsADirectoryError(f"Not a file: {path}")

    if function_name:
        text = target.read_text(encoding="utf-8")
        return _format_function_excerpt(path, text, function_name)

    effective_offset = offset if offset is not None else start_line
    if effective_offset is None:
        effective_offset = 1

    partial_read = (
        limit is not None
        or end_line is not None
        or start_line is not None
        or (offset is not None and int(offset) > 1)
    )

    effective_limit = limit
    if effective_limit is None and end_line is not None:
        effective_limit = int(end_line) - int(effective_offset) + 1
    if effective_limit is None and not partial_read:
        effective_limit = MAX_LINES_TO_READ

    line_offset = 0 if int(effective_offset) == 0 else int(effective_offset) - 1
    max_bytes = None if partial_read else MAX_FILE_BYTES

    try:
        result = read_file_in_range(target, line_offset, effective_limit, max_bytes)
    except UnicodeDecodeError:
        raise ValueError(f"File is not valid UTF-8 text: {path}") from None
    except FileTooLargeError as error:
        raise ValueError(str(error)) from error

    if not result.content and result.total_lines < int(effective_offset):
        return (
            f"<system-reminder>Warning: the file exists but is shorter than the provided "
            f"offset ({effective_offset}). The file has {result.total_lines} lines.</system-reminder>"
        )
    if result.total_lines == 0:
        return "<system-reminder>Warning: the file exists but the contents are empty.</system-reminder>"

    numbered = add_line_numbers(result.content, int(effective_offset))
    end_line_no = int(effective_offset) + max(result.line_count - 1, 0)
    header = f"# {path} lines {effective_offset}-{end_line_no} ({result.total_lines} lines total)"
    return f"{header}\n{numbered}"


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


def _coerce_grep_kwargs(kwargs):
    """Map Claude Code Grep schema keys (incl. -B/-A/-i) to implementation kwargs."""
    out = dict(kwargs)
    alias_map = {
        "-B": "context_before",
        "-A": "context_after",
        "-C": "context_c",
        "context": "context_c",
        "-n": "show_line_numbers",
        "-i": "case_insensitive",
        "glob_pattern": "glob",
        "ignore_case": "case_insensitive",
        "file_type": "type",
    }
    for old, new in alias_map.items():
        if old in out and new not in out:
            out[new] = out.pop(old)
        elif old in out:
            out.pop(old)
    return out


def _apply_head_limit(items, limit, offset=0):
    if limit == 0:
        return items[offset:], None
    effective_limit = limit if limit is not None else DEFAULT_GREP_HEAD_LIMIT
    sliced = items[offset : offset + effective_limit]
    was_truncated = len(items) - offset > effective_limit
    return sliced, effective_limit if was_truncated else None


def _format_grep_limit_info(applied_limit, applied_offset):
    parts = []
    if applied_limit is not None:
        parts.append(f"limit: {applied_limit}")
    if applied_offset:
        parts.append(f"offset: {applied_offset}")
    return ", ".join(parts)


def _relativize_grep_line(line, search_target):
    colon_index = line.find(":")
    if colon_index <= 0:
        return line
    file_path = line[:colon_index]
    rest = line[colon_index:]
    try:
        resolved = Path(file_path).resolve()
        if not resolved.exists():
            return line
        return _relative_workspace_path(file_path) + rest
    except (ValueError, OSError):
        return line


def _split_glob_patterns(glob_value):
    patterns = []
    for raw_pattern in glob_value.split():
        if "{" in raw_pattern and "}" in raw_pattern:
            patterns.append(raw_pattern)
        else:
            patterns.extend(part for part in raw_pattern.split(",") if part)
    return [pattern for pattern in patterns if pattern]


@tool("grep")
def grep(pattern, path=".", **raw_kwargs):
    """Grep tool — ported from Claude Code Grep (ripgrep wrapper, text output)."""
    kwargs = _coerce_grep_kwargs(raw_kwargs)
    glob = kwargs.get("glob")
    file_type = kwargs.get("type")
    output_mode = kwargs.get("output_mode", "files_with_matches")
    context_before = kwargs.get("context_before")
    context_after = kwargs.get("context_after")
    context_c = kwargs.get("context_c")
    show_line_numbers = kwargs.get("show_line_numbers", True)
    case_insensitive = kwargs.get("case_insensitive", False)
    head_limit = kwargs.get("head_limit")
    offset = int(kwargs.get("offset") or 0)
    multiline = kwargs.get("multiline", False)

    if not pattern:
        raise ValueError("pattern must not be empty")
    if output_mode not in {"content", "files_with_matches", "count"}:
        raise ValueError("output_mode must be 'content', 'files_with_matches', or 'count'")

    target = resolve_workspace_path(path)
    if not target.exists():
        raise FileNotFoundError(f"No such path: {path}")

    args = [_rg_binary(), "--color=never", "--hidden", "--max-columns", "500"]
    for directory in VCS_DIRECTORIES_TO_EXCLUDE:
        args.extend(["--glob", f"!{directory}"])

    if multiline:
        args.extend(["-U", "--multiline-dotall"])
    if case_insensitive:
        args.append("-i")
    if output_mode == "files_with_matches":
        args.append("-l")
    elif output_mode == "count":
        args.append("-c")
    if show_line_numbers and output_mode == "content":
        args.append("-n")

    if output_mode == "content":
        if context_c is not None:
            args.extend(["-C", str(int(context_c))])
        else:
            if context_before is not None:
                args.extend(["-B", str(int(context_before))])
            if context_after is not None:
                args.extend(["-A", str(int(context_after))])

    if pattern.startswith("-"):
        args.extend(["-e", pattern])
    else:
        args.append(pattern)

    if file_type:
        args.extend(["--type", file_type])
    if glob:
        for glob_pattern in _split_glob_patterns(glob):
            args.extend(["--glob", glob_pattern])

    args.append(str(target))
    stdout = _run_rg(args)
    lines = [line for line in stdout.splitlines() if line.strip()]

    if output_mode == "content":
        limited, applied_limit = _apply_head_limit(lines, head_limit, offset)
        final_lines = [_relativize_grep_line(line, target) for line in limited]
        content = "\n".join(final_lines) if final_lines else "No matches found"
        limit_info = _format_grep_limit_info(applied_limit, offset)
        if limit_info:
            content = f"{content}\n\n[Showing results with pagination = {limit_info}]"
        return content

    if output_mode == "count":
        limited, applied_limit = _apply_head_limit(lines, head_limit, offset)
        final_lines = [_relativize_grep_line(line, target) for line in limited]
        total_matches = 0
        file_count = 0
        for line in final_lines:
            colon_index = line.rfind(":")
            if colon_index > 0:
                count_str = line[colon_index + 1 :]
                try:
                    total_matches += int(count_str)
                    file_count += 1
                except ValueError:
                    continue
        raw_content = "\n".join(final_lines) if final_lines else "No matches found"
        limit_info = _format_grep_limit_info(applied_limit, offset)
        summary = (
            f"\n\nFound {total_matches} total "
            f"{'occurrence' if total_matches == 1 else 'occurrences'} across "
            f"{file_count} {'file' if file_count == 1 else 'files'}."
        )
        if limit_info:
            summary += f" with pagination = {limit_info}"
        return raw_content + summary

    stats = []
    for match in lines:
        try:
            stats.append((match, Path(match).resolve().stat().st_mtime))
        except (OSError, ValueError):
            stats.append((match, 0))
    stats.sort(key=lambda item: (-item[1], item[0]))
    sorted_matches = [item[0] for item in stats]
    limited, applied_limit = _apply_head_limit(sorted_matches, head_limit, offset)
    relative_matches = []
    for match in limited:
        try:
            relative_matches.append(_relative_workspace_path(match))
        except ValueError:
            continue
    if not relative_matches:
        return "No files found"
    limit_info = _format_grep_limit_info(applied_limit, offset)
    suffix = f" {limit_info}" if limit_info else ""
    file_word = "file" if len(relative_matches) == 1 else "files"
    return f"Found {len(relative_matches)} {file_word}{suffix}\n" + "\n".join(relative_matches)


@tool("Edit")
def Edit(path, old_string, new_string, replace_all=False):
    if not old_string:
        raise ValueError("old_string must not be empty")
    if old_string == new_string:
        raise ValueError("No changes to make: old_string and new_string are exactly the same.")

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
    if matches > 1 and not replace_all:
        raise ValueError(
            f"Found {matches} matches of the string to replace, but replace_all is false. "
            "To replace all occurrences, set replace_all to true. "
            "To replace only one occurrence, provide more context to uniquely identify it."
        )

    if replace_all:
        updated = text.replace(old_string, new_string)
        count = matches
    else:
        updated = text.replace(old_string, new_string, 1)
        count = 1
    target.write_text(updated, encoding="utf-8")
    occurrence = "occurrence" if count == 1 else "occurrences"
    return f"Edited {path}: replaced {count} {occurrence}."


@tool("write")
def write(path, content):
    target = resolve_workspace_path(path)
    if not target.parent.exists():
        target.parent.mkdir(parents=True, exist_ok=True)
    existed = target.exists()
    target.write_text(content, encoding="utf-8")
    if existed:
        return f"Wrote {path} (overwrote existing file)."
    return f"Wrote {path}."
