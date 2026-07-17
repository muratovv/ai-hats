"""HATS-1023 — HookRunnerExtension on the rack kernel: lexicographic order,
abort + reason channel, mandatory timeout, env contract, fail-CLOSED on a
degraded managed surface (fix #2), subscriber ordering (fix #1), and the
demo-consumer end-to-end (declaration → materialization → gate firing)."""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

import pytest

from ai_hats_rack import OperationAborted
from ai_hats_rack.dispatch import Phase

from ai_hats.lifecycle_hooks import lifecycle_hooks_dir, materialize_lifecycle_hooks
from ai_hats.rack_consumers import HookRunnerExtension, consumer_plan_sections, consumer_subscribers
from ai_hats.rack_wiring import build_rack_kernel

from tests.test_lifecycle_hooks import _skill  # shared fixture-skill builder

pytestmark = pytest.mark.integration

_FILLED_PLAN = (
    "# Plan\n\n## Requirements\nrack.\n\n## Scope & Out-of-scope\nin/out\n\n"
    "## Steps\n- [ ] do\n\n## Verification Protocol\npytest\n"
)


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603 — fixed argv, test helper
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, check=True
    )


@pytest.fixture
def project(tmp_path, monkeypatch):
    monkeypatch.delenv("AI_HATS_SESSION_ID", raising=False)
    p = tmp_path / "project"
    p.mkdir()
    _git(p, "init", "-b", "master")
    _git(p, "config", "user.email", "t@t.t")
    _git(p, "config", "user.name", "t")
    (p / "README.md").write_text("# t")
    _git(p, "add", ".")
    _git(p, "-c", "commit.gpgsign=false", "commit", "-m", "init")
    (p / ".agent").mkdir()
    return p


@pytest.fixture
def lib(tmp_path):
    return tmp_path / "lib"


def _kernel(project: Path, **kwargs):
    tasks_dir = project / ".agent" / "ai-hats" / "tracker" / "backlog" / "tasks"
    kwargs.setdefault(
        "extra_subscribers",
        consumer_subscribers(project, tasks_dir=tasks_dir, timeout=kwargs.pop("timeout", 30.0)),
    )
    return build_rack_kernel(
        project,
        tasks_dir=tasks_dir,
        state_md_path=project / ".agent" / "ai-hats" / "tracker" / "STATE.md",
        prefix="T",
        **kwargs,
    )


def _materialize(project: Path, lib: Path) -> None:
    materialize_lifecycle_hooks(project, [lib])


# ----- execution semantics -----


def test_hooks_run_in_lexicographic_order(project, lib):
    log = project / "order.log"
    body = f'#!/usr/bin/env bash\nbasename "$0" >> "{log}"\nexit 0\n'
    _skill(lib, "aaa", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _skill(lib, "bbb", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert log.read_text().splitlines() == ["aaa-h.sh", "bbb-h.sh"]


def test_failing_hook_aborts_with_its_output_as_reason(project, lib):
    body = "#!/usr/bin/env bash\necho 'summary.md is missing — write it first'\nexit 3\n"
    _skill(lib, "gate", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    card = kernel.tasks_dir / "T-1" / "task.yaml"
    before = card.read_bytes()

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "plan", actor="test", caller_cwd=project)

    assert exc_info.value.subscriber == "hook-runner"
    assert "summary.md is missing" in exc_info.value.reason
    assert "exit 3" in exc_info.value.reason
    assert card.read_bytes() == before, "an aborted transition persists nothing"
    assert kernel.get("T-1").state == "brainstorm"


def test_hanging_hook_times_out_loud(project, lib):
    body = "#!/usr/bin/env bash\nsleep 30\n"
    _skill(lib, "hang", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project, timeout=0.5)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")

    start = time.monotonic()
    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert time.monotonic() - start < 10, "the timeout must fire, not wait for the hook"
    assert "timed out" in exc_info.value.reason
    assert kernel.get("T-1").state == "brainstorm"


def test_env_contract(project, lib):
    dump = project / "env.dump"
    body = f'#!/usr/bin/env bash\nenv | grep "^AI_HATS_HOOK_" | sort > "{dump}"\nexit 0\n'
    _skill(lib, "probe", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="tester", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="tester", caller_cwd=project)

    got = dict(line.split("=", 1) for line in dump.read_text().splitlines())
    assert got == {
        "AI_HATS_HOOK_EVENT": "brainstorm--plan",
        "AI_HATS_HOOK_TASK_FILE": str(kernel.tasks_dir / "T-1" / "task.yaml"),
        "AI_HATS_HOOK_FROM": "brainstorm",
        "AI_HATS_HOOK_TO": "plan",
        "AI_HATS_HOOK_IS_EPIC": "0",
        "AI_HATS_HOOK_FORCE": "0",
        "AI_HATS_HOOK_REASON": "",
        "AI_HATS_HOOK_ACTOR": "tester",
    }


def test_env_contract_forced_transition(project, lib):
    """Force rides through `transition` (HATS-518) — hooks fire and see it."""
    dump = project / "env.dump"
    body = f'#!/usr/bin/env bash\nenv | grep "^AI_HATS_HOOK_" | sort > "{dump}"\nexit 0\n'
    _skill(lib, "probe", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition(
        "T-1", "plan", actor="test", caller_cwd=project, force=True, reason="ops override"
    )
    got = dict(line.split("=", 1) for line in dump.read_text().splitlines())
    assert got["AI_HATS_HOOK_FORCE"] == "1"
    assert got["AI_HATS_HOOK_REASON"] == "ops override"


def test_event_without_hooks_passes(project, lib):
    _skill(lib, "elsewhere", hooks={"document--review": ["h.sh"]})
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)  # must not raise
    assert kernel.get("T-1").state == "plan"


def test_no_materialization_at_all_passes(project):
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert kernel.get("T-1").state == "plan"


# ----- fail-CLOSED (fix #2): manifest expects, disk degraded -----


def test_missing_event_dir_is_fail_closed(project, lib):
    _skill(lib, "gate", hooks={"brainstorm--plan": ["h.sh"]})
    _materialize(project, lib)
    shutil.rmtree(lifecycle_hooks_dir(project) / "brainstorm--plan.d")
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert "ai-hats self init" in exc_info.value.reason
    assert kernel.get("T-1").state == "brainstorm"


def test_non_executable_managed_hook_is_fail_closed(project, lib):
    _skill(lib, "gate", hooks={"brainstorm--plan": ["h.sh"]})
    _materialize(project, lib)
    dest = lifecycle_hooks_dir(project) / "brainstorm--plan.d" / "gate-h.sh"
    dest.chmod(0o644)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert "missing or non-executable" in exc_info.value.reason
    assert "ai-hats self init" in exc_info.value.reason


def test_unmanaged_non_executable_file_aborts_not_skips(project, lib):
    """The dispatcher.sh silent `[[ -x ]] || continue` is the HYP-078 hole —
    the lifecycle runner refuses to skip a hook that cannot run."""
    _skill(lib, "gate", hooks={"brainstorm--plan": ["h.sh"]})
    _materialize(project, lib)
    stray = lifecycle_hooks_dir(project) / "brainstorm--plan.d" / "manual-drop.sh"
    stray.write_text("#!/usr/bin/env bash\nexit 0\n")
    stray.chmod(0o644)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert "manual-drop.sh" in exc_info.value.reason
    assert "not" in exc_info.value.reason and "executable" in exc_info.value.reason


# ----- ordering (fix #1): consumer abort leaves no resource side effects -----


def test_runner_sits_between_gate_and_claim(project):
    kernel = _kernel(project)
    names = [
        s.name for s in kernel._dispatcher.subscribers_for("edge:plan--execute", Phase.IN_LOCK)
    ]
    assert names == ["ownership-single-slot", "plan-gate", "hook-runner", "ownership", "worktree"]


def test_consumer_abort_leaves_no_worktree(project, lib):
    from ai_hats.paths import worktrees_dir
    from ai_hats_wt import WorktreeManager

    body = "#!/usr/bin/env bash\necho 'consumer says no'\nexit 1\n"
    _skill(lib, "gate", hooks={"plan--execute": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)

    with pytest.raises(OperationAborted) as exc_info:
        kernel.transition("T-1", "execute", actor="test", caller_cwd=project)

    assert exc_info.value.subscriber == "hook-runner"
    assert "consumer says no" in exc_info.value.reason
    assert WorktreeManager.load_for_task(project, "T-1", state_dir=worktrees_dir(project)) is None
    assert not WorktreeManager.branch_exists(project, "task/t-1")


# ----- demo consumer end-to-end: declaration → materialization → gates -----


def test_demo_consumer_end_to_end(project, lib):
    """A fixture consumer skill ships a summary gate on document--review AND
    an extra required plan section; the whole channel is exercised:
    declaration → union materialization → scaffold+gate extension → hook
    abort with the hook's reason → pass after compliance."""
    summary_gate = (
        "#!/usr/bin/env bash\n"
        'dir="$(dirname "$AI_HATS_HOOK_TASK_FILE")"\n'
        'if [[ ! -s "$dir/summary.md" ]]; then\n'
        '  echo "summary.md missing or empty — document the task first"\n'
        "  exit 1\n"
        "fi\n"
    )
    _skill(
        lib,
        "summary-gate",
        hooks={"document--review": ["gate.sh"]},
        sections=["- Rollback plan"],
        script_body=summary_gate,
    )
    _materialize(project, lib)

    kernel = _kernel(project)  # sections=None → consumer catalog picked up
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)

    scaffold = (kernel.tasks_dir / "T-1" / "plan.md").read_text()
    assert "## Rollback plan" in scaffold, "consumer section must reach the scaffold"

    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    with pytest.raises(OperationAborted) as exc_info:  # consumer section unfilled
        kernel.transition("T-1", "execute", actor="test", caller_cwd=project)
    assert exc_info.value.subscriber == "plan-gate"
    assert "Rollback plan" in exc_info.value.reason

    (kernel.tasks_dir / "T-1" / "plan.md").write_text(
        _FILLED_PLAN + "\n## Rollback plan\nrevert the merge\n"
    )
    kernel.transition("T-1", "execute", actor="test", caller_cwd=project)
    kernel.transition("T-1", "document", actor="test", caller_cwd=project)

    with pytest.raises(OperationAborted) as exc_info:  # hook refuses: no summary.md
        kernel.transition("T-1", "review", actor="test", caller_cwd=project)
    assert exc_info.value.subscriber == "hook-runner"
    assert "summary.md missing" in exc_info.value.reason
    assert kernel.get("T-1").state == "document"

    (kernel.tasks_dir / "T-1" / "summary.md").write_text("done\n")
    kernel.transition("T-1", "review", actor="test", caller_cwd=project)
    assert kernel.get("T-1").state == "review"


def test_fail_under_revert_consumer_section(project, lib):
    """Remove the consumer declaration → re-materialize → the gate stops
    requiring the section (config-driven, not baked in)."""
    skill = _skill(lib, "extra", sections=["- Rollback plan"])
    _materialize(project, lib)
    assert any(s.name == "Rollback plan" for s in consumer_plan_sections(project))

    shutil.rmtree(skill)
    _materialize(project, lib)
    assert all(s.name != "Rollback plan" for s in consumer_plan_sections(project))

    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    (kernel.tasks_dir / "T-1" / "plan.md").write_text(_FILLED_PLAN)
    kernel.transition("T-1", "execute", actor="test", caller_cwd=project)  # must not raise
    assert kernel.get("T-1").state == "execute"


# ----- unit-level runner guards -----


def test_runner_ignores_non_edge_events(project):
    runner = HookRunnerExtension(
        lifecycle_hooks_dir(project),
        project / "tasks",
        project_dir=project,
    )

    class _Evt:  # not an EdgeEvent
        key = "epicify"

    class _Ctx:
        event = _Evt()

    assert runner.on_event(_Ctx()) is None


def test_runner_manifest_scoping_is_per_event(project, lib):
    """Entries of OTHER events in the manifest never block this event
    (dispatcher.sh backstop scoping, HATS-593)."""
    _skill(lib, "gate", hooks={"document--review": ["h.sh"]})
    _materialize(project, lib)
    shutil.rmtree(lifecycle_hooks_dir(project) / "document--review.d")
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    # brainstorm--plan has no manifest entries → unaffected by the degraded
    # document--review surface.
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project)
    assert kernel.get("T-1").state == "plan"


def test_hook_cwd_is_project_dir(project, lib):
    dump = project / "cwd.dump"
    body = f'#!/usr/bin/env bash\npwd > "{dump}"\nexit 0\n'
    _skill(lib, "probe", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    kernel = _kernel(project)
    kernel.create(actor="test", caller_cwd=project / ".agent", task_id="T-1", title="t")
    kernel.transition("T-1", "plan", actor="test", caller_cwd=project / ".agent")
    assert Path(dump.read_text().strip()).resolve() == project.resolve()


def test_refused_transition_is_journaled(project, lib):
    """PROP-004: the consumer refusal is auditable — abort outcome with the
    hook's reason lands in the dispatch journal."""

    class _Sink:
        def __init__(self):
            self.records = []

        def record(self, record):
            self.records.append(record)

    body = "#!/usr/bin/env bash\necho nope\nexit 1\n"
    _skill(lib, "gate", hooks={"brainstorm--plan": ["h.sh"]}, script_body=body)
    _materialize(project, lib)
    sink = _Sink()
    kernel = _kernel(project, journal_sink=sink)
    kernel.create(actor="test", caller_cwd=project, task_id="T-1", title="t")
    with pytest.raises(OperationAborted):
        kernel.transition("T-1", "plan", actor="test", caller_cwd=project)

    refusal = sink.records[-1]
    assert refusal.result == "aborted"
    outcomes = {o.subscriber: o for o in refusal.outcomes}
    assert outcomes["hook-runner"].outcome == "abort"
    assert "nope" in outcomes["hook-runner"].reason
