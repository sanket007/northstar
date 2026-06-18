from __future__ import annotations
import sys
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
        if not identifier or not identifier.isalnum() or identifier != identifier.upper() or len(identifier) > 12:
            raise ValueError(
                "Plane project identifier must be non-empty, UPPERCASE, alphanumeric, and ≤12 chars "
                f"(got {identifier!r})")
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
