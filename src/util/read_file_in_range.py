"""Line-oriented file reader (ported from Claude Code readFileInRange)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


class FileTooLargeError(Exception):
    def __init__(self, size_in_bytes: int, max_size_bytes: int):
        self.size_in_bytes = size_in_bytes
        self.max_size_bytes = max_size_bytes
        super().__init__(
            f"File content ({size_in_bytes} bytes) exceeds maximum allowed size "
            f"({max_size_bytes} bytes). Use offset and limit parameters to read specific "
            "portions of the file, or search for specific content instead of reading "
            "the whole file."
        )


@dataclass(frozen=True)
class ReadFileRangeResult:
    content: str
    line_count: int
    total_lines: int
    total_bytes: int
    read_bytes: int


def read_file_in_range(
    file_path: Path,
    offset: int = 0,
    max_lines: int | None = None,
    max_bytes: int | None = None,
) -> ReadFileRangeResult:
    """Return lines [offset, offset + max_lines) from a UTF-8 text file."""
    if file_path.is_dir():
        raise IsADirectoryError(f"EISDIR: illegal operation on a directory, read '{file_path}'")

    raw = file_path.read_bytes()
    if max_bytes is not None and len(raw) > max_bytes:
        raise FileTooLargeError(len(raw), max_bytes)

    text = raw.decode("utf-8")
    if text.startswith("\ufeff"):
        text = text[1:]

    lines = text.splitlines()
    total_lines = len(lines)
    end = offset + max_lines if max_lines is not None else total_lines
    selected = lines[offset:end]
    content = "\n".join(selected)
    return ReadFileRangeResult(
        content=content,
        line_count=len(selected),
        total_lines=total_lines,
        total_bytes=len(raw),
        read_bytes=len(content.encode("utf-8")),
    )


def add_line_numbers(content: str, start_line: int) -> str:
    if not content:
        return ""
    lines = content.splitlines()
    return "\n".join(f"{index + start_line}\t{line}" for index, line in enumerate(lines))
