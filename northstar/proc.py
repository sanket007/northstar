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
    proc = subprocess.run(
        cmd, shell=shell, env=env, timeout=timeout, input=input,
        capture_output=True, text=True,
    )
    return CommandResult(proc.returncode, proc.stdout or "", proc.stderr or "")
