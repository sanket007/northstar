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
