"""Ported group 10 (incidents §4): per-section plan gate + scaffold
(HATS-635/621/794/328) on the rack extension API."""

from __future__ import annotations

import pytest

from ai_hats_rack.dispatch import AbortOperation, OperationAborted
from ai_hats_rack.extensions import (
    DEFAULT_PLAN_SECTIONS,
    Section,
    load_sections,
    render_scaffold,
    standalone_extensions,
    unfilled_sections,
)

from rack_testkit import StubSubscriber, in_lock, make_kernel, walk

ALL_REQUIRED = [s.name for s in DEFAULT_PLAN_SECTIONS if s.required]
SCAFFOLD = render_scaffold(DEFAULT_PLAN_SECTIONS)

_FILLED_PLAN = (
    "# Plan for T-1: Probe\n\n"
    "## Requirements\nShip it.\n\n"
    "## Scope & Out-of-scope\nIn: gate. Out: skill.\n\n"
    "## Steps\n- [x] do thing\n\n"
    "## Verification Protocol\npytest -q\n"
)


# ---------------------------------------------------------------------------
# unfilled_sections — the pure per-section check (HATS-635)
# ---------------------------------------------------------------------------


def test_all_sections_filled_returns_empty():
    assert unfilled_sections(_FILLED_PLAN) == []


def test_blank_scaffold_flags_every_required_section():
    text = SCAFFOLD.format(task_id="T-1", title="Probe")
    assert unfilled_sections(text) == ALL_REQUIRED


def test_partial_plan_names_only_the_empty_sections():
    text = (
        "# Plan for T-1: Probe\n\n"
        "## Requirements\nOnly this one is filled.\n\n"
        "## Scope & Out-of-scope\n\n"
        "## Steps\n\n"
        "## Verification Protocol\n\n"
    )
    assert unfilled_sections(text) == [
        "Scope & Out-of-scope",
        "Steps",
        "Verification Protocol",
    ]


def test_whitespace_only_body_counts_as_unfilled():
    text = (
        "# Plan for T-1: Probe\n\n"
        "## Requirements\n   \n\t\n\n"
        "## Scope & Out-of-scope\nx\n\n"
        "## Steps\nx\n\n"
        "## Verification Protocol\nx\n"
    )
    assert unfilled_sections(text) == ["Requirements"]


def test_missing_heading_counts_as_unfilled():
    # Free-form plan with no headings: the old byte-equality check passed
    # this ("not the verbatim scaffold") — the per-section gate must flag all.
    assert unfilled_sections("# Plan\n\nsome free text, no sections\n") == ALL_REQUIRED


def test_subheadings_inside_a_section_do_not_break_fill():
    text = (
        "# Plan for T-1: Probe\n\n"
        "## Requirements\n### detail\nnested content\n\n"
        "## Scope & Out-of-scope\nx\n\n"
        "## Steps\nx\n\n"
        "## Verification Protocol\nx\n"
    )
    assert unfilled_sections(text) == []


def test_missing_plan_file_flags_every_required_section():
    assert unfilled_sections(None) == ALL_REQUIRED


# ---------------------------------------------------------------------------
# Conditional "Approach & counter" value-counter stage (HATS-621)
# ---------------------------------------------------------------------------


def test_approach_and_counter_is_a_conditional_section():
    by_name = {s.name: s for s in DEFAULT_PLAN_SECTIONS}
    assert "Approach & counter" in by_name
    assert by_name["Approach & counter"].required is False


def test_approach_and_counter_sits_after_requirements_before_scope():
    names = [s.name for s in DEFAULT_PLAN_SECTIONS]
    assert names.index("Approach & counter") == names.index("Requirements") + 1
    assert names.index("Approach & counter") < names.index("Scope & Out-of-scope")


def test_blank_scaffold_does_not_flag_the_optional_section():
    text = SCAFFOLD.format(task_id="T-1", title="Probe")
    assert "Approach & counter" not in unfilled_sections(text)


def test_all_required_filled_with_empty_optional_passes():
    text = (
        "# Plan for T-1: Probe\n\n"
        "## Requirements\nShip the value-counter stage.\n\n"
        "## Approach & counter\n\n"
        "## Scope & Out-of-scope\nIn: section. Out: role.\n\n"
        "## Steps\n- [x] do thing\n\n"
        "## Verification Protocol\npytest -q\n"
    )
    assert unfilled_sections(text) == []


# ---------------------------------------------------------------------------
# Config-driven catalog: one source for scaffold AND gate (HATS-635)
# ---------------------------------------------------------------------------


def test_load_sections_from_yaml(tmp_path):
    catalog = tmp_path / "sections.yaml"
    catalog.write_text("- Requirements\n- {name: Risks, required: false}\n- Steps\n")
    sections = load_sections(catalog)
    assert sections == (
        Section("Requirements"),
        Section("Risks", required=False),
        Section("Steps"),
    )


def test_custom_catalog_drives_both_scaffold_and_gate(tasks_dir, cwd):
    """Never-drift on the declarative path (HATS-1043): one custom catalog feeds
    BOTH the scaffold and the gate, wired from the definition's on_enter slots."""
    sections = (Section("Goal"), Section("Rollback plan"))
    kernel = make_kernel(tasks_dir, subscribers=standalone_extensions(tasks_dir, sections))
    kernel.create(actor="test", caller_cwd=cwd, task_id="T-1", title="Custom")
    walk(kernel, "T-1", "plan", cwd=cwd)
    scaffolded = (tasks_dir / "T-1" / "plan.md").read_text()
    assert "## Goal" in scaffolded and "## Rollback plan" in scaffolded

    with pytest.raises(OperationAborted) as exc_info:
        walk(kernel, "T-1", "execute", cwd=cwd)
    assert "Goal" in exc_info.value.reason and "Rollback plan" in exc_info.value.reason


# ---------------------------------------------------------------------------
# Scaffold on transition → plan
# ---------------------------------------------------------------------------


@pytest.fixture
def kit(tasks_dir):
    return make_kernel(tasks_dir, subscribers=standalone_extensions(tasks_dir))


def _create(kernel, cwd, task_id="T-1", title="Probe"):
    return kernel.create(actor="test", caller_cwd=cwd, task_id=task_id, title=title).task


def test_transition_plan_writes_scaffold(kit, tasks_dir, cwd):
    _create(kit, cwd, title="Test scaffold")
    walk(kit, "T-1", "plan", cwd=cwd)
    dst = tasks_dir / "T-1" / "plan.md"
    assert dst.read_text() == SCAFFOLD.format(task_id="T-1", title="Test scaffold")
    assert "## Steps" in dst.read_text()


def test_plan_scaffold_not_overwritten_and_noted(kit, tasks_dir, cwd):
    """Idempotence + the supervisor-decided preservation note."""
    _create(kit, cwd)
    plan_path = tasks_dir / "T-1" / "plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text("# My custom plan")

    walk(kit, "T-1", "plan", cwd=cwd)

    assert plan_path.read_text() == "# My custom plan"
    card = kit.get("T-1")
    assert any("plan.md already exists — preserved" in e.message for e in card.work_log)


# ---------------------------------------------------------------------------
# Gate on transition → execute
# ---------------------------------------------------------------------------


def test_transition_execute_blocks_on_empty_scaffold(kit, tasks_dir, cwd):
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)

    with pytest.raises(OperationAborted) as exc_info:
        walk(kit, "T-1", "execute", cwd=cwd)
    # The abort reason must NAME every empty required section (HATS-635).
    for name in ALL_REQUIRED:
        assert name in exc_info.value.reason
    assert exc_info.value.subscriber == "plan-gate"
    assert kit.get("T-1").state == "plan"  # nothing persisted


def test_partial_plan_blocks_and_names_only_the_empty_sections(kit, tasks_dir, cwd):
    """Fail-under-revert vs byte-equality: ANY content used to pass the old
    check; the per-section gate names exactly the empty ones."""
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(
        "# Plan for T-1: Probe\n\n"
        "## Requirements\nOnly this section is filled.\n\n"
        "## Scope & Out-of-scope\n\n"
        "## Steps\n\n"
        "## Verification Protocol\n\n"
    )
    with pytest.raises(OperationAborted) as exc_info:
        walk(kit, "T-1", "execute", cwd=cwd)
    reason = exc_info.value.reason
    for name in ("Scope & Out-of-scope", "Steps", "Verification Protocol"):
        assert name in reason
    assert "Requirements," not in reason  # the filled one is not listed


def test_the_content_refusal_says_the_question_was_asked_for_nothing(kit, tasks_dir, cwd):
    """The prompt goes up before the plan is read — the hook cannot judge the
    sections without becoming a second plan-gate (HATS-1253). So the refusal
    that DOES judge them says the answer was spent on a move that never
    happened, instead of leaving the agent to wonder (HATS-1642 review)."""
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)

    with pytest.raises(OperationAborted) as exc_info:
        walk(kit, "T-1", "execute", cwd=cwd)

    assert "asked again" in exc_info.value.reason, exc_info.value.reason


def test_a_move_that_never_raised_a_question_is_not_told_one_was_wasted(kit, tasks_dir, cwd):
    """Control: only `plan → execute` raises the prompt, so only it gets the note."""
    _create(kit, cwd)
    walk(kit, "T-1", "plan", "blocked", cwd=cwd)

    with pytest.raises(OperationAborted) as exc_info:
        walk(kit, "T-1", "execute", cwd=cwd)

    assert "Empty required section" in exc_info.value.reason
    assert "asked again" not in exc_info.value.reason, exc_info.value.reason


def test_transition_execute_proceeds_on_populated_plan(kit, tasks_dir, cwd):
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    walk(kit, "T-1", "execute", cwd=cwd)
    assert kit.get("T-1").state == "execute"


def test_gate_abort_does_not_half_apply_final_state(kit, tasks_dir, cwd):
    """HATS-723 heir: resolution/final_state ride the same lock window — an
    aborted transition persists zero bytes."""
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)
    before = (tasks_dir / "T-1" / "task.yaml").read_bytes()
    with pytest.raises(OperationAborted):
        kit.transition(
            "T-1", "execute", actor="test", caller_cwd=cwd, final_state="should not persist"
        )
    assert (tasks_dir / "T-1" / "task.yaml").read_bytes() == before
    assert kit.get("T-1").final_state == ""


def test_epic_execute_skips_plan_gate(kit, tasks_dir, cwd):
    """HATS-794: an epic with an unfilled plan still enters execute; a
    childless task with the same empty scaffold is still gated."""
    _create(kit, cwd, task_id="T-1", title="Epic")
    kit.create(actor="test", caller_cwd=cwd, task_id="T-2", title="Child", parent_task="T-1")
    walk(kit, "T-1", "plan", "execute", cwd=cwd)  # empty scaffold, epic → waived
    epic = kit.get("T-1")
    assert epic.state == "execute"
    assert any("Epic → execute (tracker)" in e.message for e in epic.work_log)

    _create(kit, cwd, task_id="T-3", title="Solo")  # childless → still gated
    walk(kit, "T-3", "plan", cwd=cwd)
    with pytest.raises(OperationAborted):
        walk(kit, "T-3", "execute", cwd=cwd)


def test_reopen_done_to_execute_is_not_gated(kit, tasks_dir, cwd):
    """HATS-328: the plan passed once on the original execute — reopen is not
    re-gated even if plan.md has been emptied since."""
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    walk(kit, "T-1", "execute", "document", "review", "done", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(SCAFFOLD.format(task_id="T-1", title="Probe"))

    walk(kit, "T-1", "execute", cwd=cwd)  # must not raise
    reopened = kit.get("T-1")
    assert reopened.state == "execute"
    assert reopened.completed_at == ""
    assert any("Reopened from done" in e.message for e in reopened.work_log)


def test_forced_execute_is_still_gated(kit, tasks_dir, cwd):
    """Parity with the tracker: force relaxes the FSM arrow, not the gate —
    a forced → execute with an empty plan is refused (HATS-518 class)."""
    _create(kit, cwd)
    with pytest.raises(OperationAborted):
        kit.transition(
            "T-1", "execute", actor="test", caller_cwd=cwd, force=True, reason="skip plan"
        )
    assert kit.get("T-1").state == "brainstorm"


# ---------------------------------------------------------------------------
# PlanConsentExtension — supervisor approval gate (AI_HATS_PLAN_ACK=1)
# ---------------------------------------------------------------------------


def _consent_definition(tmp_path):
    """The packaged contract PLUS ``plan-consent`` on ``execute``.

    HATS-1682 took the handler out of the packaged default — where consent is
    required became the ROLE's declaration, and this file can say nothing about
    the wt half of the same question. The rack still SHIPS the handler for a
    backlog that declares it, and that is what these tests exercise.
    """
    from importlib import resources

    import yaml
    from ai_hats_rack.definition import load_backlog

    data = yaml.safe_load(resources.files("ai_hats_rack").joinpath("backlog.yaml").read_text())
    for state in data["fsm"]["states"]:
        if state["name"] == "execute":
            state.setdefault("on_enter", []).append({"name": "plan-consent", "priority": 11})
    path = tmp_path / "consent-backlog.yaml"
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    return load_backlog(path)


@pytest.fixture
def consent_kit(tasks_dir, tmp_path):
    return make_kernel(
        tasks_dir,
        subscribers=standalone_extensions(tasks_dir, definition=_consent_definition(tmp_path)),
    )


def test_plan_consent_blocks_without_ack_env(consent_kit, tasks_dir, cwd, monkeypatch):
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
    _create(consent_kit, cwd)
    walk(consent_kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)

    with pytest.raises(OperationAborted) as exc_info:
        walk(consent_kit, "T-1", "execute", cwd=cwd)

    assert exc_info.value.subscriber == "plan-consent"
    assert "supervisor approval" in exc_info.value.reason
    assert "AI_HATS_PLAN_ACK=1" in exc_info.value.reason
    assert consent_kit.get("T-1").state == "plan"


def test_plan_consent_passes_with_ack_env(consent_kit, tasks_dir, cwd, monkeypatch):
    monkeypatch.setenv("AI_HATS_PLAN_ACK", "1")
    _create(consent_kit, cwd)
    walk(consent_kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)

    walk(consent_kit, "T-1", "execute", cwd=cwd)
    assert consent_kit.get("T-1").state == "execute"


def test_plan_consent_skipped_on_reopen_and_epic(consent_kit, tasks_dir, cwd, monkeypatch):
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
    _create(consent_kit, cwd, task_id="T-1", title="Epic")
    consent_kit.create(
        actor="test", caller_cwd=cwd, task_id="T-2", title="Child", parent_task="T-1"
    )

    # Epic execute skips plan-consent
    walk(consent_kit, "T-1", "plan", "execute", cwd=cwd)
    assert consent_kit.get("T-1").state == "execute"


class _Booth:
    """Stand-in for the integrator's ticket store: one card, one use, and asking
    about a ticket is not the same act as spending it."""

    def __init__(self, *task_ids: str) -> None:
        self.unspent = set(task_ids)
        self.asked: list[str] = []
        self.spent: list[str] = []

    def peek(self, task_id: str) -> bool:
        self.asked.append(task_id)
        return task_id in self.unspent

    def spend(self, task_id: str) -> bool:
        if task_id not in self.unspent:
            return False
        self.unspent.discard(task_id)
        self.spent.append(task_id)
        return True


def _kit_with_booth(tasks_dir, booth, extra=(), *, definition=None):
    """The seam the integrator fills (HATS-1642): the rack cannot import the
    library that writes the ticket, so the store arrives through the factory."""
    from ai_hats_rack.composition import compose_subscribers, stock_factories
    from ai_hats_rack.definition import load_backlog
    from ai_hats_rack.extensions import PlanConsentExtension

    gate = PlanConsentExtension(tickets=booth)
    factories = {**stock_factories(), "plan-consent": lambda d, c, cfg: gate}
    subs = compose_subscribers(definition or load_backlog(), tasks_dir, factories)
    return make_kernel(tasks_dir, subscribers=[*subs, gate.spender(), *extra])


def _planned(kit, tasks_dir, cwd):
    _create(kit, cwd)
    walk(kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)


def test_plan_consent_passes_when_the_seam_spends_a_ticket(tasks_dir, cwd, tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
    booth = _Booth("T-1")
    kit = _kit_with_booth(tasks_dir, booth, definition=_consent_definition(tmp_path))
    _planned(kit, tasks_dir, cwd)

    walk(kit, "T-1", "execute", cwd=cwd)

    assert kit.get("T-1").state == "execute"
    assert booth.asked == ["T-1"], "the gate did not ask about THIS card"
    assert booth.spent == ["T-1"], "the ticket outlived the transition it paid for"


def test_a_transaction_that_aborts_later_does_not_eat_the_ticket(
    tasks_dir, cwd, tmp_path, monkeypatch
):
    """The gate runs at 11; ownership (20) and the worktree (30) can still abort.

    Spending there would burn a click on a transition that never happened, and
    the supervisor has no way to get it back (HATS-1642 review).
    """
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
    booth = _Booth("T-1")

    def _abort(_ctx):
        raise AbortOperation("the worktree could not be created")

    later = StubSubscriber("late-abort", [in_lock("plan->execute", 20)], _abort)
    kit = _kit_with_booth(tasks_dir, booth, extra=[later], definition=_consent_definition(tmp_path))
    _planned(kit, tasks_dir, cwd)

    with pytest.raises(OperationAborted):
        walk(kit, "T-1", "execute", cwd=cwd)

    assert kit.get("T-1").state == "plan"
    assert booth.asked == ["T-1"], "the gate never looked at the ticket"
    assert booth.spent == [], "a rolled-back transition ate the supervisor's click"


def test_plan_consent_blocks_when_the_seam_holds_no_ticket(tasks_dir, cwd, tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
    booth = _Booth()  # the supervisor was never asked
    kit = _kit_with_booth(tasks_dir, booth, definition=_consent_definition(tmp_path))
    _planned(kit, tasks_dir, cwd)

    with pytest.raises(OperationAborted) as exc_info:
        walk(kit, "T-1", "execute", cwd=cwd)

    assert exc_info.value.subscriber == "plan-consent"
    assert booth.asked == ["T-1"]
    assert kit.get("T-1").state == "plan"


def test_the_env_ack_never_spends_a_ticket(tasks_dir, cwd, tmp_path, monkeypatch):
    """Pre-approved in the launching environment: no question is left to ask, so
    a ticket the supervisor may still hold stays unspent."""
    monkeypatch.setenv("AI_HATS_PLAN_ACK", "1")
    booth = _Booth("T-1")
    kit = _kit_with_booth(tasks_dir, booth, definition=_consent_definition(tmp_path))
    _planned(kit, tasks_dir, cwd)

    walk(kit, "T-1", "execute", cwd=cwd)

    assert kit.get("T-1").state == "execute"
    assert booth.asked == [], "the env channel burned a ticket it did not need"


def test_plan_consent_is_not_skipped_on_force(consent_kit, tasks_dir, cwd, monkeypatch):
    """HATS-1682: consent is not a property of the command, so no flag drops it.

    Renamed from `test_plan_consent_skipped_on_force`, which asserted the
    opposite AND took the `kit` fixture — one that wires no `plan-consent` at
    all, so it passed no matter what the handler did.
    """
    monkeypatch.delenv("AI_HATS_PLAN_ACK", raising=False)
    _create(consent_kit, cwd)
    walk(consent_kit, "T-1", "plan", cwd=cwd)
    (tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)

    with pytest.raises(OperationAborted) as exc_info:
        consent_kit.transition(
            "T-1", "execute", actor="test", caller_cwd=cwd, force=True, reason="forced override"
        )

    assert exc_info.value.subscriber == "plan-consent"
    assert consent_kit.get("T-1").state == "plan"


# ---------------------------------------------------------------------------
# merge_sections — the consumer extension channel (HATS-1023)
# ---------------------------------------------------------------------------


def test_merge_sections_appends_and_dedupes_base_wins():
    from ai_hats_rack.extensions import merge_sections

    base = (Section(name="Requirements"), Section(name="Steps"))
    extras = (
        Section(name="Rollback plan"),
        Section(name="Requirements", required=False),  # cannot weaken a stock section
        Section(name="Rollback plan", required=False),  # first extra occurrence wins
    )
    merged = merge_sections(base, extras)
    assert [s.name for s in merged] == ["Requirements", "Steps", "Rollback plan"]
    assert merged[0].required is True
    assert merged[2].required is True


def test_merge_sections_feeds_gate_and_scaffold_consistently():
    """The merged catalog drives BOTH surfaces (HATS-635 never-drift)."""
    from ai_hats_rack.extensions import merge_sections

    merged = merge_sections(DEFAULT_PLAN_SECTIONS, (Section(name="Rollback plan"),))
    assert "## Rollback plan" in render_scaffold(merged)
    assert "Rollback plan" in unfilled_sections(_FILLED_PLAN, merged)
