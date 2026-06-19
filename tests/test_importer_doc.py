from pathlib import Path

def test_importer_doc_has_key_invariants():
    d = Path("templates/plane-importer.md").read_text().lower()
    assert "grill-me" in d                      # grills the whole plan
    assert "draft" in d                          # creates Draft tasks
    assert "blocked_by" in d or "create_work_item_relation" in d   # dependencies
    assert "external_id" in d or "[ns:" in d     # idempotency marker
    assert "acceptance criteria" in d and "citation" in d
    assert "directly in the plane board" in d or "hand-created" in d  # compliance note
