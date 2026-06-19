"""Human-readable activity log for everything northstar does to the outside world.

Every subprocess we launch and every HTTP call we make routes through here, so the
operator can see — in plain language, in the terminal and in `northstar logs` — exactly
what is happening: which command ran, which service was hit, the result, and how long it
took. This is the single place that decides what that output looks like.

Toggles (read from the environment at call time):
  NORTHSTAR_QUIET=1   silence all activity lines
  NORTHSTAR_DEBUG=1   include extra detail (full URLs instead of just the path)

Output goes to stderr so it never pollutes stdout (e.g. JSON a command may print).
Secrets are redacted before anything is written.
"""
from __future__ import annotations
import os
import re
import shlex
import sys
import time

_TAG = "northstar"

# --- redaction (defense in depth: secrets should never reach here, but never log them if they do) ---
# KEY=value / TOKEN=value / SECRET=value / PASSWORD=value  (e.g. the tmux `env PLANE_API_KEY=…` string)
_ENV_SECRET_RE = re.compile(r"(\b[\w-]*(?:KEY|TOKEN|SECRET|PASSWORD|PASSWD|PASS)[\w-]*)=(\S+)", re.I)
# bare credentials that may appear anywhere (GitHub PATs / OAuth tokens)
_TOKEN_RE = re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{16,}|github_pat_[A-Za-z0-9_]{20,})\b")


def redact(text: str) -> str:
    text = _ENV_SECRET_RE.sub(r"\1=***", text)
    text = _TOKEN_RE.sub("***", text)
    return text


def _truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _enabled() -> bool:
    return not _truthy("NORTHSTAR_QUIET")


def _emit(category: str, message: str) -> None:
    if _enabled():
        print(f"{time.strftime('%H:%M:%S')} {_TAG} › {category:<5} {message}",
              file=sys.stderr, flush=True)


def format_cmd(cmd, shell: bool = False) -> str:
    if isinstance(cmd, (list, tuple)):
        s = " ".join(shlex.quote(str(c)) for c in cmd)
    else:
        s = str(cmd)
    return redact(s)


def _short_url(url: str) -> str:
    if _truthy("NORTHSTAR_DEBUG"):
        return redact(url)
    # show just the path (the host carries no useful signal and the key is never in the URL)
    path = url.split("://", 1)[-1]
    slash = path.find("/")
    return redact(path[slash:] if slash != -1 else path)


# --- subprocess / commands -------------------------------------------------
def exec_start(cmd, shell: bool = False) -> float:
    _emit("exec", f"$ {format_cmd(cmd, shell)}")
    return time.monotonic()


def exec_done(started: float, returncode: int, *, timed_out: bool = False) -> None:
    dur = time.monotonic() - started
    if timed_out:
        _emit("exec", f"✗ timed out  ({dur:.2f}s)")
    elif returncode == 0:
        _emit("exec", f"✓ ok  ({dur:.2f}s)")
    else:
        _emit("exec", f"✗ exit {returncode}  ({dur:.2f}s)")


# --- HTTP / external services ----------------------------------------------
def http_done(method: str, url: str, status: int, started: float, *, service: str = "plane") -> None:
    dur = time.monotonic() - started
    icon = "✓" if status < 400 else "✗"
    _emit(service, f"{icon} {method} {_short_url(url)} → {status}  ({dur:.2f}s)")


def http_retry(method: str, url: str, status, attempt: int, total: int, wait: float,
               *, service: str = "plane") -> None:
    reason = status if status is not None else "network error"
    _emit(service, f"⟳ {method} {_short_url(url)} → {reason}; retry {attempt}/{total} in {wait:.1f}s")


def http_error(method: str, url: str, err: BaseException, *, service: str = "plane") -> None:
    _emit(service, f"✗ {method} {_short_url(url)} → {type(err).__name__}: {err}")


# --- free-form activity ----------------------------------------------------
def info(category: str, message: str) -> None:
    """A one-off activity line for things that aren't a command or an HTTP call."""
    _emit(category, redact(message))
