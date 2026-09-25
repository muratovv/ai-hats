"""``WrapRunner._probe_readiness``: every readiness finding is read and becomes a
WARN notice; only a runner that ``refuses_unready`` turns a blocking one into a
refusal — the TUI launches anyway, the surface's own login flow runs there."""

from types import SimpleNamespace

from ai_hats_observe.canonical.signals import (
    Notice,
    PersonActionRequired,
    PersonMustAct,
    WorthRecording,
)

from ai_hats.headless.runner import HeadlessRunner
from ai_hats.runtime_common import readiness_notices
from ai_hats.wrap_runner import WrapRunner
from tests.test_wrap_runner_settings_lint import _runner, _session

_REFUSAL = PersonActionRequired(reason=PersonMustAct.REAUTHENTICATE, detail="not logged in")
_NOTICE = Notice(reason=WorthRecording.SURFACE_WARNING, detail="probe unavailable")


def test_blocking_and_notice_findings_both_warn(tmp_path):
    provider = SimpleNamespace(readiness_findings=lambda environ: [_REFUSAL, _NOTICE])
    traces: list[str] = []

    findings = _runner(tmp_path, provider)._probe_readiness(_session(traces))

    assert findings == [_REFUSAL, _NOTICE]
    assert [(n.level, n.text) for n in readiness_notices(findings)] == [
        ("warn", "not logged in"),
        ("warn", "probe unavailable"),
    ]
    assert any("reauthenticate" in line for line in traces)


def test_only_the_holder_refuses_an_unready_surface():
    assert (WrapRunner.refuses_unready, HeadlessRunner.refuses_unready) == (False, True)


def test_a_ready_surface_yields_no_findings(tmp_path):
    provider = SimpleNamespace(readiness_findings=lambda environ: [])

    assert _runner(tmp_path, provider)._probe_readiness(_session([])) == []


def test_a_probe_that_raises_is_logged_not_raised(tmp_path):
    def boom(environ):
        raise RuntimeError("probe exploded")

    provider = SimpleNamespace(readiness_findings=boom)
    traces: list[str] = []

    assert _runner(tmp_path, provider)._probe_readiness(_session(traces)) == []
    assert any("probe exploded" in line for line in traces)
