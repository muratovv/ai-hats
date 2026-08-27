"""The refusal a caller reads when a link kind does not resolve.

The wording is the subject here, not the exit code. It had no test at all, and
an untested human string is one nobody notices rotting — this one sent a session
off to "fix" three correct instructions because it presented one catalog's kind
set as if it were absolute.

Every claim is asserted in both directions: the sibling hint has to appear when
the kind IS a sibling's, and stay away when the kind is nobody's. A hint that
fires unconditionally is worse than none, because it is then a lie.
"""

from __future__ import annotations

from ai_hats_rack.registry import LinkKind, UnknownLinkKindError, _build_registry


def _registry(backlog: str, *names: str):
    return _build_registry([LinkKind(name=n) for n in names], backlog)


def test_the_refusal_names_the_backlog_whose_kinds_it_lists():
    """Without the owner the set reads as absolute — the whole defect."""
    with_owner = str(UnknownLinkKindError("related_tasks", ["related"], "tasks"))
    assert "'tasks' backlog" in with_owner
    assert "related" in with_owner


def test_an_unnamed_backlog_keeps_the_plain_wording():
    """`load_registry` has no backlog to name; it must not print an empty quote."""
    plain = str(UnknownLinkKindError("wat", ["related"]))
    assert "backlog" not in plain
    assert "''" not in plain


def test_the_hint_names_the_sibling_that_declares_the_kind():
    hinted = str(UnknownLinkKindError("related_tasks", ["related"], "tasks", "proposals"))
    assert "'proposals' backlog" in hinted


def test_no_hint_when_the_kind_belongs_to_nobody():
    """The other direction: an unconditional hint would be a false statement."""
    unhinted = str(UnknownLinkKindError("nosuchkind", ["related"], "tasks"))
    assert "proposals" not in unhinted
    assert "is a kind of" not in unhinted


def test_require_carries_the_backlog_into_the_error():
    """The registry raises kind-blind but not owner-blind."""
    registry = _registry("tasks", "related", "parent_task")
    try:
        registry.require("related_tasks")
    except UnknownLinkKindError as exc:
        assert exc.backlog == "tasks"
        assert exc.elsewhere == ""
        assert "'tasks' backlog" in str(exc)
    else:  # pragma: no cover - the call above must raise
        raise AssertionError("require() accepted an undeclared kind")


def test_a_registry_built_without_a_backlog_still_works():
    """Threading the name through must not make it required."""
    registry = _build_registry([LinkKind(name="related")])
    assert registry.backlog == ""
    assert registry.get("related") is not None
