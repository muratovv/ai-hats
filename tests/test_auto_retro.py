"""Tests for retro.auto_retro — policy decision logic."""

from __future__ import annotations

import json

import yaml

from ai_hats.retro.auto_retro import should_run


def _write_config(path, *, policy="smart", min_turns=5, min_tool_calls=10, mode="programmatic"):
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
                "mode": mode,
            },
            "judge": {"policy": "manual"},
        },
    }
    with open(path, "w") as f:
        yaml.dump(data, f)


def _write_metrics(path, *, turns=6, tool_calls=15):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"turns": turns, "tool_calls": tool_calls, "exit_code": 0}))


class TestPolicyOff:
    def test_skip(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, policy="off")
        _write_metrics(metrics)

        action, _ = should_run(config, metrics)
        assert action == "skip"


class TestPolicyAlways:
    def test_run(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, policy="always")
        _write_metrics(metrics, turns=1, tool_calls=0)

        action, _ = should_run(config, metrics)
        assert action == "run"


class TestPolicySmart:
    def test_skip_both_below(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=3, tool_calls=5)

        action, _ = should_run(config, metrics)
        assert action == "skip"

    def test_run_turns_met(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=7, tool_calls=5)

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_run_tool_calls_met(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=3, tool_calls=15)

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_run_both_met(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=10, tool_calls=20)

        action, _ = should_run(config, metrics)
        assert action == "run"


class TestPolicyHint:
    def test_hint_when_threshold_met(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, policy="hint", min_turns=5)
        _write_metrics(metrics, turns=10)

        action, _ = should_run(config, metrics)
        assert action == "hint"

    def test_skip_when_below(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, policy="hint", min_turns=5, min_tool_calls=10)
        _write_metrics(metrics, turns=2, tool_calls=3)

        action, _ = should_run(config, metrics)
        assert action == "skip"


class TestEdgeCases:
    def test_missing_config_uses_defaults(self, tmp_path):
        """No ai-hats.yaml → defaults (smart, threshold 5/10)."""
        config = tmp_path / "nonexistent.yaml"
        metrics = tmp_path / "metrics.json"
        _write_metrics(metrics, turns=10, tool_calls=20)

        action, _ = should_run(config, metrics)
        assert action == "run"

    def test_missing_metrics_skip(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "nonexistent.json"
        _write_config(config, policy="smart")

        action, _ = should_run(config, metrics)
        assert action == "skip"

    def test_malformed_metrics_skip(self, tmp_path):
        config = tmp_path / "ai-hats.yaml"
        metrics = tmp_path / "metrics.json"
        _write_config(config, policy="smart")
        metrics.write_text("not json{{{")

        action, _ = should_run(config, metrics)
        assert action == "skip"


def _setup_project(tmp_path, session_id="SID", **config_kwargs):
    """Create project dir with ai-hats.yaml + metrics.json for make_decision tests."""
    _write_config(tmp_path / "ai-hats.yaml", **config_kwargs)
    metrics = tmp_path / ".gitlog" / f"session_{session_id}" / "metrics.json"
    metrics.parent.mkdir(parents=True)
    return metrics


class TestWriteRetroLog:
    def test_creates_file_and_session_dir(self, tmp_path):
        from ai_hats.retro.auto_retro import write_retro_log

        write_retro_log(tmp_path, "SID", "runtime", "decision", "skip: below threshold")

        log = tmp_path / ".gitlog" / "session_SID" / "retro.log"
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

        log = tmp_path / ".gitlog" / "session_SID" / "retro.log"
        lines = log.read_text().strip().split("\n")
        assert len(lines) == 3
        assert "decision" in lines[0] and "runtime" in lines[0]
        assert "spawn" in lines[1] and "hook" in lines[1]
        assert "saved" in lines[2] and "builder" in lines[2]

    def test_strips_tabs_and_newlines_in_detail(self, tmp_path):
        from ai_hats.retro.auto_retro import write_retro_log

        write_retro_log(tmp_path, "SID", "hook", "skip", "a\tb\nc")
        line = (tmp_path / ".gitlog" / "session_SID" / "retro.log").read_text().rstrip("\n")
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
        assert d["retro_path"].endswith(".agent/retrospectives/sessions/programmatic/SID.md")

    def test_run_threshold_met(self, tmp_path):
        from ai_hats.retro.auto_retro import make_decision

        metrics = _setup_project(tmp_path, min_turns=5, min_tool_calls=10, mode="llm")
        metrics.write_text(json.dumps({"turns": 20, "tool_calls": 50}))

        d = make_decision(tmp_path, "SID")
        assert d["action"] == "run"
        assert d["mode"] == "llm"
        assert d["background"] is True
        assert d["retro_path"].endswith("/llm/SID.md")

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


class TestDescribeDecision:
    def test_run_bg_llm(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision({
            "action": "run", "reason": "threshold met", "mode": "llm",
            "background": True, "retro_path": "/x/llm/SID.md",
        })
        assert "generating" in s and "llm" in s and "bg" in s and "/x/llm/SID.md" in s

    def test_skip_with_reason(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision({
            "action": "skip",
            "reason": "below threshold (turns=0<1, tool_calls=0<1)",
            "mode": None, "background": None, "retro_path": None,
        })
        assert s.startswith("skipped")
        assert "below threshold" in s

    def test_hint_includes_cli_hint(self):
        from ai_hats.retro.auto_retro import describe_decision

        s = describe_decision({
            "action": "hint", "reason": "threshold met",
            "mode": "llm", "background": False,
            "retro_path": "/a/llm/20260422-071234-1.md",
        })
        assert "ai-hats retro" in s
        assert "20260422-071234-1" in s


class TestMainHookWritesLog:
    def test_skip_writes_hook_line(self, tmp_path, monkeypatch):
        from ai_hats.retro import auto_retro

        metrics = _setup_project(tmp_path, min_turns=5, min_tool_calls=10)
        metrics.write_text(json.dumps({"turns": 0, "tool_calls": 0}))

        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("AI_HATS_SESSION_ID", "SID")
        auto_retro.main()

        log = tmp_path / ".gitlog" / "session_SID" / "retro.log"
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
        monkeypatch.setenv("AI_HATS_SESSION_ID", "SID")
        auto_retro.main()

        log = tmp_path / ".gitlog" / "session_SID" / "retro.log"
        content = log.read_text()
        assert "hint" in content
        assert "threshold met" in content

    def test_run_foreground_writes_builder_lines(self, tmp_path, monkeypatch):
        """Foreground mode writes start + saved/failed via write_retro_log."""
        from ai_hats.retro import auto_retro

        metrics = _setup_project(
            tmp_path, policy="always", mode="programmatic",
        )
        metrics.write_text(json.dumps({"turns": 10, "tool_calls": 20, "exit_code": 0}))

        # Force synchronous (foreground) path regardless of config default.
        class _Builder:
            def __init__(self, *a, **kw): pass
            def build_and_save(self, sid, mode=None):
                return tmp_path / f"retro-{sid}.md"

        monkeypatch.setattr(
            "ai_hats.retro.builder.SessionRetroBuilder", _Builder,
        )
        auto_retro._run_foreground(tmp_path, "SID", "programmatic")

        log = tmp_path / ".gitlog" / "session_SID" / "retro.log"
        content = log.read_text()
        assert "builder\tstart" in content
        assert "builder\tsaved" in content
