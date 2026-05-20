"""Unit tests for HATS-408 P1: yaml hardening + default_role healing.

ProjectConfig.from_yaml must:
  * Drop known-deprecated keys (e.g. `imports_order`) BEFORE pydantic strict
    validation, emitting one stderr WARN per stripped key.
  * Heal empty `default_role` from `active_role` on load, emitting one
    stderr WARN.
  * Stay backward-compatible with clean yaml (no spurious WARNs) and still
    refuse unknown non-deprecated keys.
"""

from __future__ import annotations

import pytest

from ai_hats.models import ProjectConfig, ProjectConfigError, _DEPRECATED_PROJECT_FIELDS


# -- Deprecated-field stripping --


def test_imports_order_stripped_and_warned(tmp_path, capsys):
    """v0.6 yaml carrying the `imports_order` ghost loads cleanly with a single stderr WARN."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "default_role: dev\n"
        "imports_order: role-first\n"
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.provider == "claude"
    assert cfg.active_role == "dev"
    captured = capsys.readouterr()
    assert captured.out == ""
    # Exactly one WARN line for the stripped field. Count occurrences of the
    # quoted-field token (avoids false positives if the tmp path itself
    # happens to contain "imports_order", which pytest's fixture naming does).
    assert captured.err.count("'imports_order'") == 1
    assert "deprecated field" in captured.err
    assert str(path) in captured.err
    assert "remove from yaml to silence" in captured.err
    # Single WARN line — newline count is a stable shape check.
    assert captured.err.count("\n") == 1


def test_clean_yaml_emits_no_deprecated_warn(tmp_path, capsys):
    """Regression: clean yaml stays silent (no false-positive WARNs)."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "default_role: dev\n"
    )

    ProjectConfig.from_yaml(path)

    captured = capsys.readouterr()
    assert captured.err == ""


def test_idempotent_rerun_no_warn(tmp_path, capsys):
    """Second from_yaml on the same (clean) file emits no WARN — strip helper is pure."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "default_role: dev\n"
    )

    ProjectConfig.from_yaml(path)
    capsys.readouterr()  # drain first run's output (should already be empty)
    ProjectConfig.from_yaml(path)

    captured = capsys.readouterr()
    assert captured.err == ""


def test_unknown_non_deprecated_key_still_raises(tmp_path):
    """Truly unknown keys must still hit `extra="forbid"` — strip is a narrow allow-list."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "mystery_flag: true\n"
    )

    with pytest.raises(ProjectConfigError) as exc:
        ProjectConfig.from_yaml(path)
    assert "mystery_flag" in str(exc.value)


def test_deprecated_constant_is_frozen():
    """Guard against accidental mutation of the allow-list at runtime."""
    assert isinstance(_DEPRECATED_PROJECT_FIELDS, frozenset)
    assert "imports_order" in _DEPRECATED_PROJECT_FIELDS


# -- default_role healing --


def test_default_role_healed_from_active_role(tmp_path, capsys):
    """v0.6 yaml with active_role + empty default_role → default_role := active_role + WARN."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        # default_role intentionally omitted (v0.6 shape)
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.active_role == "dev"
    assert cfg.default_role == "dev"
    captured = capsys.readouterr()
    assert "healed default_role := active_role" in captured.err
    assert "'dev'" in captured.err
    assert str(path) in captured.err


def test_default_role_heal_no_op_when_both_set(tmp_path, capsys):
    """No heal when default_role already populated, even if it differs from active_role."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "default_role: sre\n"
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.active_role == "dev"
    assert cfg.default_role == "sre"
    captured = capsys.readouterr()
    assert "healed default_role" not in captured.err


def test_default_role_heal_no_op_when_both_empty(tmp_path, capsys):
    """Greenfield project (no active_role, no default_role) → no heal, no WARN."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.active_role == ""
    assert cfg.default_role == ""
    captured = capsys.readouterr()
    assert captured.err == ""


def test_default_role_heal_explicit_empty_string(tmp_path, capsys):
    """v0.6 sometimes wrote `default_role: ''` literally — must still heal."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        'default_role: ""\n'
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.default_role == "dev"
    captured = capsys.readouterr()
    assert "healed default_role" in captured.err


# -- Combined stripping + healing --


def test_strip_and_heal_independent_warns(tmp_path, capsys):
    """A v0.6 yaml that needs both fixes emits BOTH WARNs in a single from_yaml call."""
    path = tmp_path / "ai-hats.yaml"
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "imports_order: role-first\n"
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.default_role == "dev"
    captured = capsys.readouterr()
    assert "imports_order" in captured.err
    assert "healed default_role" in captured.err
