"""Lightweight symbol navigation helpers (LSP-style, no external server required)."""
import ast
import json
import re
from pathlib import Path

from langbridge_code.agents.common.workspace import get_workspace_root
from langbridge_code.tools.common.purpose import PURPOSE_PARAMETER
from langbridge_code.tools.filesystem import resolve_workspace_path

TOOL_SCHEMAS = [
    {
        "type": "function",
        "name": "lsp",
        "description": (
            "Navigate symbols in the workspace. Actions: document_symbols, "
            "go_to_definition, find_references. Python uses AST; other files use text search."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "purpose": PURPOSE_PARAMETER,
                "action": {
                    "type": "string",
                    "enum": ["document_symbols", "go_to_definition", "find_references"],
                },
                "path": {
                    "type": "string",
                    "description": "File path relative to the workspace.",
                },
                "symbol": {
                    "type": "string",
                    "description": "Symbol name for go_to_definition / find_references.",
                },
                "line": {
                    "type": "integer",
                    "description": "Optional 1-based line hint for disambiguation.",
                },
            },
            "required": ["purpose", "action", "path"],
            "additionalProperties": False,
        },
    }
]

TOOLS = {}


def tool(name):
    def register(function):
        TOOLS[name] = function
        return function

    return register


def _python_symbols(path: Path, text: str) -> list[dict]:
    symbols = []
    try:
        tree = ast.parse(text)
    except SyntaxError as error:
        raise ValueError(f"Python syntax error in {path}: {error}") from error
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(
                {
                    "name": node.name,
                    "kind": "class" if isinstance(node, ast.ClassDef) else "function",
                    "line": node.lineno,
                }
            )
    return sorted(symbols, key=lambda item: item["line"])


def _find_python_definition(text: str, symbol: str) -> int | None:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == symbol:
            return node.lineno
    return None


def _find_text_references(root: Path, rel_path: str, symbol: str) -> list[dict]:
    pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    hits = []
    for candidate in root.rglob("*"):
        if not candidate.is_file() or candidate.suffix in {".png", ".jpg", ".gif", ".zip"}:
            continue
        try:
            rel = str(candidate.relative_to(root))
            text = candidate.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for index, line in enumerate(text.splitlines(), start=1):
            if pattern.search(line):
                hits.append({"path": rel, "line": index, "text": line.strip()})
        if len(hits) >= 100:
            break
    return hits


@tool("lsp")
def lsp(action, path, symbol=None, line=None):
    target = resolve_workspace_path(path)
    if not target.exists() or not target.is_file():
        raise FileNotFoundError(f"No such file: {path}")
    text = target.read_text(encoding="utf-8")
    rel = str(target.relative_to(get_workspace_root()))

    if action == "document_symbols":
        if target.suffix == ".py":
            payload = {"path": rel, "symbols": _python_symbols(target, text)}
        else:
            names = re.findall(r"^\s*(?:def|class|function|const|let|var)\s+([A-Za-z_]\w*)", text, re.MULTILINE)
            payload = {
                "path": rel,
                "symbols": [{"name": name, "kind": "symbol", "line": None} for name in names],
            }
        return json.dumps(payload, ensure_ascii=False, indent=2)

    if not symbol:
        raise ValueError("symbol is required for go_to_definition and find_references")

    if action == "go_to_definition":
        def_line = _find_python_definition(text, symbol) if target.suffix == ".py" else None
        if def_line is None:
            for index, row in enumerate(text.splitlines(), start=1):
                if re.search(rf"\b(def|class|function|const|let|var)\s+{re.escape(symbol)}\b", row):
                    def_line = index
                    break
        if def_line is None:
            raise ValueError(f"Definition for {symbol!r} not found in {path}")
        if line is not None and def_line != int(line):
            pass
        return json.dumps({"path": rel, "symbol": symbol, "line": def_line}, indent=2)

    if action == "find_references":
        refs = []
        if target.suffix == ".py":
            root = get_workspace_root()
            refs = [hit for hit in _find_text_references(root, rel, symbol) if hit["path"].endswith(".py")]
        else:
            refs = _find_text_references(get_workspace_root(), rel, symbol)
        return json.dumps({"symbol": symbol, "references": refs[:100]}, ensure_ascii=False, indent=2)

    raise ValueError(f"unsupported action: {action}")
