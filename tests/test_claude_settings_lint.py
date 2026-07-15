"""HATS-1006: Claude settings lint — pure rule lint + ClaudeProvider chain.

Lives with the surface (``ai_hats.providers``), not the runner: the lint is
Claude-specific; ``WrapRunner`` only consumes ``Provider.settings_lint_warnings``.
"""

import json
from pathlib import Path

from ai_hats.providers import ClaudeProvider, lint_permission_rules, lint_settings_files

SRC = Path("/x/.claude/settings.json")


def test_deprecated_write_rule_in_allow_is_found():
    settings = {"permissions": {"allow": ["Write(~/dev/**)"]}}
    findings = lint_permission_rules(settings, source=SRC)
    assert len(findings) == 1
    f = findings[0]
    assert f.source == SRC
    assert f.array == "allow"
    assert f.rule == "Write(~/dev/**)"
    assert f.replacement == "Edit(~/dev/**)"


def test_deny_and_ask_arrays_are_linted():
    settings = {
        "permissions": {
            "deny": ["Write(//**/.env)"],
            "ask": ["NotebookEdit(~/nb/**)"],
        }
    }
    findings = lint_permission_rules(settings, source=SRC)
    assert [(f.array, f.replacement) for f in findings] == [
        ("deny", "Edit(//**/.env)"),
        ("ask", "Edit(~/nb/**)"),
    ]


def test_glob_rule_maps_to_read():
    settings = {"permissions": {"allow": ["Glob(src/**)"]}}
    (finding,) = lint_permission_rules(settings, source=SRC)
    assert finding.replacement == "Read(src/**)"


def test_clean_settings_yield_nothing():
    settings = {"permissions": {"allow": ["Edit(~/dev/**)", "Bash(git:*)", "WebSearch"]}}
    assert lint_permission_rules(settings, source=SRC) == []


def test_prefix_lookalike_tools_are_not_flagged():
    settings = {"permissions": {"allow": ["WriteFile(~/dev/**)", "mcp__Write__x"]}}
    assert lint_permission_rules(settings, source=SRC) == []


def test_malformed_shapes_are_skipped_field_level():
    assert lint_permission_rules([], source=SRC) == []
    assert lint_permission_rules({"permissions": "nope"}, source=SRC) == []
    settings = {"permissions": {"allow": [42, None, {"Write(x)": 1}], "deny": "nope"}}
    assert lint_permission_rules(settings, source=SRC) == []


def test_files_chain_reads_existing_and_fails_open(tmp_path):
    good = tmp_path / "settings.json"
    good.write_text(json.dumps({"permissions": {"allow": ["Write(~/dev/**)"]}}))
    broken = tmp_path / "settings.local.json"
    broken.write_text("{not json")
    missing = tmp_path / "absent.json"

    findings = lint_settings_files([good, broken, missing])

    assert [(f.source, f.rule) for f in findings] == [(good, "Write(~/dev/**)")]


def _seed(path: Path, rules: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": rules}))


def test_claude_provider_lints_the_three_file_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    _seed(tmp_path / "cfg" / "settings.json", {"allow": ["Write(~/dev/**)"]})
    project = tmp_path / "proj"
    _seed(project / ".claude" / "settings.json", {"deny": ["Glob(src/**)"]})
    _seed(project / ".claude" / "settings.local.json", {"allow": ["Edit(//tmp/**)"]})

    warnings_ = ClaudeProvider().settings_lint_warnings(project)

    assert len(warnings_) == 2
    assert "Write(~/dev/**)" in warnings_[0]
    assert "Edit(~/dev/**)" in warnings_[0]
    assert "Glob(src/**)" in warnings_[1]
    assert "Read(src/**)" in warnings_[1]


def test_claude_provider_clean_chain_is_silent(tmp_path, monkeypatch):
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "cfg"))
    project = tmp_path / "proj"
    project.mkdir()

    assert ClaudeProvider().settings_lint_warnings(project) == []
