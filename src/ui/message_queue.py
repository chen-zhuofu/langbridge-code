"""FIFO queue for user messages sent while the agent is busy."""

from collections import deque

DEFAULT_MAX_SIZE = 20


class UserMessageQueue:
    """Thread-safe enough for TUI: main thread enqueues, worker completion drains."""

    def __init__(self, max_size: int = DEFAULT_MAX_SIZE):
        self._items: deque[str] = deque()
        self._max_size = max(1, max_size)

    def enqueue(self, text: str) -> bool:
        message = (text or "").strip()
        if not message:
            return False
        if len(self._items) >= self._max_size:
            return False
        self._items.append(message)
        return True

    def dequeue(self) -> str | None:
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
        return list(self._items)

    @property
    def full(self) -> bool:
        return len(self._items) >= self._max_size
