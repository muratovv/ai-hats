"""HATS-1385: the open-PROP digest the Phase 2 judge preamble carries.

The judge who missed a 136-card inbox was handed only the Phase 1 draft. These
cover the digest that now rides along, and the auto-noise classifier it counts
with — whose every single-clause shortcut has a measured victim on the live
inbox (PROP-107, PROP-080, PROP-119).
"""

from __future__ import annotations

from pathlib import Path

from ai_hats.cli.reflect import (
    _build_inbox_digest,
    _fill_inbox_digest,
    _is_auto_filed,
)
from ai_hats.rack_workspace import PropView


def _prop(pid: str, title: str, *, target: str = "built-in", votes: int = 0) -> PropView:
    return PropView(
        id=pid,
        title=title,
        status="open",
        category="process",
        target=target,
        description="d",
        rationale="r",
        votes=tuple({"session_id": f"s{i}"} for i in range(votes)),
        related_hypotheses=(),
        failed_session_id="",
    )


AUTO = _prop("PROP-170", "harness incident: 20260731-1", target="harness-incident")


# ----- the classifier: each clause has a victim without the others -----------


def test_auto_shaped_and_unvoted_is_noise():
    assert _is_auto_filed(AUTO) is True


def test_hand_written_title_under_an_auto_target_is_kept():
    """PROP-107 shape: a card *about* the reviewer wears the reviewer target."""
    card = _prop(
        "PROP-107",
        "session-reviewer cannot run rack/ai-hats CLI to materialize verdicts",
        target="session-reviewer",
        votes=5,
    )
    assert _is_auto_filed(card) is False


def test_auto_shaped_but_seconded_is_kept():
    """A vote means a reviewer independently stood behind the incident."""
    card = _prop(
        "PROP-021",
        "harness incident: 20260605-1",
        target="harness-incident",
        votes=3,
    )
    assert _is_auto_filed(card) is False


# ----- the digest ------------------------------------------------------------


def _digest(monkeypatch, props: list[PropView]) -> str:
    monkeypatch.setattr("ai_hats.cli.reflect.rack_workspace", lambda _d: object())
    monkeypatch.setattr("ai_hats.cli.reflect.open_proposals", lambda _ws: props)
    return _build_inbox_digest(Path("/nowhere"))


def test_digest_splits_noise_from_signal(monkeypatch):
    props = [AUTO, _prop("PROP-010", "maintainer omits --parent-task", votes=2)]
    out = _digest(monkeypatch, props)
    assert "## PROP inbox — 2 open" in out
    assert "auto-filed, unvoted: 1" in out
    assert "everything else: 1" in out


def test_digest_ranks_on_both_axes(monkeypatch):
    """Votes alone buries pre-HATS-1397 cards, so age is a second listing."""
    old_quiet = _prop("PROP-010", "oldest, silent", votes=0)
    young_loud = _prop("PROP-160", "newest, popular", votes=9)
    out = _digest(monkeypatch, [old_quiet, young_loud])
    votes_block, _, age_block = out.partition("Longest open")
    assert "PROP-160" in votes_block.partition("Leaders by votes")[2]
    assert age_block.index("PROP-010") < age_block.index("PROP-160")


def test_digest_on_empty_inbox_says_so(monkeypatch):
    """An empty inbox is a statement, not an absence — silence reads as 'unread'."""
    out = _digest(monkeypatch, [])
    assert "0 open proposals" in out
    assert out.strip()


def test_digest_stays_compact(monkeypatch):
    """The full dump measured ~126K chars on 147 cards; that is what was skipped."""
    props = [_prop(f"PROP-{i:03d}", f"proposal number {i}", votes=i % 4) for i in range(147)]
    assert len(_digest(monkeypatch, props)) < 2000


# ----- placeholder substitution ---------------------------------------------


def test_fill_substitutes_the_placeholder(monkeypatch):
    monkeypatch.setattr("ai_hats.cli.reflect._build_inbox_digest", lambda _d: "DIGEST\n")
    out = _fill_inbox_digest("before\n{inbox_digest}\nafter", Path("/nowhere"))
    assert out == "before\nDIGEST\n\nafter"


def test_fill_without_placeholder_appends_and_warns(monkeypatch, capsys):
    """An overridden injection must not silently drop the inbox — that is the bug."""
    monkeypatch.setattr("ai_hats.cli.reflect._build_inbox_digest", lambda _d: "DIGEST\n")
    out = _fill_inbox_digest("a preamble with no placeholder", Path("/nowhere"))
    assert "DIGEST" in out
    assert "no {inbox_digest} placeholder" in capsys.readouterr().out


def test_shipped_injection_carries_the_placeholder():
    """Regression guard: the built-in must never lose the slot it declares."""
    from ai_hats.assembler import Assembler

    path = Assembler(Path.cwd()).resolver.resolve_injection("reflect-hypothesis-interactive")
    assert path is not None
    assert "{inbox_digest}" in path.read_text()
