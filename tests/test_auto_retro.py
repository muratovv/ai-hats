"""Tests for retro.auto_retro — policy decision logic."""

from __future__ import annotations

import json

import yaml

from ai_hats.retro.auto_retro import should_run
from ai_hats.paths import runs_dir
from ai_hats.constants import ENV_SESSION_ID, ENV_SKIP_RETRO
from ai_hats.paths import METRICS_JSON, PROJECT_CONFIG, RETRO_LOG, session_dirname


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


def _write_metrics(path, *, turns=6, tool_calls=15):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"turns": turns, "tool_calls": tool_calls, "exit_code": 0}))


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
        metrics.write_text(json.dumps({"turns": 0, "tool_calls": 0}))

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


class TestDescribeDecision:
    def test_run_bg(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision({
            "action": "run", "reason": "threshold met",
            "background": True, "retro_path": "/x/SID.md",
        })
        assert "generating" in s and "bg" in s and "/x/SID.md" in s

    def test_skip_with_reason(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision({
            "action": "skip",
            "reason": "below threshold (turns=0<1, tool_calls=0<1)",
            "background": None, "retro_path": None,
        })
        assert s.startswith("skipped")
        assert "below threshold" in s

    def test_hint_includes_cli_hint(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision({
            "action": "hint", "reason": "threshold met",
            "background": False,
            "retro_path": "/a/20260422-071234-1.md",
        })
        assert "ai-hats session retro" in s
        assert "20260422-071234-1" in s


class TestMainHookWritesLog:
    def test_skip_writes_hook_line(self, tmp_path, monkeypatch):
        from ai_hats.retro import auto_retro

        metrics = _setup_project(tmp_path, min_turns=5, min_tool_calls=10)
        metrics.write_text(json.dumps({"turns": 0, "tool_calls": 0}))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ENV_SESSION_ID, "SID")
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
        monkeypatch.setenv(ENV_SESSION_ID, "SID")
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
            auto_retro, "_spawn_session_reviewer_background",
            lambda pd, sid: spawned.append((pd, sid)),
        )
        auto_retro._run_foreground(tmp_path, "SID")
        assert spawned == [(tmp_path, "SID")]


class TestRecursionGuard:
    def test_main_returns_early_with_breadcrumb(self, tmp_path, monkeypatch):
        """HATS_SKIP_RETRO=1 → main() exits early and logs `recursion-guard`."""
        from ai_hats.retro import auto_retro

        # No config / metrics — the guard fires before policy logic runs.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv(ENV_SESSION_ID, "SID")
        monkeypatch.setenv(ENV_SKIP_RETRO, "1")

        # Sentinel — should_run must NOT be reached.
        called: list[bool] = []
        monkeypatch.setattr(
            auto_retro, "should_run",
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
