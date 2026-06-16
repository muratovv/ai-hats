"""Unit tests for ``is_local_channel`` (HATS-781).

The update banner / probe must be hidden when the harness runs from a LOCAL
editable checkout — the dev drives updates with ``git``, not ``self update``.
"""

from __future__ import annotations

from ai_hats.update_check import is_local_channel


def _write_yaml(project_dir, body: str) -> None:
    (project_dir / "ai-hats.yaml").write_text(body)


def test_false_when_no_config(tmp_path):
    assert is_local_channel(tmp_path) is False


def test_true_when_channel_local(tmp_path):
    _write_yaml(tmp_path, "harness:\n  channel: local\n  path: .\n")
    assert is_local_channel(tmp_path) is True


def test_false_when_channel_edge(tmp_path):
    _write_yaml(tmp_path, "harness:\n  channel: edge\n")
    assert is_local_channel(tmp_path) is False


def test_false_when_channel_stable_default(tmp_path):
    # No harness block → channel defaults to stable.
    _write_yaml(tmp_path, "provider: claude\n")
    assert is_local_channel(tmp_path) is False


def test_false_when_config_unparseable(tmp_path):
    # Broken YAML must degrade to False (do not suppress the banner).
    _write_yaml(tmp_path, "harness:\n  channel: local\n :::not yaml:::\n")
    assert is_local_channel(tmp_path) is False


def test_false_when_unknown_channel_value(tmp_path):
    # An invalid enum value must not raise out of the helper.
    _write_yaml(tmp_path, "harness:\n  channel: bogus\n")
    assert is_local_channel(tmp_path) is False
