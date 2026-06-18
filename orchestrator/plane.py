from __future__ import annotations
from dataclasses import dataclass
import httpx


@dataclass
class Issue:
    id: str
    name: str
    description_html: str
    state_id: str
    sequence_id: int


@dataclass
class Comment:
    id: str
    body_html: str
    created_at: str


class PlaneClient:
    def __init__(self, base_url, api_key, workspace_slug, project_id,
                 client: httpx.Client | None = None):
        self._prefix = f"{base_url}/api/v1/workspaces/{workspace_slug}/projects/{project_id}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def _paginate(self, url: str, params: dict | None = None) -> list[dict]:
        params = dict(params or {})
        out: list[dict] = []
        while True:
            resp = self._http.get(url, params=params)
            resp.raise_for_status()
            body = resp.json()
            out.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def list_states(self) -> dict[str, str]:
        rows = self._paginate(f"{self._prefix}/states/")
        return {r["name"]: r["id"] for r in rows}

    def list_issues_in_state(self, state_id: str) -> list[Issue]:
        rows = self._paginate(f"{self._prefix}/work-items/", {"state": state_id})
        return [self._parse_issue(r) for r in rows if r.get("state") == state_id]

    def list_comments(self, issue_id: str) -> list[Comment]:
        rows = self._paginate(f"{self._prefix}/work-items/{issue_id}/comments/")
        return [self._parse_comment(r) for r in rows]

    def add_comment(self, issue_id: str, body_html: str) -> None:
        resp = self._http.post(f"{self._prefix}/work-items/{issue_id}/comments/",
                               json={"comment_html": body_html})
        resp.raise_for_status()

    def set_state(self, issue_id: str, state_id: str) -> None:
        resp = self._http.patch(f"{self._prefix}/work-items/{issue_id}/", json={"state": state_id})
        resp.raise_for_status()

    @staticmethod
    def _parse_issue(r: dict) -> Issue:
        return Issue(id=r["id"], name=r.get("name", ""),
                     description_html=r.get("description_html", ""),
                     state_id=r.get("state", ""), sequence_id=int(r.get("sequence_id", 0)))

    @staticmethod
    def _parse_comment(r: dict) -> Comment:
        return Comment(id=r["id"], body_html=r.get("comment_html", ""),
                       created_at=r.get("created_at", ""))
