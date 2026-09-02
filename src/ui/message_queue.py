"""FIFO queue for user messages sent while the agent is busy."""

from collections import deque
from dataclasses import dataclass

DEFAULT_MAX_SIZE = 20


@dataclass(frozen=True)
class QueuedUserMessage:
    text: str
    image_paths: tuple[str, ...] = ()


class UserMessageQueue:
    """Thread-safe enough for TUI: main thread enqueues, worker completion drains."""

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        self._items: deque[str | QueuedUserMessage] = deque()
        self._max_size = max(1, max_size)

    def enqueue(self, text: str, image_paths=()) -> bool:
        message = (text or "").strip()
        paths = tuple(str(path) for path in (image_paths or []))
        if not message and not paths:
            return False
        if len(self._items) >= self._max_size:
            return False
        self._items.append(
            QueuedUserMessage(message, paths) if paths else message
        )
        return True

    def dequeue(self) -> str | QueuedUserMessage | None:
        if not self._items:
            return None
        return self._items.popleft()

    def clear(self) -> int:
        count = len(self._items)
        self._items.clear()
        return count

    def __len__(self) -> int:
        return len(self._items)

    def items(self) -> list[str]:
        return [
            item.text if isinstance(item, QueuedUserMessage) else item
            for item in self._items
        ]

    @property
    def full(self) -> bool:
        return len(self._items) >= self._max_size
