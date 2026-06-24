from __future__ import annotations
from dataclasses import dataclass, field
import httpx
import time

from orchestrator import obs

_RETRY_STATUS = {429, 500, 502, 503, 504}


@dataclass
class Issue:
    id: str
    name: str
    description_html: str
    state_id: str
    sequence_id: int
    labels: list[str] = field(default_factory=list)  # label names (resolved from ids)


@dataclass
class Comment:
    id: str
    body_html: str
    created_at: str


class PlaneClient:
    def __init__(self, base_url, api_key, workspace_slug, project_id,
                 client: httpx.Client | None = None, sleep=time.sleep, max_retries=3):
        self._prefix = f"{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})
        self._sleep = sleep
        self._max_retries = max_retries
        self._label_names: dict[str, str] | None = None  # lazy id->name cache

    def _send(self, method, url, **kw):
        delay = 0.5
        for attempt in range(self._max_retries):
            started = time.monotonic()
            try:
                resp = self._http.request(method, url, **kw)
            except (httpx.ConnectError, httpx.TimeoutException) as e:
                obs.http_error(method, url, e)
                if attempt == self._max_retries - 1:
                    raise
                obs.http_retry(method, url, None, attempt + 1, self._max_retries, delay)
                self._sleep(delay); delay *= 2; continue
            if resp.status_code in _RETRY_STATUS and attempt < self._max_retries - 1:
                obs.http_retry(method, url, resp.status_code, attempt + 1, self._max_retries, delay)
                self._sleep(delay); delay *= 2; continue
            obs.http_done(method, url, resp.status_code, started)
            resp.raise_for_status()
            return resp
        resp.raise_for_status()
        return resp

    def _paginate(self, url: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {})
        out: list[dict] = []
        while True:
            resp = self._send("GET", url, params=params)
            body = resp.json()
            out.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            # Plane always returns next_cursor, even on the last page — `next_page_results`
            # is the real "more pages?" flag. Also stop if the cursor stops advancing, so a
            # missing/sticky flag can never cause an infinite refetch loop.
            if not body.get("next_page_results") or not cursor or cursor == params.get("cursor"):
                return out
            params["cursor"] = cursor

    def list_states(self) -> dict[str, str]:
        rows = self._paginate(f"{self._prefix}/states/")
        return {r["name"]: r["id"] for r in rows}

    def list_labels(self) -> dict[str, str]:
        """Project label id -> name. Cached: the skip-review label set is fixed per project,
        so one fetch per daemon process is enough. An id missing from the map resolves to no
        name, which fail-safes to 'don't skip' (the reviewer still runs)."""
        if self._label_names is None:
            rows = self._paginate(f"{self._prefix}/labels/")
            self._label_names = {r["id"]: r["name"] for r in rows}
        return self._label_names

    def list_issues_in_state(self, state_id: str, per_page: int = 25) -> list[Issue]:
        rows = self._paginate(f"{self._prefix}/work-items/", {"state": state_id, "per_page": per_page})
        return [self._parse_issue(r) for r in rows if r.get("state") == state_id]

    def list_comments(self, issue_id: str) -> list[Comment]:
        rows = self._paginate(f"{self._prefix}/work-items/{issue_id}/comments/")
        return [self._parse_comment(r) for r in rows]

    def add_comment(self, issue_id: str, body_html: str) -> None:
        self._send("POST", f"{self._prefix}/work-items/{issue_id}/comments/",
                   json={"comment_html": body_html})

    def set_state(self, issue_id: str, state_id: str) -> None:
        self._send("PATCH", f"{self._prefix}/work-items/{issue_id}/", json={"state": state_id})

    def get_issue(self, issue_id: str) -> Issue:
        resp = self._send("GET", f"{self._prefix}/work-items/{issue_id}/")
        return self._parse_issue(resp.json())

    def list_blocked_by(self, issue_id: str) -> list[str]:
        resp = self._send("GET", f"{self._prefix}/work-items/{issue_id}/relations/")
        return resp.json().get("blocked_by", []) or []

    def _parse_issue(self, r: dict) -> Issue:
        label_ids = r.get("labels") or []
        names = self.list_labels() if label_ids else {}
        labels = [names[lid] for lid in label_ids if lid in names]
        return Issue(id=r["id"], name=r.get("name", ""),
                     description_html=r.get("description_html", ""),
                     state_id=r.get("state", ""), sequence_id=int(r.get("sequence_id", 0)),
                     labels=labels)

    @staticmethod
    def _parse_comment(r: dict) -> Comment:
        return Comment(id=r["id"], body_html=r.get("comment_html", ""),
                       created_at=r.get("created_at", ""))
