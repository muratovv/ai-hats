"""HATS-901: seam tests for ``WrapRunner._check_skill_collisions``.

Drives the method directly — a real session would need a PTY spawn; the
seam is enough because the pure detection logic lives in plugin_dir and
is covered there.
"""

from types import SimpleNamespace

from ai_hats.paths import session_cache_dir
from ai_hats.wrap_runner import WrapRunner


def _setup(project, tmp_path, monkeypatch, sid="sess-1"):
    """Materialize a minimal plugin skills dir + isolate HOME from the runner."""
    plugin_skills = session_cache_dir(project, sid) / "plugin" / "skills"
    (plugin_skills / "alpha").mkdir(parents=True)
    (plugin_skills / "alpha" / "SKILL.md").write_text("# alpha\n")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr("ai_hats.wrap_runner.Path.home", lambda: fake_home)
    session = SimpleNamespace(session_id=sid)
    result = SimpleNamespace(skills=[SimpleNamespace(name="alpha")])
    return session, result, fake_home


def test_collision_yields_warn_notice(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    session, result, _ = _setup(project, tmp_path, monkeypatch)
    stale = project / ".claude" / "skills" / "alpha"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# stale export\n")

    notices = WrapRunner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["warn"]
    assert "alpha" in notices[0].text
    assert str(stale) in notices[0].text


def test_clean_project_yields_no_notice(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    session, result, _ = _setup(project, tmp_path, monkeypatch)

    assert WrapRunner(project)._check_skill_collisions(session, result) == []
