"""Stop-aware subprocess runner for shell/test tools.

`subprocess.run` blocks until the command finishes, so a user's stop (Ctrl+S)
could not take effect until the tool's timeout expired. This runner polls the
child and, when a stop is requested, kills the whole process group and raises
StopRequested so the run unwinds immediately.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time

from langbridge_code.agents.common import control

_POLL_SECONDS = 0.2


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()


def run_command(args, *, cwd, env, timeout: int) -> tuple[str, int | None, bool]:
    """Run a command like subprocess.run(stdout=PIPE, stderr=STDOUT, text=True).

    Returns (output, exit_code, timed_out); exit_code is None on timeout.
    Raises control.StopRequested (killing the command) if the user stops the run.
    """
    proc = subprocess.Popen(
        args,
        cwd=cwd,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,  # own process group so the whole tree dies on stop
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            output, _ = proc.communicate(timeout=_POLL_SECONDS)
            return output or "", proc.returncode, False
        except subprocess.TimeoutExpired:
            if control.stop_requested():
                _kill_process_group(proc)
                proc.communicate()
                raise control.StopRequested()
            if time.monotonic() >= deadline:
                _kill_process_group(proc)
                output, _ = proc.communicate()
                return output or "", None, True
