"""CLI wiring seam: entry-point discovery, wired/bare kernel selection, and
inline delta echo (HATS-1038 C1). The rack stays first-party-free — the
integrator's wired kernel reaches it only through this discovered provider."""

from __future__ import annotations

from ai_hats_rack import cli
from ai_hats_rack.dispatch import Delta, DispatchRecord, Phase, SubscriberOutcome
from ai_hats_rack.kernel import Kernel, KernelResult


def _record(*lines: str) -> DispatchRecord:
    outcomes = tuple(
        SubscriberOutcome("wt", Phase.IN_LOCK, "delta", delta=Delta(work_log=(line,)))
        for line in lines
    )
    return DispatchRecord(
        event_key="edge:plan--execute",
        task_id="HATS-001",
        actor="a",
        force=False,
        reason="",
        outcomes=outcomes,
    )


# ----- discovery -------------------------------------------------------------


def test_provider_is_none_without_entry_point(monkeypatch):
    monkeypatch.setattr("importlib.metadata.entry_points", lambda group=None: [])
    cli._provider.cache_clear()
    try:
        assert cli._provider() is None
    finally:
        cli._provider.cache_clear()


def test_provider_loads_first_entry_point(monkeypatch):
    sentinel = object()

    class _FakeEP:
        def load(self):
            return lambda: sentinel

    monkeypatch.setattr("importlib.metadata.entry_points", lambda group=None: [_FakeEP()])
    cli._provider.cache_clear()
    try:
        assert cli._provider() is sentinel
    finally:
        cli._provider.cache_clear()


# ----- wired vs bare kernel selection ----------------------------------------


def test_build_kernel_delegates_to_provider(tmp_path):
    override = tmp_path / "tasks"

    class _StubProvider:
        def build_kernel(self, root, caller_cwd):
            return ("wired", root)

        def after_create(self, root, result):  # pragma: no cover - unused here
            pass

        def handle_error(self, exc, as_json, task_id=""):  # pragma: no cover
            return False

    kernel, root = cli._build_kernel(override, tmp_path, _StubProvider())
    assert kernel == ("wired", root)
    assert root.tasks_dir == override


def test_build_kernel_bare_without_provider(tmp_path):
    kernel, root = cli._build_kernel(tmp_path / "tasks", tmp_path, None)
    assert isinstance(kernel, Kernel)
    assert root.tasks_dir == tmp_path / "tasks"


# ----- inline delta echo -----------------------------------------------------


def test_echo_deltas_prints_worktree_and_epic_lines(capsys):
    result = KernelResult(
        task=None,
        journal=(_record("Worktree: /tmp/wt", "epic HATS-9: activate brainstorm -> execute"),),
    )
    cli._echo_deltas(result)
    out = capsys.readouterr().out
    assert "Worktree: /tmp/wt" in out
    assert "epic HATS-9: activate brainstorm -> execute" in out


def test_echo_deltas_dedupes_repeated_lines(capsys):
    # The per-edge journal repeats cumulative outcomes; a line prints once.
    result = KernelResult(
        task=None,
        journal=(_record("Worktree: /tmp/wt"), _record("Worktree: /tmp/wt")),
    )
    cli._echo_deltas(result)
    assert capsys.readouterr().out.count("Worktree: /tmp/wt") == 1
