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
