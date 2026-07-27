"""Process-wide run control for the agent loops: pause and stop.

Only one agent run is active at a time (the TUI runs it in a single worker
thread), so module-level state is enough.

- Pause is a soft hold: the loops block at a step boundary and resume in place.
- Stop is a hard abort: the loops raise `StopRequested` to unwind the whole run.

The loops call `checkpoint()` at each step boundary, and wrap the blocking model
call in `run_interruptible()` so a stop can abandon an in-flight request without
waiting for it to return. Defaults are "running, not stopped", so non-TUI callers
(headless, plain REPL) never block or abort on their own.
"""

import threading


class StopRequested(BaseException):
    """Raised inside the agent loops to abort the current run.

    Subclasses BaseException (not Exception) so the loops' broad
    ``except Exception`` tool-error handlers do not swallow it.
    """


class TurnAborted(StopRequested):
    """Raised when the user denies an action at the terminal approval prompt.

    Like a stop, it unwinds the whole turn instead of feeding a tool error back
    to the model. The plain REPL catches it and waits for the next message.
    """


_resume_event = threading.Event()
_resume_event.set()
_stop_event = threading.Event()

_POLL_SECONDS = 0.05


def pause():
    _resume_event.clear()


def resume():
    _resume_event.set()


def is_paused():
    return not _resume_event.is_set()


def request_stop():
    # Setting resume too so a paused worker can wake up and observe the stop.
    _stop_event.set()
    _resume_event.set()


def clear_stop():
    _stop_event.clear()


def stop_requested():
    return _stop_event.is_set()


def checkpoint():
    """Call at each loop step boundary: abort if stopped, else block while paused."""
    if _stop_event.is_set():
        raise StopRequested()
    _resume_event.wait()
    if _stop_event.is_set():
        raise StopRequested()


def run_interruptible(call):
    """Run a blocking `call()` but abandon it and raise StopRequested on stop.

    The call runs in a daemon thread. If a stop arrives first, we raise
    immediately and let the daemon finish in the background; its result is
    discarded. The call must not mutate shared state, only return a value.
    """
    if _stop_event.is_set():
        raise StopRequested()

    result = {}
    done = threading.Event()

    def worker():
        try:
            result["value"] = call()
        except BaseException as error:  # noqa: BLE001 - propagate to caller thread
            result["error"] = error
        finally:
            done.set()

    threading.Thread(target=worker, daemon=True).start()
    while not done.wait(_POLL_SECONDS):
        if _stop_event.is_set():
            raise StopRequested()
    if "error" in result:
        raise result["error"]
    return result["value"]
