from __future__ import annotations
import httpx

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


class PlaneAdmin:
    def __init__(self, base_url, api_key, workspace_slug, client: httpx.Client | None = None):
        self._base = f"{base_url.rstrip('/')}/api/v1/workspaces/{workspace_slug}"
        self._http = client or httpx.Client(timeout=30)
        self._http.headers.update({"X-API-Key": api_key, "Content-Type": "application/json"})

    def create_project(self, name, identifier, description="") -> dict:
        payload = {"name": name, "identifier": identifier, "description": description}
        r = self._http.post(f"{self._base}/projects/", json=payload)
        r.raise_for_status()
        return r.json()

    def list_states(self, project_id) -> list[dict]:
        out, params = [], {}
        url = f"{self._base}/projects/{project_id}/states/"
        while True:
            r = self._http.get(url, params=params)
            r.raise_for_status()
            body = r.json()
            out.extend(body.get("results", []))
            cursor = body.get("next_cursor")
            if not cursor:
                return out
            params["cursor"] = cursor

    def create_state(self, project_id, name, group, color="#6B7280", sequence=None) -> dict:
        payload = {"name": name, "group": group, "color": color}
        if sequence is not None:
            payload["sequence"] = sequence
        r = self._http.post(f"{self._base}/projects/{project_id}/states/", json=payload)
        r.raise_for_status()
        return r.json()

    def update_state(self, project_id, state_id, **fields) -> None:
        r = self._http.patch(f"{self._base}/projects/{project_id}/states/{state_id}/", json=fields)
        r.raise_for_status()

    def delete_state(self, project_id, state_id) -> None:
        r = self._http.delete(f"{self._base}/projects/{project_id}/states/{state_id}/")
        r.raise_for_status()

    def state_has_items(self, project_id, state_id) -> bool:
        r = self._http.get(f"{self._base}/projects/{project_id}/work-items/",
                           params={"state": state_id, "per_page": 1})
        r.raise_for_status()
        return len(r.json().get("results", [])) > 0
