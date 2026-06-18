from orchestrator import state_machine as sm


def test_role_for_state():
    assert sm.role_for_state("Ready to Dev") == "builder"
    assert sm.role_for_state("In Progress") == "builder"
    assert sm.role_for_state("Review") == "reviewer"
    assert sm.role_for_state("QA") == "qa"
    assert sm.role_for_state("Completed") is None
    assert sm.role_for_state("Blocked") is None


def test_allowed_transitions():
    assert sm.is_allowed("Ready to Dev", "In Progress")
    assert sm.is_allowed("In Progress", "Blocked")
    assert sm.is_allowed("In Progress", "Review")
    assert sm.is_allowed("Review", "In Progress")
    assert sm.is_allowed("Review", "QA")
    assert sm.is_allowed("QA", "In Progress")
    assert sm.is_allowed("QA", "Completed")


def test_disallowed_transitions():
    assert not sm.is_allowed("Review", "Completed")   # must pass QA first
    assert not sm.is_allowed("Ready to Dev", "Completed")
    assert not sm.is_allowed("QA", "Review")
