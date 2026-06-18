import httpx, respx
from northstar.plane_admin import PlaneAdmin, CANONICAL_GROUPS, CANONICAL_ORDER

BASE = "https://plane.test"
WS = "acme"
WPREFIX = f"{BASE}/api/v1/workspaces/{WS}"


def admin():
    return PlaneAdmin(BASE, "key", WS, client=httpx.Client())


def test_canonical_constants():
    assert CANONICAL_GROUPS["Blocked"] == "started"
    assert CANONICAL_GROUPS["Deployed"] == "completed"
    assert CANONICAL_ORDER == ["Draft", "Ready to Dev", "In Progress", "Review",
                               "QA", "Blocked", "Completed", "Deployed"]


@respx.mock
def test_create_project_posts_and_returns_id():
    route = respx.post(f"{WPREFIX}/projects/").mock(
        return_value=httpx.Response(201, json={"id": "p1", "name": "Web", "identifier": "WEB"}))
    proj = admin().create_project("Web", "WEB")
    assert proj["id"] == "p1"
    assert b'"identifier":"WEB"' in route.calls.last.request.content


@respx.mock
def test_list_states_paginates():
    route = respx.get(f"{WPREFIX}/projects/p1/states/")
    route.side_effect = [
        httpx.Response(200, json={"results": [{"id": "s1", "name": "Backlog", "group": "backlog"}],
                                  "next_cursor": "C"}),
        httpx.Response(200, json={"results": [{"id": "s2", "name": "Done", "group": "completed"}],
                                  "next_cursor": None}),
    ]
    names = [s["name"] for s in admin().list_states("p1")]
    assert names == ["Backlog", "Done"]


@respx.mock
def test_create_state_posts_payload():
    route = respx.post(f"{WPREFIX}/projects/p1/states/").mock(
        return_value=httpx.Response(201, json={"id": "s9", "name": "QA", "group": "started"}))
    out = admin().create_state("p1", "QA", "started", sequence=20000)
    assert out["id"] == "s9"
    body = route.calls.last.request.content
    assert b'"name":"QA"' in body and b'"group":"started"' in body and b'"sequence":20000' in body


@respx.mock
def test_update_state_patches():
    route = respx.patch(f"{WPREFIX}/projects/p1/states/s1/").mock(return_value=httpx.Response(200, json={}))
    admin().update_state("p1", "s1", name="Draft", group="backlog")
    assert route.called and b'"name":"Draft"' in route.calls.last.request.content


@respx.mock
def test_delete_state():
    route = respx.delete(f"{WPREFIX}/projects/p1/states/s1/").mock(return_value=httpx.Response(204))
    admin().delete_state("p1", "s1")
    assert route.called


@respx.mock
def test_state_has_items_true_when_results_nonempty():
    route = respx.get(f"{WPREFIX}/projects/p1/work-items/").mock(
        return_value=httpx.Response(200, json={"results": [{"id": "i1"}]}))
    assert admin().state_has_items("p1", "s1") is True
    assert b"state=s1" in route.calls.last.request.url.query


@respx.mock
def test_state_has_items_false_when_empty():
    route = respx.get(f"{WPREFIX}/projects/p1/work-items/").mock(
        return_value=httpx.Response(200, json={"results": []}))
    assert admin().state_has_items("p1", "s1") is False
    assert b"state=s1" in route.calls.last.request.url.query


class RecordingAdmin(PlaneAdmin):
    """A PlaneAdmin whose CRUD methods are replaced by recorders (no HTTP)."""
    def __init__(self, states):
        self._states = states           # list of {id,name,group,default?}
        self.updates = []
        self.creates = []
        self.deletes = []
        self._has_items = set()         # state ids that "have items"
        self._next = 100

    def list_states(self, project_id):
        return list(self._states)

    def update_state(self, project_id, state_id, **fields):
        self.updates.append((state_id, fields))
        for s in self._states:
            if s["id"] == state_id:
                s.update(fields)

    def create_state(self, project_id, name, group, color="#6B7280", sequence=None):
        self._next += 1
        s = {"id": f"new{self._next}", "name": name, "group": group}
        self._states.append(s)
        self.creates.append((name, group))
        return s

    def delete_state(self, project_id, state_id):
        self.deletes.append(state_id)
        self._states = [s for s in self._states if s["id"] != state_id]

    def state_has_items(self, project_id, state_id):
        return state_id in self._has_items


DEFAULTS = lambda: [
    {"id": "d1", "name": "Backlog", "group": "backlog", "default": True},
    {"id": "d2", "name": "Todo", "group": "unstarted"},
    {"id": "d3", "name": "In Progress", "group": "started"},
    {"id": "d4", "name": "Done", "group": "completed"},
    {"id": "d5", "name": "Cancelled", "group": "cancelled"},
]


def test_ensure_board_fresh_renames_repurposes_creates():
    a = RecordingAdmin(DEFAULTS())
    ids = a.ensure_board("p1", fresh=True)
    # 4 updates: Backlog->Draft, Todo->Ready to Dev, Done->Completed, Cancelled->Blocked
    renamed = {f["name"] for _, f in a.updates}
    assert renamed == {"Draft", "Ready to Dev", "Completed", "Blocked"}
    assert len(a.updates) == 4
    # 3 creates: Review, QA, Deployed
    assert {n for n, _ in a.creates} == {"Review", "QA", "Deployed"}
    assert a.deletes == []
    # returns all 8 canonical ids
    assert set(ids) == set(CANONICAL_ORDER)


def test_ensure_board_existing_creates_missing_and_warns_nonempty_extra():
    states = [
        {"id": "x1", "name": "In Progress", "group": "started"},
        {"id": "x2", "name": "Notes", "group": "started"},   # extra, has items -> must NOT delete
        {"id": "x3", "name": "Scratch", "group": "started"},  # extra, empty -> may delete
    ]
    a = RecordingAdmin(states)
    a._has_items = {"x2"}
    ids = a.ensure_board("p1", fresh=False)
    # all 8 canonical present in the returned map
    assert set(ids) == set(CANONICAL_ORDER)
    # the non-empty extra was never deleted
    assert "x2" not in a.deletes
    # the empty extra may be removed
    assert "x3" in a.deletes
