"""The HATS-1324 acceptance measurement: how much `--grep field:` improves
precision on the one intent the default haystack cannot express — "find the
cards of epic 126x".

The fixture is a distillate, not a synthetic guess: its proportion is lifted
from a 2026-07-29 measurement over the live 860-card backlog, where
`--grep "HATS-126"` returned 21 rows and *none* of them was one of the 10 cards
whose id actually matches. Numbers are asserted in both directions — a bare
count would stay green on an empty result.
"""

from __future__ import annotations

import pytest

from ai_hats_rack.linked import EmptyGrepPatternError, scan_cards
from ai_hats_rack.models import TaskCard

#: Cards whose *id* matches `T-126` — the 10 the searcher wants.
TARGETS = 10
#: Cards that merely *mention* `T-126x` in prose — the 21 the searcher gets.
MENTIONS = 21


def _card(tasks_dir, task_id, **fields):
    path = tasks_dir / task_id / "task.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    TaskCard(id=task_id, **fields).save(path)


@pytest.fixture
def distillate(tasks_dir):
    """10 id-matching cards that never name themselves + 21 that name them.

    Both halves mirror the live corpus: an epic's own cards carry step titles
    ("S3 — Unmount …"), while the cards *citing* them are the ones that spell
    the id out.
    """
    for n in range(TARGETS):
        _card(
            tasks_dir,
            f"T-126{n}",
            title=f"S{n} — cutover step {n}",
            description="Retire the legacy surface; rack becomes the only one.",
        )
    for n in range(MENTIONS):
        _card(
            tasks_dir,
            f"T-2{n:03d}",
            title=f"Follow-up {n}",
            description=f"Split out of T-126{n % TARGETS}; see that card for the ruling.",
        )
    return tasks_dir


def _precision(rows, prefix="T-126"):
    """Share of returned rows that are actually what the searcher asked for."""
    if not rows:
        return 0.0
    return sum(1 for r in rows if r.id.startswith(prefix)) / len(rows)


def test_default_haystack_cannot_express_an_id_search(distillate):
    # BEFORE. `id` is not in the haystack, so every row is a mention.
    rows = scan_cards(distillate, grep="T-126")
    assert len(rows) == MENTIONS
    assert sum(1 for r in rows if r.id.startswith("T-126")) == 0
    assert _precision(rows) == 0.0


def test_field_targeted_grep_reaches_full_precision(distillate):
    # AFTER. Same needle, haystack narrowed to the field that carries it.
    rows = scan_cards(distillate, grep="id:T-126")
    assert len(rows) == TARGETS
    assert sum(1 for r in rows if r.id.startswith("T-126")) == TARGETS
    assert _precision(rows) == 1.0


def test_the_delta_is_the_whole_point(distillate):
    """One assertion a reviewer can read without reconstructing the two above."""
    before = _precision(scan_cards(distillate, grep="T-126"))
    after = _precision(scan_cards(distillate, grep="id:T-126"))
    assert (before, after) == (0.0, 1.0)


def test_bare_grep_is_untouched(distillate):
    """Back-compat guard: no prefix, no behaviour change."""
    rows = scan_cards(distillate, grep="cutover step")
    assert [r.id for r in rows] == [f"T-126{n}" for n in range(TARGETS)]
    assert scan_cards(distillate, grep="CUTOVER STEP") == rows  # still case-insensitive


def test_unknown_prefix_stays_a_literal(tasks_dir):
    """`path:line` refs outnumber field-qualified needles in real card text, so
    only a *known* field switches modes — an unknown one is just a substring."""
    _card(tasks_dir, "T-1", title="Seam", description="see linked.py:452 for the filter")
    _card(tasks_dir, "T-2", title="Other", description="no reference here")
    assert [r.id for r in scan_cards(tasks_dir, grep="linked.py:452")] == ["T-1"]


def test_field_prefix_without_a_pattern_is_refused(tasks_dir):
    _card(tasks_dir, "T-1", title="Seam")
    with pytest.raises(EmptyGrepPatternError) as excinfo:
        scan_cards(tasks_dir, grep="id:")
    assert excinfo.value.field == "id"


def test_exact_filters_are_not_shadowed_by_grep_fields(tasks_dir):
    """`tags` / `state` / `parent_task` stay out of the field set on purpose —
    `--tag` / `--state` / `--parent` already match them exactly."""
    _card(tasks_dir, "T-1", title="A", description="state:execute", state="brainstorm")
    rows = scan_cards(tasks_dir, grep="state:execute")
    assert [r.id for r in rows] == ["T-1"]  # literal, not a state filter
