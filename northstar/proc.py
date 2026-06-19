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
    try:
        proc = subprocess.run(
            cmd, shell=shell, env=env, timeout=timeout, input=input,
            capture_output=True, text=True,
        )
    except (FileNotFoundError, PermissionError) as e:
        # Missing or non-executable binary — report it like a 127 "command not found"
        # instead of raising, so callers (e.g. doctor) can show a clean ✗.
        return CommandResult(127, "", str(e))
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
