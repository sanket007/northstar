from __future__ import annotations
from dataclasses import dataclass
import subprocess


@dataclass
class CommandResult:
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run(cmd, *, shell=False, env=None, timeout=None, input=None) -> CommandResult:
    # These are non-interactive tool invocations. Detach stdin (unless we're feeding `input`)
    # so an unexpectedly interactive command gets EOF and exits instead of hanging forever.
    stdin = None if input is not None else subprocess.DEVNULL
    try:
        proc = subprocess.run(
            cmd, shell=shell, env=env, timeout=timeout, input=input, stdin=stdin,
            capture_output=True, text=True,
        )
    except (FileNotFoundError, PermissionError) as e:
        # Missing or non-executable binary — report it like a 127 "command not found"
        # instead of raising, so callers (e.g. doctor) can show a clean ✗.
        return CommandResult(127, "", str(e))
    except subprocess.TimeoutExpired:
        # A genuinely stuck command — report a 124 "timed out" instead of hanging.
        return CommandResult(124, "", f"timed out after {timeout}s")
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
