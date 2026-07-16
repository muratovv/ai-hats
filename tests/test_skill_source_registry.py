"""Tests for the skill-source open-registry (HATS-871 / T11).

Mirrors ``test_provider_registry.py`` (T10). A package advertises a skills root
under the ``ai_hats.skills`` entry-point group; ai-hats discovers it via
``importlib.metadata`` and appends the root's ``skills/`` dir to the resolver
chain — without the integrator importing or hard-coding the package.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ai_hats import skill_sources as ss


@pytest.fixture(autouse=True)
def _isolate_registry():
    """Each test gets a clean registry; prior state restored afterwards."""
    saved = dict(ss._SKILL_SOURCE_REGISTRY)
    ss._reset_for_tests()
    yield
    ss._reset_for_tests()
    ss._SKILL_SOURCE_REGISTRY.update(saved)


def _make_source(tmp_path: Path, pkg: str, skill: str) -> Path:
    """A fake skill-source root: ``<pkg>/skills/<skill>/SKILL.md``."""
    skill_dir = tmp_path / pkg / "skills" / skill
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(f"---\nname: {skill}\n---\nbody\n")
    return tmp_path / pkg


def test_register_and_roots_roundtrip(tmp_path):
    root = _make_source(tmp_path, "pkg_a", "demo")
    ss.register_skill_source("pkg_a", root)
    assert "pkg_a" in ss.skill_source_names()
    assert ss.skill_source_roots() == [root]


def test_double_register_raises(tmp_path):
    root = _make_source(tmp_path, "pkg_a", "demo")
    ss.register_skill_source("pkg_a", root)
    with pytest.raises(ss.SkillSourceRegistryError, match="already registered"):
        ss.register_skill_source("pkg_a", root)


def test_empty_group_means_no_roots():
    assert ss.skill_source_roots() == []


def test_registration_order_is_preserved(tmp_path):
    a = _make_source(tmp_path, "pkg_a", "a")
    b = _make_source(tmp_path, "pkg_b", "b")
    ss.register_skill_source("pkg_a", a)
    ss.register_skill_source("pkg_b", b)
    assert ss.skill_source_roots() == [a, b]


def test_resolve_anchor_rejects_root_without_skills_dir(tmp_path, monkeypatch):
    (tmp_path / "nopkg").mkdir()
    monkeypatch.setattr(ss, "files", lambda anchor: tmp_path / "nopkg")
    assert ss._resolve_anchor("nopkg") is None


def test_resolve_anchor_returns_root_with_skills_dir(tmp_path, monkeypatch):
    root = _make_source(tmp_path, "pkg_a", "demo")
    monkeypatch.setattr(ss, "files", lambda anchor: root)
    assert ss._resolve_anchor("pkg_a") == root


class _FakeEntryPoint:
    """Minimal stand-in for importlib.metadata.EntryPoint (.name / .value)."""

    def __init__(self, name: str, value: str):
        self.name = name
        self.value = value


def test_out_of_tree_source_discovered_via_entry_point(tmp_path, monkeypatch):
    root = _make_source(tmp_path, "plugin_pkg", "plugin-skill")
    ep = _FakeEntryPoint("plugin", "plugin_pkg")
    monkeypatch.setattr(ss, "_skill_source_entry_points", lambda: [ep])
    monkeypatch.setattr(
        ss, "_resolve_anchor", lambda anchor: root if anchor == "plugin_pkg" else None
    )
    ss._reset_for_tests()

    ss._load_skill_source_entry_points()

    assert "plugin" in ss.skill_source_names()
    assert ss.skill_source_roots() == [root]


def test_broken_anchor_skipped_not_fatal(tmp_path, monkeypatch, caplog):
    good_root = _make_source(tmp_path, "good_pkg", "good-skill")
    ep_bad = _FakeEntryPoint("bad", "missing_pkg")
    ep_good = _FakeEntryPoint("good", "good_pkg")
    monkeypatch.setattr(ss, "_skill_source_entry_points", lambda: [ep_bad, ep_good])
    monkeypatch.setattr(
        ss, "_resolve_anchor", lambda anchor: good_root if anchor == "good_pkg" else None
    )
    ss._reset_for_tests()

    with caplog.at_level("WARNING"):
        ss._load_skill_source_entry_points()  # must not raise

    assert "bad" not in ss.skill_source_names()  # unresolvable anchor skipped
    assert "good" in ss.skill_source_names()  # the good one still registered


def test_discovery_failure_is_non_fatal(monkeypatch):
    def _boom():
        raise RuntimeError("package metadata unavailable")

    monkeypatch.setattr(ss, "_skill_source_entry_points", _boom)
    ss._reset_for_tests()

    ss._load_skill_source_entry_points()  # swallows the error
    assert ss.skill_source_roots() == []
