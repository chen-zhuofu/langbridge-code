"""Track which agent is in the foreground for status-bar context usage."""
from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class ForegroundSnapshot:
    label: str
    messages: list
    model: str


_lock = threading.Lock()
_scopes: dict[int, list[tuple[object, ForegroundSnapshot, float]]] = {}
_listeners: list[Callable[[], None]] = []


def register_foreground_listener(callback) -> None:
    """Register a callback invoked whenever the foreground agent changes."""
    with _lock:
        if callback not in _listeners:
            _listeners.append(callback)


def unregister_foreground_listener(callback) -> None:
    with _lock:
        if callback in _listeners:
            _listeners.remove(callback)


def _notify() -> None:
    listeners = list(_listeners)
    for callback in listeners:
        callback()


def _thread_stack() -> list[tuple[object, ForegroundSnapshot, float]]:
    return _scopes.setdefault(threading.get_ident(), [])


def enter_foreground(label: str, messages, model: str) -> object:
    token = object()
    snapshot = ForegroundSnapshot(label=label, messages=messages, model=model)
    with _lock:
        _thread_stack().append((token, snapshot, time.monotonic()))
    _notify()
    return token


def publish_foreground(token, messages, model: str, *, label: str | None = None) -> None:
    with _lock:
        stack = _scopes.get(threading.get_ident(), [])
        for index, (entry_token, snapshot, _) in enumerate(stack):
            if entry_token is token:
                next_label = label or snapshot.label
                stack[index] = (
                    token,
                    ForegroundSnapshot(label=next_label, messages=messages, model=model),
                    time.monotonic(),
                )
                break
    _notify()


def leave_foreground(token) -> None:
    thread_id = threading.get_ident()
    with _lock:
        stack = _scopes.get(thread_id, [])
        _scopes[thread_id] = [entry for entry in stack if entry[0] is not token]
        if not _scopes[thread_id]:
            del _scopes[thread_id]
    _notify()


def current_foreground() -> ForegroundSnapshot | None:
    with _lock:
        best: ForegroundSnapshot | None = None
        best_ts = -1.0
        for stack in _scopes.values():
            for _, snapshot, updated_at in stack:
                if updated_at > best_ts:
                    best_ts = updated_at
                    best = snapshot
        return best


def clear_foreground() -> None:
    with _lock:
        _scopes.clear()
    _notify()


class ForegroundTracker:
    """Enter/publish/leave foreground context for one agent session."""

    def __init__(self, label: str, messages, model: str):
        self.label = label
        self._messages = messages
        self._model = model
        self._token = None

    def activate(self) -> None:
        if self._token is None:
            self._token = enter_foreground(self.label, self._messages, self._model)
        else:
            self.publish()

    def publish(self) -> None:
        if self._token is not None:
            publish_foreground(self._token, self._messages, self._model, label=self.label)

    def deactivate(self) -> None:
        if self._token is not None:
            leave_foreground(self._token)
            self._token = None
