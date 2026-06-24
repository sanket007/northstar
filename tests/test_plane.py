import httpx, respx
from orchestrator.plane import PlaneClient, Issue

BASE = "https://plane.test"
WS = "acme"
PROJ = "proj1"
PREFIX = f"{BASE}/api/v1/workspaces/{WS}/projects/{PROJ}"


def make_client():
    return PlaneClient(BASE, "key", WS, PROJ, client=httpx.Client())


@respx.mock
def test_list_states_maps_name_to_id():
    respx.get(f"{PREFIX}/states/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"id": "s1", "name": "Ready to Dev"},
            {"id": "s2", "name": "QA"},
        ], "next_cursor": None,
    }))
    states = make_client().list_states()
    assert states == {"Ready to Dev": "s1", "QA": "s2"}


@respx.mock
def test_list_issues_in_state_parses_and_filters():
    respx.get(f"{PREFIX}/work-items/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"id": "i1", "name": "Add health", "description_html": "<p>do it</p>",
             "state": "s1", "sequence_id": 7},
        ], "next_cursor": None,
    }))
    issues = make_client().list_issues_in_state("s1")
    assert issues == [Issue(id="i1", name="Add health",
                            description_html="<p>do it</p>", state_id="s1", sequence_id=7)]


@respx.mock
def test_list_issues_resolves_label_names():
    respx.get(f"{PREFIX}/labels/").mock(return_value=httpx.Response(200, json={
        "results": [{"id": "l1", "name": "docs"}, {"id": "l2", "name": "feature"}],
        "next_cursor": None,
    }))
    respx.get(f"{PREFIX}/work-items/").mock(return_value=httpx.Response(200, json={
        "results": [
            {"id": "i1", "name": "Doc it", "description_html": "", "state": "s1",
             "sequence_id": 7, "labels": ["l1", "unknown"]},
        ], "next_cursor": None,
    }))
    issues = make_client().list_issues_in_state("s1")
    assert issues[0].labels == ["docs"]  # id resolved to name; unknown ids drop out (fail-safe)


@respx.mock
def test_list_comments_paginates():
    route = respx.get(f"{PREFIX}/work-items/i1/comments/")
    # Real Plane always returns a non-null next_cursor; `next_page_results` ends pagination.
    route.side_effect = [
        httpx.Response(200, json={"results": [{"id": "c1", "comment_html": "<p>a</p>",
                                               "created_at": "t1"}],
                                  "next_cursor": "20:1:0", "next_page_results": True}),
        httpx.Response(200, json={"results": [{"id": "c2", "comment_html": "<p>b</p>",
                                               "created_at": "t2"}],
                                  "next_cursor": "20:2:0", "next_page_results": False}),
    ]
    comments = make_client().list_comments("i1")
    assert [c.id for c in comments] == ["c1", "c2"]


@respx.mock
def test_set_state_patches_work_item():
    route = respx.patch(f"{PREFIX}/work-items/i1/").mock(return_value=httpx.Response(200, json={}))
    make_client().set_state("i1", "s2")
    assert route.called
    sent = route.calls.last.request
    assert b'"state":"s2"' in sent.content


@respx.mock
def test_add_comment_posts_html():
    route = respx.post(f"{PREFIX}/work-items/i1/comments/").mock(
        return_value=httpx.Response(201, json={}))
    make_client().add_comment("i1", "<p>hi</p>")
    assert route.called
    assert b"<p>hi</p>" in route.calls.last.request.content


def test_send_retries_on_5xx_then_succeeds():
    import httpx, respx
    from orchestrator.plane import PlaneClient
    slept = []
    with respx.mock:
        route = respx.get("https://x/api/v1/workspaces/w/projects/p/states/")
        route.side_effect = [httpx.Response(503), httpx.Response(200, json={"results": [{"id": "s1", "name": "Draft"}], "next_cursor": None})]
        c = PlaneClient("https://x", "k", "w", "p", client=httpx.Client(), sleep=lambda d: slept.append(d))
        assert c.list_states() == {"Draft": "s1"}
        assert route.call_count == 2 and slept  # retried and slept once


@respx.mock
def test_get_issue_and_list_blocked_by():
    import httpx, respx
    from orchestrator.plane import PlaneClient, Issue
    pfx = "https://x/api/v1/workspaces/w/projects/p"
    respx.get(f"{pfx}/work-items/i9/").mock(return_value=httpx.Response(200, json={
        "id": "i9", "name": "n", "description_html": "", "state": "sDone", "sequence_id": 9}))
    respx.get(f"{pfx}/work-items/i1/relations/").mock(return_value=httpx.Response(200, json={
        "blocked_by": ["i9"], "blocking": []}))
    c = PlaneClient("https://x", "k", "w", "p", client=httpx.Client())
    assert c.get_issue("i9") == Issue("i9", "n", "", "sDone", 9)
    assert c.list_blocked_by("i1") == ["i9"]
    assert c.list_blocked_by("i1") == ["i9"] or c.list_blocked_by("i9") == []  # tolerant
