"""``WrapRunner._probe_readiness``: every readiness finding is a WARN notice —
HITL launches anyway, the surface's own TUI runs its login flow."""

from types import SimpleNamespace

from ai_hats_observe.canonical.signals import (
    Notice,
    PersonActionRequired,
    PersonMustAct,
    WorthRecording,
)

from tests.test_wrap_runner_settings_lint import _runner, _session

_REFUSAL = PersonActionRequired(reason=PersonMustAct.REAUTHENTICATE, detail="not logged in")
_NOTICE = Notice(reason=WorthRecording.SURFACE_WARNING, detail="probe unavailable")


def test_blocking_and_notice_findings_both_warn(tmp_path):
    provider = SimpleNamespace(readiness_findings=lambda environ: [_REFUSAL, _NOTICE])
    traces: list[str] = []

    notices = _runner(tmp_path, provider)._probe_readiness(_session(traces))

    assert [(n.level, n.text) for n in notices] == [
        ("warn", "not logged in"),
        ("warn", "probe unavailable"),
    ]
    assert any("reauthenticate" in line for line in traces)


def test_a_ready_surface_yields_no_notices(tmp_path):
    provider = SimpleNamespace(readiness_findings=lambda environ: [])

    assert _runner(tmp_path, provider)._probe_readiness(_session([])) == []


def test_a_probe_that_raises_is_logged_not_raised(tmp_path):
    def boom(environ):
        raise RuntimeError("probe exploded")

    provider = SimpleNamespace(readiness_findings=boom)
    traces: list[str] = []

    assert _runner(tmp_path, provider)._probe_readiness(_session(traces)) == []
    assert any("probe exploded" in line for line in traces)
