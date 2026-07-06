"""OpenAI Codex search tool schemas (grep_files, glob_file_search)."""

OPENAI_GREP_FILES_SCHEMA = {
    "type": "function",
    "name": "grep_files",
    "description": (
        "Finds files whose contents match the pattern and lists them by modification time."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Regular expression pattern to search for.",
            },
            "include": {
                "type": "string",
                "description": (
                    'Optional glob that limits which files are searched (e.g. "*.rs" or "*.{ts,tsx}").'
                ),
            },
            "path": {
                "type": "string",
                "description": (
                    "Directory or file path to search. Defaults to the session's working directory."
                ),
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of file paths to return (defaults to 100).",
                "default": 100,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}

OPENAI_GLOB_FILE_SEARCH_SCHEMA = {
    "type": "function",
    "name": "glob_file_search",
    "description": (
        "Pattern-based file search using glob syntax. More efficient than find for "
        "locating files by name pattern."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "pattern": {
                "type": "string",
                "description": "Glob pattern to match file paths (e.g. '*.py', '**/*.ts').",
            },
            "path": {
                "type": "string",
                "description": "Directory to search. Defaults to the working directory.",
                "default": ".",
            },
            "limit": {
                "type": "integer",
                "description": "Maximum number of matching paths to return (defaults to 100).",
                "default": 100,
            },
        },
        "required": ["pattern"],
        "additionalProperties": False,
    },
}

OPENAI_SEARCH_SCHEMAS = [OPENAI_GLOB_FILE_SEARCH_SCHEMA, OPENAI_GREP_FILES_SCHEMA]
OPENAI_SEARCH_TOOL_NAMES = {"grep_files", "glob_file_search"}
