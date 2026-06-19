from __future__ import annotations
import sys
import time
import httpx

from orchestrator import obs

_RETRY_STATUS = {429, 500, 502, 503, 504}

CANONICAL_GROUPS = {
    "Draft": "backlog",
    "Ready to Dev": "unstarted",
    "In Progress": "started",
    "Review": "started",
    "QA": "started",
    "Blocked": "started",
    "Completed": "completed",
    "Deployed": "completed",
}
CANONICAL_ORDER = ["Draft", "Ready to Dev", "In Progress", "Review",
                   "QA", "Blocked", "Completed", "Deployed"]


def _retry_after(header_value, fallback) -> float:
    """Seconds to wait before retry — Plane's Retry-After header if numeric, else the backoff."""
    if header_value:
        try:
            return max(float(header_value), fallback)
        except (TypeError, ValueError):
            pass
    return fallback


def _plane_reason(response) -> str:
    """Pull the human-readable reason out of a Plane error response body."""
    try:
        body = response.json()
    except Exception:
        text = (response.text or "").strip()
        return text[:300] if text else "no response body"
    if isinstance(body, dict):
        for key in ("error", "detail", "message", "name", "identifier", "non_field_errors"):
            if key in body and body[key]:
                val = body[key]
                return "; ".join(val) if isinstance(val, list) else str(val)
        # fall back to the first field's message (DRF-style {"field": ["msg"]})
        for key, val in body.items():
            if val:
                msg = "; ".join(val) if isinstance(val, list) else str(val)
                return f"{key}: {msg}"
    return str(body)[:300]


class PlaneAdmin:
    def __init__(self, base_url, api_key, workspace_slug, client: httpx.Client | None = None,
                 sleep=time.sleep, max_retries=4):
        self._base = f"{base_url.rstrip('/')}/api/v1/workspaces/{workspace_slug}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})
        self._sleep = sleep
        self._max_retries = max_retries

    def _request(self, method, url, **kw):
        delay = 1.0
        for attempt in range(self._max_retries):
            started = time.monotonic()
            try:
                r = self._http.request(method, url, **kw)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                obs.http_error(method, url, e)
                if attempt == self._max_retries - 1:
                    raise RuntimeError(f"Plane API unreachable at {url}: {e}") from e
                obs.http_retry(method, url, None, attempt + 1, self._max_retries, delay)
                self._sleep(delay); delay *= 2; continue
            if r.status_code in _RETRY_STATUS and attempt < self._max_retries - 1:
                # 429s carry a Retry-After (seconds); honor it when present, else back off.
                wait = _retry_after(r.headers.get("Retry-After"), delay)
                obs.http_retry(method, url, r.status_code, attempt + 1, self._max_retries, wait)
                self._sleep(wait); delay *= 2; continue
            obs.http_done(method, url, r.status_code, started)
            if r.is_error:
                raise RuntimeError(
                    f"Plane API returned {r.status_code} at {url} — {_plane_reason(r)}")
            return r

    def create_project(self, name, identifier, description="") -> dict:
        if not identifier or not identifier.isalnum() or identifier != identifier.upper() or len(identifier) > 12:
            raise ValueError(
                "Plane project identifier must be non-empty, UPPERCASE, alphanumeric, and ≤12 chars "
                f"(got {identifier!r})")
        payload = {"name": name, "identifier": identifier, "description": description}
        r = self._request("POST", f"{self._base}/projects/", json=payload)
        return r.json()

    def list_states(self, project_id) -> list[dict]:
        out, params = [], {}
        url = f"{self._base}/projects/{project_id}/states/"
        while True:
            r = self._request("GET", url, params=params)
            body = r.json()
            out.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            # Plane always returns next_cursor, even on the last page — `next_page_results`
            # is the real "more pages?" flag. Also stop if the cursor stops advancing, so a
            # missing/sticky flag can never cause an infinite refetch loop.
            if not body.get("next_page_results") or not cursor or cursor == params.get("cursor"):
                return out
            params["cursor"] = cursor

    def create_state(self, project_id, name, group, color="#6B7280", sequence=None) -> dict:
        payload = {"name": name, "group": group, "color": color}
        if sequence is not None:
            payload["sequence"] = sequence
        r = self._request("POST", f"{self._base}/projects/{project_id}/states/", json=payload)
        return r.json()

    def update_state(self, project_id, state_id, **fields) -> None:
        r = self._request("PATCH", f"{self._base}/projects/{project_id}/states/{state_id}/", json=fields)

    def delete_state(self, project_id, state_id) -> None:
        r = self._request("DELETE", f"{self._base}/projects/{project_id}/states/{state_id}/")

    def state_has_items(self, project_id, state_id) -> bool:
        r = self._request("GET", f"{self._base}/projects/{project_id}/work-items/",
                           params={"state": state_id, "per_page": 1})
        return len(r.json().get("results", [])) > 0

    _DEFAULT_RENAME = {"Backlog": "Draft", "Todo": "Ready to Dev", "Done": "Completed"}

    def ensure_board(self, project_id, *, fresh: bool) -> dict:
        states = self.list_states(project_id)
        by_name = {s["name"]: s for s in states}

        # 1. rename known Plane defaults to canonical names (only if target absent)
        for src, dst in self._DEFAULT_RENAME.items():
            if src in by_name and dst not in by_name:
                self.update_state(project_id, by_name[src]["id"],
                                  name=dst, group=CANONICAL_GROUPS[dst])
                s = by_name.pop(src); s["name"] = dst; by_name[dst] = s

        # 2. fresh projects: repurpose the seeded Cancelled state into Blocked (no native group)
        if fresh and "Cancelled" in by_name and "Blocked" not in by_name:
            self.update_state(project_id, by_name["Cancelled"]["id"], name="Blocked", group="started")
            s = by_name.pop("Cancelled"); s["name"] = "Blocked"; by_name["Blocked"] = s

        # 3. create any canonical states still missing, ordered by sequence
        seq = 15000
        for name in CANONICAL_ORDER:
            if name not in by_name:
                by_name[name] = self.create_state(project_id, name, CANONICAL_GROUPS[name],
                                                   sequence=seq)
            seq += 5000

        # 4. existing projects: remove only safe leftover (empty, non-default, non-canonical) states
        if not fresh:
            for name, s in list(by_name.items()):
                if name in CANONICAL_GROUPS or s.get("default"):
                    continue
                if self.state_has_items(project_id, s["id"]):
                    print(f"northstar: leaving non-canonical state {name!r} (has work items)", file=sys.stderr)
                    continue  # holds work items — warn (left in place), never delete
                print(f"northstar: removing empty non-canonical state {name!r}", file=sys.stderr)
                self.delete_state(project_id, s["id"])
                by_name.pop(name, None)

        return {name: by_name[name]["id"] for name in CANONICAL_ORDER}
