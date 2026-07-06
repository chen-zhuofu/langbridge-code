"""Kimi Code CLI search tool schemas (Grep, Glob)."""

KIMI_GREP_SCHEMA = {
    "type": "function",
    "name": "Grep",
    "description": (
        "Search file contents with ripgrep. Supports regex pattern, glob/type filters, "
        "output_mode (files_with_matches, content, count_matches), context lines, "
        "and pagination via offset/head_limit."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "The regular expression pattern to search for in file contents.",
            },
            "path": {
                "type": "string",
                "description": (
                    "File or directory to search in. Defaults to the working directory."
                ),
                "default": ".",
            },
            "glob": {
                "type": "string",
                "description": "Glob pattern to filter files (e.g. `*.js`, `*.{ts,tsx}`).",
            },
            "type": {
                "type": "string",
                "description": (
                    "File type to search (e.g. py, rust, js). More efficient than glob "
                    "for standard file types."
                ),
            },
            "output_mode": {
                "type": "string",
                "enum": ["content", "files_with_matches", "count_matches"],
                "description": (
                    "content: matching lines; files_with_matches: file paths; "
                    "count_matches: match counts per file. Defaults to files_with_matches."
                ),
                "default": "files_with_matches",
            },
            "-B": {
                "type": "integer",
                "description": "Lines before each match (content mode only).",
            },
            "-A": {
                "type": "integer",
                "description": "Lines after each match (content mode only).",
            },
            "-C": {
                "type": "integer",
                "description": "Lines before and after each match (content mode only).",
            },
            "-n": {
                "type": "boolean",
                "description": "Show line numbers in content mode. Defaults to true.",
                "default": True,
            },
            "-i": {
                "type": "boolean",
                "description": "Case insensitive search.",
                "default": False,
            },
            "head_limit": {
                "type": "integer",
                "description": (
                    "Limit output entries/lines. Defaults to 250; 0 means unlimited."
                ),
                "default": 250,
            },
            "offset": {
                "type": "integer",
                "description": "Skip first N entries before applying head_limit.",
                "default": 0,
            },
            "multiline": {
                "type": "boolean",
                "description": "Enable multiline matching where `.` matches newlines.",
                "default": False,
            },
            "include_ignored": {
                "type": "boolean",
                "description": (
                    "Search files ignored by .gitignore. Sensitive files remain filtered."
                ),
                "default": False,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}

KIMI_GLOB_SCHEMA = {
    "type": "function",
    "name": "Glob",
    "description": (
        "Match files in a directory by glob pattern. Results are sorted by modification "
        "time (newest first), capped at 100 entries. Respects .gitignore by default."
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
                "description": "Directory to search. Defaults to the working directory.",
                "default": ".",
            },
            "include_ignored": {
                "type": "boolean",
                "description": (
                    "Include files ignored by .gitignore. Sensitive files remain filtered."
                ),
                "default": False,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}

KIMI_SEARCH_SCHEMAS = [KIMI_GLOB_SCHEMA, KIMI_GREP_SCHEMA]
KIMI_SEARCH_TOOL_NAMES = {"Grep", "Glob"}
