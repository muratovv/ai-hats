"""Tests for retro.auto_retro — policy decision logic."""

from __future__ import annotations

import json
import logging

import yaml

from ai_hats.retro.auto_retro import should_run
from ai_hats.paths import runs_dir
from ai_hats.constants import ENV_SKIP_RETRO
from ai_hats_observe.trace import ENV_SESSION_ID
from ai_hats_observe.artifacts import METRICS_JSON, RETRO_LOG, session_dirname
from ai_hats.paths import PROJECT_CONFIG


def _write_config(path, *, policy="smart", min_turns=5, min_tool_calls=10):
    data = {
        "schema_version": 2,
        "provider": "claude",
        "active_role": "assistant",
        "default_role": "",
        "library_paths": [],
        "feedback": {
            "session_retro": {
                "policy": policy,
                "smart_threshold": {
                    "min_turns": min_turns,
                    "min_tool_calls": min_tool_calls,
                },
                "background": True,
            },
        },
    }
    with open(path, "w") as f:
        yaml.dump(data, f)


def _write_metrics(path, *, turns=6, tool_calls=15, role=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"turns": turns, "tool_calls": tool_calls, "exit_code": 0}
    if role is not None:
        payload["role"] = role
    path.write_text(json.dumps(payload))


class TestPolicyOff:
    def test_skip(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="off")
        _write_metrics(metrics)

        action, _ = should_run(config, metrics)
        assert action == "skip"


class TestPolicyAlways:
    def test_run(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="always")
        _write_metrics(metrics, turns=1, tool_calls=0)

        action, _ = should_run(config, metrics)
        assert action == "run"


class TestReviewerOwnSession:
    """HATS-1483: the auditor's own sessions are never AUTO-reviewed.

    The env guard (HATS-252/1402/1481) protects a process, so it leaked at
    every new entry point. This invariant is a property of the session.
    """

    def test_skip_reviewer_session_under_policy_always(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="always")
        _write_metrics(metrics, role="session-reviewer")

        action, reason = should_run(config, metrics)
        assert action == "skip"
        assert "session-reviewer" in reason

    def test_skip_reviewer_session_under_policy_smart(self, tmp_path):
        """Not policy-specific: the hook path (smart) skips the same session."""
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=99, tool_calls=99, role="session-reviewer")

        action, _ = should_run(config, metrics)
        assert action == "skip"

    def test_other_role_still_runs(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="always")
        _write_metrics(metrics, role="ai-hats-maintainer")

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_missing_metrics_fails_open(self, tmp_path):
        """No metrics.json → no new skip; policy decides as before."""
        config = tmp_path / PROJECT_CONFIG
        _write_config(config, policy="always")

        action, reason = should_run(config, tmp_path / "nonexistent.json")
        assert (action, reason) == ("run", "policy=always")

    def test_unreadable_metrics_fails_open(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="always")
        metrics.write_text("not json{{{")

        action, reason = should_run(config, metrics)
        assert (action, reason) == ("run", "policy=always")

    def test_make_decision_skips_reviewer_session(self, tmp_path):
        """The runtime step reads the same field — no env involved."""
        from ai_hats.retro.auto_retro import make_decision

        metrics = _setup_project(tmp_path, policy="always")
        metrics.write_text(json.dumps({"role": "session-reviewer", "turns": 3}))

        d = make_decision(tmp_path, "SID")
        assert d["action"] == "skip"
        assert "session-reviewer" in d["reason"]


class TestPolicySmart:
    def test_skip_both_below(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=3, tool_calls=5)

        action, _ = should_run(config, metrics)
        assert action == "skip"

    def test_run_turns_met(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=7, tool_calls=5)

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_run_tool_calls_met(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=3, tool_calls=15)

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_run_both_met(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=10, tool_calls=20)

        action, _ = should_run(config, metrics)
        assert action == "run"


class TestPolicyHint:
    def test_hint_when_threshold_met(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="hint", min_turns=5)
        _write_metrics(metrics, turns=10)

        action, _ = should_run(config, metrics)
        assert action == "hint"

    def test_skip_when_below(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="hint", min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=2, tool_calls=3)

        action, _ = should_run(config, metrics)
        assert action == "skip"


class TestEdgeCases:
    def test_missing_config_uses_defaults(self, tmp_path):
        """No ai-hats.yaml → defaults (smart, threshold 5/10)."""
        config = tmp_path / "nonexistent.yaml"
        metrics = tmp_path / METRICS_JSON
        _write_metrics(metrics, turns=10, tool_calls=20)

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_missing_metrics_skip(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / "nonexistent.json"
        _write_config(config, policy="smart")

        action, _ = should_run(config, metrics)
        assert action == "skip"

    def test_malformed_metrics_skip(self, tmp_path):
        config = tmp_path / PROJECT_CONFIG
        metrics = tmp_path / METRICS_JSON
        _write_config(config, policy="smart")
        metrics.write_text("not json{{{")

        action, _ = should_run(config, metrics)
        assert action == "skip"


def _setup_project(tmp_path, session_id="SID", **config_kwargs):
    """Create project dir with ai-hats.yaml + metrics.json for make_decision tests."""
    _write_config(tmp_path / PROJECT_CONFIG, **config_kwargs)
    metrics = runs_dir(tmp_path) / session_dirname(session_id) / METRICS_JSON
    metrics.parent.mkdir(parents=True)
    return metrics


def _be_session(monkeypatch, session_id, project):
    """Stand in for a session the way a launch writes one — envelope included.

    The hook reads the identity, not the scalar beside it (HATS-1613), and a
    bare id stands in for a session no launch produces.
    """
    from ai_hats.session_identity import SessionIdentity

    identity = SessionIdentity(
        id=session_id,
        role="maintainer",
        provider="claude",
        project_dir=project,
        session_dir=runs_dir(project) / session_dirname(session_id),
    )
    for key, value in identity.to_env().items():
        monkeypatch.setenv(key, value)


class TestWriteRetroLog:
    def test_creates_file_and_session_dir(self, tmp_path):
        from ai_hats.retro.auto_retro import write_retro_log

        write_retro_log(tmp_path, "SID", "runtime", "decision", "skip: below threshold")

        log = runs_dir(tmp_path) / "session_SID" / RETRO_LOG
        assert log.exists()
        line = log.read_text().rstrip("\n")
        parts = line.split("\t")
        assert len(parts) == 4
        assert parts[1] == "runtime"
        assert parts[2] == "decision"
        assert parts[3] == "skip: below threshold"

    def test_appends_multiple_entries(self, tmp_path):
        from ai_hats.retro.auto_retro import write_retro_log

        write_retro_log(tmp_path, "SID", "runtime", "decision", "run: threshold met")
        write_retro_log(tmp_path, "SID", "hook", "spawn", "pid=1234")
        write_retro_log(tmp_path, "SID", "builder", "saved", "/path/to/retro.md")

        log = runs_dir(tmp_path) / "session_SID" / RETRO_LOG
        lines = log.read_text().strip().split("\n")
        assert len(lines) == 3
        assert "decision" in lines[0] and "runtime" in lines[0]
        assert "spawn" in lines[1] and "hook" in lines[1]
        assert "saved" in lines[2] and "builder" in lines[2]

    def test_strips_tabs_and_newlines_in_detail(self, tmp_path):
        from ai_hats.retro.auto_retro import write_retro_log

        write_retro_log(tmp_path, "SID", "hook", "skip", "a\tb\nc")
        line = (runs_dir(tmp_path) / "session_SID" / RETRO_LOG).read_text().rstrip("\n")
        # Split on the SEPARATOR tabs (4 parts), then check the last field.
        parts = line.split("\t")
        assert parts[3] == "a b c"


class TestMakeDecision:
    def test_skip_below_threshold(self, tmp_path):
        from ai_hats.retro.auto_retro import make_decision

        metrics = _setup_project(tmp_path, min_turns=5, min_tool_calls=10)
        # `measured` is what makes a threshold evaluable at all (HATS-1397): a
        # bare `turns: 0` is a pre-HATS-1374 fabrication, and skipping it as
        # "below threshold" claims a comparison nobody could have made.
        metrics.write_text(json.dumps({"measured": True, "turns": 0, "tool_calls": 0}))

        d = make_decision(tmp_path, "SID")
        assert d["action"] == "skip"
        assert "below threshold" in d["reason"]
        assert d["retro_path"].endswith("sessions/retros/sessions/SID.md")

    def test_run_threshold_met(self, tmp_path):
        from ai_hats.retro.auto_retro import make_decision

        metrics = _setup_project(tmp_path, min_turns=5, min_tool_calls=10)
        metrics.write_text(json.dumps({"turns": 20, "tool_calls": 50}))

        d = make_decision(tmp_path, "SID")
        assert d["action"] == "run"
        assert d["background"] is True
        assert d["retro_path"].endswith("/sessions/SID.md")

    def test_hint_populates_reminder(self, tmp_path):
        from ai_hats.retro.auto_retro import make_decision

        metrics = _setup_project(tmp_path, policy="hint", min_turns=5, min_tool_calls=10)
        metrics.write_text(json.dumps({"turns": 20, "tool_calls": 50}))

        d = make_decision(tmp_path, "SID")
        assert d["action"] == "hint"
        assert d["reminder"] == {
            "count": 1,
            "command": "ai-hats reflect hypothesis",
        }

    def test_internal_error_returns_skip(self, tmp_path, monkeypatch):
        """make_decision must not raise; errors collapse into skip."""
        from ai_hats.retro import auto_retro

        def boom(*a, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(auto_retro, "should_run", boom)
        d = auto_retro.make_decision(tmp_path, "SID")
        assert d["action"] == "skip"
        assert "internal error" in d["reason"]
        assert "boom" in d["reason"]
        assert d["wrap_up"] is None

    def test_keyboard_interrupt_returns_skip(self, tmp_path, monkeypatch):
        """HATS-1426: 'never raises' excluded KeyboardInterrupt — the one thing
        that actually reached it in production."""
        from ai_hats.retro import auto_retro

        def interrupted(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr(auto_retro, "should_run", interrupted)
        d = auto_retro.make_decision(tmp_path, "SID")
        assert d["action"] == "skip"
        assert "internal error" in d["reason"]

    def test_unresolvable_paths_return_skip(self, tmp_path, monkeypatch):
        """The incident died resolving runs_dir (unreadable ai-hats.yaml), which
        sat outside the guard — and the skip dict resolves that path again."""
        from ai_hats import paths
        from ai_hats.retro import auto_retro

        def broken(*a, **kw):
            raise ValueError("ai-hats.yaml unparseable")

        monkeypatch.setattr(paths, "runs_dir", broken)
        d = auto_retro.make_decision(tmp_path, "SID")
        assert d["action"] == "skip"
        assert "internal error" in d["reason"]
        assert d["log_path"] is None


class TestDescribeDecision:
    def test_run_bg(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision(
            {
                "action": "run",
                "reason": "threshold met",
                "background": True,
                "retro_path": "/x/SID.md",
            }
        )
        assert "generating" in s and "bg" in s and "/x/SID.md" in s

    def test_skip_with_reason(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision(
            {
                "action": "skip",
                "reason": "below threshold (turns=0<1, tool_calls=0<1)",
                "background": None,
                "retro_path": None,
            }
        )
        assert s.startswith("skipped")
        assert "below threshold" in s

    def test_hint_includes_cli_hint(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision(
            {
                "action": "hint",
                "reason": "threshold met",
                "background": False,
                "retro_path": "/a/20260422-071234-1.md",
            }
        )
        assert "ai-hats session retro" in s
        assert "20260422-071234-1" in s


class TestMainHookWritesLog:
    def test_skip_writes_hook_line(self, tmp_path, monkeypatch):
        from ai_hats.retro import auto_retro

        metrics = _setup_project(tmp_path, min_turns=5, min_tool_calls=10)
        # `measured` is what makes a threshold evaluable at all (HATS-1397): a
        # bare `turns: 0` is a pre-HATS-1374 fabrication, and skipping it as
        # "below threshold" claims a comparison nobody could have made.
        metrics.write_text(json.dumps({"measured": True, "turns": 0, "tool_calls": 0}))

        monkeypatch.chdir(tmp_path)
        _be_session(monkeypatch, "SID", tmp_path)
        auto_retro.main()

        log = runs_dir(tmp_path) / "session_SID" / RETRO_LOG
        assert log.exists()
        content = log.read_text()
        assert "hook" in content
        assert "skip" in content
        assert "below threshold" in content

    def test_hint_writes_hook_line(self, tmp_path, monkeypatch):
        from ai_hats.retro import auto_retro

        metrics = _setup_project(tmp_path, policy="hint", min_turns=5)
        metrics.write_text(json.dumps({"turns": 10, "tool_calls": 20}))

        monkeypatch.chdir(tmp_path)
        _be_session(monkeypatch, "SID", tmp_path)
        auto_retro.main()

        log = runs_dir(tmp_path) / "session_SID" / RETRO_LOG
        content = log.read_text()
        assert "hint" in content
        assert "threshold met" in content

    def test_run_foreground_spawns_session_reviewer(self, tmp_path, monkeypatch):
        """Foreground mode delegates to a single session-reviewer spawn."""
        from ai_hats.retro import auto_retro

        spawned: list[tuple] = []
        monkeypatch.setattr(
            auto_retro,
            "_spawn_session_reviewer_background",
            lambda pd, sid: spawned.append((pd, sid)),
        )
        auto_retro._run_foreground(tmp_path, "SID")
        assert spawned == [(tmp_path, "SID")]


class TestMainReadsTheIdentity:
    """HATS-1613: the hook reads the session through one reader, not the scalar.

    Session end is observability, so a torn identity stays soft — but it is
    reported: the id it would have logged under cannot be vouched for.
    """

    def test_a_half_identified_session_is_reported_and_not_worked_on(
        self, tmp_path, monkeypatch, caplog
    ):
        from ai_hats.retro import auto_retro

        _setup_project(tmp_path)
        monkeypatch.setenv(ENV_SESSION_ID, "SID")  # the scalar alone: no launch writes that

        with caplog.at_level(logging.WARNING):
            auto_retro.main(tmp_path)

        # Any step past the guard logs its outcome, so an empty session dir is
        # the proof nothing was decided under an id nobody can vouch for.
        assert not (runs_dir(tmp_path) / "session_SID" / RETRO_LOG).exists()
        assert "AI_HATS_SESSION_IDENTITY" in caplog.text

    def test_no_session_is_inert_and_silent(self, tmp_path, monkeypatch, caplog):
        """An operator in a plain terminal is a legitimate state, not a fault."""
        from ai_hats.retro import auto_retro

        _setup_project(tmp_path)
        monkeypatch.delenv("AI_HATS_SESSION_IDENTITY", raising=False)

        with caplog.at_level(logging.WARNING):
            auto_retro.main(tmp_path)

        assert not (runs_dir(tmp_path) / "session_SID" / RETRO_LOG).exists()
        assert caplog.text == ""


class TestRecursionGuard:
    def test_main_returns_early_with_breadcrumb(self, tmp_path, monkeypatch):
        """HATS_SKIP_RETRO=1 → main() exits early and logs `recursion-guard`."""
        from ai_hats.retro import auto_retro

        # No config / metrics — the guard fires before policy logic runs.
        monkeypatch.chdir(tmp_path)
        _be_session(monkeypatch, "SID", tmp_path)
        monkeypatch.setenv(ENV_SKIP_RETRO, "1")

        # Sentinel — should_run must NOT be reached.
        called: list[bool] = []
        monkeypatch.setattr(
            auto_retro,
            "should_run",
            lambda *a, **kw: called.append(True) or ("run", ""),
        )

        auto_retro.main()

        assert called == []
        log = runs_dir(tmp_path) / "session_SID" / RETRO_LOG
        content = log.read_text()
        assert "auto_retro\tskip\trecursion-guard" in content

    def test_spawn_session_reviewer_sets_env(self, tmp_path, monkeypatch):
        """Popen child env carries HATS_SKIP_RETRO=1 to break the loop."""
        from ai_hats.retro import auto_retro

        captured: dict = {}

        class _FakeProc:
            pid = 4242

        def fake_popen(cmd, **kw):  # noqa: ANN001 — test stub
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            return _FakeProc()

        monkeypatch.setattr("subprocess.Popen", fake_popen)
        auto_retro._spawn_session_reviewer_background(tmp_path, "SID")

        assert captured["env"][ENV_SKIP_RETRO] == "1"
        # ai_hats.cli.reflect_session_main is the harness entry-point.
        assert "ai_hats.cli.reflect_session_main" in captured["cmd"]
        assert "SID" in captured["cmd"]
        log = runs_dir(tmp_path) / "session_SID" / RETRO_LOG
        assert "session-reviewer\tspawn" in log.read_text()
