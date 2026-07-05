"""Unit tests for HATS-408 P1: yaml hardening + default_role healing.

ProjectConfig.from_yaml must:
  * Drop known-deprecated keys (e.g. `imports_order`) BEFORE pydantic strict
    validation, emitting one stderr WARN per stripped key.
  * Heal empty `default_role` from `active_role` on load, emitting one
    stderr WARN.
  * Stay backward-compatible with clean yaml (no spurious WARNs).
  * HATS-581: strip unknown non-deprecated keys with a WARN (forward-compat),
    rather than raising on them.
"""

from __future__ import annotations

from ai_hats.models import ProjectConfig, _DEPRECATED_PROJECT_FIELDS
from ai_hats.paths import PROJECT_CONFIG


# -- Deprecated-field stripping --


def test_imports_order_stripped_and_warned(tmp_path, capsys):
    """v0.6 yaml carrying the `imports_order` ghost loads cleanly with a single stderr WARN."""
    path = tmp_path / PROJECT_CONFIG
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
    path = tmp_path / PROJECT_CONFIG
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
    path = tmp_path / PROJECT_CONFIG
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


def test_unknown_non_deprecated_key_stripped_with_warn(tmp_path, capsys):
    """HATS-581: truly unknown keys are now stripped with a WARN instead of
    crashing (forward-compat — an older binary must survive a yaml a newer
    binary wrote). Previously these hit ``extra="forbid"`` and raised; the
    forward-compat strip supersedes that for top-level keys.
    """
    path = tmp_path / PROJECT_CONFIG
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "mystery_flag: true\n"
    )

    cfg = ProjectConfig.from_yaml(path)

    assert cfg.provider == "claude"
    assert not hasattr(cfg, "mystery_flag")
    err = capsys.readouterr().err
    assert "dropping unknown field 'mystery_flag'" in err


def test_deprecated_constant_is_frozen():
    """Guard against accidental mutation of the allow-list at runtime."""
    assert isinstance(_DEPRECATED_PROJECT_FIELDS, frozenset)
    assert "imports_order" in _DEPRECATED_PROJECT_FIELDS


# -- default_role healing --


def test_default_role_healed_from_active_role(tmp_path, capsys):
    """v0.6 yaml with active_role + empty default_role → default_role := active_role + WARN."""
    path = tmp_path / PROJECT_CONFIG
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
    path = tmp_path / PROJECT_CONFIG
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
    path = tmp_path / PROJECT_CONFIG
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
    path = tmp_path / PROJECT_CONFIG
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


# -- HATS-408 review A2: round-trip preserves user-configured fields --


def test_save_after_load_preserves_user_fields(tmp_path, capsys):
    """HATS-415 ``_normalize_yaml`` inside ``bump()`` calls ``cfg.save()`` to
    persist healed default_role + strip deprecated keys. ``cfg.save()`` must
    NOT silently drop other user-configured sections (customizations,
    library_paths, feedback policy, task_prefix, venv_path,
    manage_gitignore) — that would erase user settings."""
    import yaml

    path = tmp_path / PROJECT_CONFIG
    # A "loaded" v0.6 yaml with every reasonable user customisation set.
    # Note: ``customizations`` wire format is NESTED add/remove (not flat
    # add_traits) — see OverlayConfig.from_dict/to_dict.
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "default_role: dev\n"
        "library_paths:\n"
        "  - /opt/extra-lib\n"
        "  - ./local-lib\n"
        "manage_gitignore: false\n"
        "task_prefix: PROJ\n"
        "customizations:\n"
        "  dev:\n"
        "    add:\n"
        "      traits: [my-trait]\n"
        "    remove:\n"
        "      skills: [skill-x]\n"
        "feedback:\n"
        "  session_retro:\n"
        "    policy: always\n"
    )

    cfg = ProjectConfig.from_yaml(path)
    capsys.readouterr()  # drain any WARNs
    # Simulate what ``_normalize_yaml`` does inside ``bump()`` (the path
    # previously owned by ``migrate-v07 --force``).
    cfg.save(path)

    saved = yaml.safe_load(path.read_text())
    assert saved["library_paths"] == ["/opt/extra-lib", "./local-lib"], saved
    assert saved.get("manage_gitignore") is False, saved
    assert saved.get("task_prefix") == "PROJ", saved
    assert "customizations" in saved and "dev" in saved["customizations"], saved
    assert saved["customizations"]["dev"]["add"]["traits"] == ["my-trait"]
    assert saved["customizations"]["dev"]["remove"]["skills"] == ["skill-x"]
    assert "feedback" in saved, saved
    assert saved["feedback"]["session_retro"]["policy"] == "always"
    # And the v0.6 ghost is gone.
    assert "imports_order" not in saved


def test_save_after_strip_preserves_round_trip_for_deprecated_yaml(tmp_path, capsys):
    """End-to-end HATS-415 yaml normalisation (was migrate-v07): load v0.6
    yaml with imports_order + customizations, save, re-load, save again →
    byte-stable + user fields survive both passes."""
    import yaml

    path = tmp_path / PROJECT_CONFIG
    path.write_text(
        "schema_version: 4\n"
        "provider: claude\n"
        "ai_hats_dir: .agent/ai-hats\n"
        "active_role: dev\n"
        "imports_order: role-first\n"
        "customizations:\n"
        "  dev:\n"
        "    add:\n"
        "      traits: [extra]\n"
    )

    ProjectConfig.from_yaml(path).save(path)
    first_bytes = path.read_bytes()
    capsys.readouterr()
    ProjectConfig.from_yaml(path).save(path)
    second_bytes = path.read_bytes()

    assert first_bytes == second_bytes, "second save drifted from first"
    saved = yaml.safe_load(first_bytes)
    assert "imports_order" not in saved
    assert saved["default_role"] == "dev"
    assert saved["customizations"]["dev"]["add"]["traits"] == ["extra"]


def test_strip_and_heal_independent_warns(tmp_path, capsys):
    """A v0.6 yaml that needs both fixes emits BOTH WARNs in a single from_yaml call."""
    path = tmp_path / PROJECT_CONFIG
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
