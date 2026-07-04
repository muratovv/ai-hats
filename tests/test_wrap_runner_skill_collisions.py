"""HATS-901/907: seam tests for ``WrapRunner._check_skill_collisions``.

Drives the method directly — a real session would need a PTY spawn; the
seam is enough because the pure detection/sweep logic lives in plugin_dir
and is covered there. The heal flow itself is e2e-covered in
``tests/e2e/test_skills_mirror_self_heals.py``.
"""

from types import SimpleNamespace

from ai_hats.paths import session_cache_dir
from ai_hats.wrap_runner import WrapRunner


def _runner(project):
    """WrapRunner with a minimal payload + REAL HooksManager (HATS-865) so
    class-level ``binary_behind_source`` monkeypatches still bite."""
    from ai_hats.composition_payload import CompositionPayload
    from ai_hats.hooks_manager import HooksManager
    from ai_hats.models import ProjectConfig
    from ai_hats_core import CompositionResult

    hooks = HooksManager(
        project,
        ProjectConfig(),
        compose=lambda role: None,
        resolve_provider=lambda name: None,
    )
    payload = CompositionPayload(
        result=CompositionResult(
            name="t", priorities=[], rules=[], skills=[], injections=[],
        ),
        provider=None,
        effective_role="t",
        hooks=hooks,
    )
    return WrapRunner(project, payload)


def _setup(project, tmp_path, monkeypatch, sid="sess-1"):
    """Minimal plugin skills dir + isolated HOME + trace-capturing session."""
    plugin_skills = session_cache_dir(project, sid) / "plugin" / "skills"
    (plugin_skills / "alpha").mkdir(parents=True)
    (plugin_skills / "alpha" / "SKILL.md").write_text("# alpha\n")
    fake_home = tmp_path / "fake-home"
    fake_home.mkdir(exist_ok=True)
    monkeypatch.setattr("ai_hats.wrap_runner.Path.home", lambda: fake_home)
    traces: list[str] = []
    session = SimpleNamespace(session_id=sid, log_trace=lambda tag, msg: traces.append(msg))
    result = SimpleNamespace(skills=[SimpleNamespace(name="alpha")])
    return session, result, fake_home, traces


def _plant_managed_mirror(base):
    mirror = base / ".claude" / "skills"
    (mirror / "alpha").mkdir(parents=True)
    (mirror / "alpha" / "SKILL.md").write_text("# stale export\n")
    (mirror / ".ai-hats-managed").write_text("alpha\n")
    return mirror


def test_collision_yields_warn_notice(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    session, result, _, _ = _setup(project, tmp_path, monkeypatch)
    stale = project / ".claude" / "skills" / "alpha"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# stale export\n")

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["warn"]
    assert "alpha" in notices[0].text
    assert str(stale) in notices[0].text


def test_clean_project_yields_no_notice(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    session, result, _, _ = _setup(project, tmp_path, monkeypatch)

    assert _runner(project)._check_skill_collisions(session, result) == []


def test_managed_project_mirror_auto_heals(tmp_path, monkeypatch):
    """HATS-907: marker-proven project mirror → removed, green NOTE naming the
    skills + trash path, no CLI verb instructed (HATS-906 pin successor)."""
    project = tmp_path / "project"
    project.mkdir()
    session, result, _, traces = _setup(project, tmp_path, monkeypatch)
    mirror = _plant_managed_mirror(project)

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["note"]
    assert "alpha" in notices[0].text
    assert "trash" in notices[0].text
    assert "self init" not in notices[0].text
    assert "self bump" not in notices[0].text
    assert not (mirror / "alpha").exists()
    assert not (mirror / ".ai-hats-managed").exists()
    assert any("skills-mirror heal" in t for t in traces)


def test_managed_heal_gated_on_version_skew(tmp_path, monkeypatch):
    """A binary behind upstream must not sweep (HATS-905 P1); the WARN names a
    real CLI verb (HATS-906 pin successor)."""
    project = tmp_path / "project"
    project.mkdir()
    session, result, _, _ = _setup(project, tmp_path, monkeypatch)
    mirror = _plant_managed_mirror(project)
    monkeypatch.setattr(
        "ai_hats.hooks_manager.HooksManager.binary_behind_source", lambda self: True
    )

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["warn"]
    assert "self update" in notices[0].text
    assert "self bump" not in notices[0].text
    assert (mirror / "alpha" / "SKILL.md").exists()
    assert (mirror / ".ai-hats-managed").exists()


def test_managed_heal_gated_in_hard_delete_mode(tmp_path, monkeypatch):
    """AI_HATS_TRASH_DIR=- → removal would be unrecoverable → WARN, no heal."""
    project = tmp_path / "project"
    project.mkdir()
    session, result, _, _ = _setup(project, tmp_path, monkeypatch)
    mirror = _plant_managed_mirror(project)
    monkeypatch.setenv("AI_HATS_TRASH_DIR", "-")

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["warn"]
    assert (
        "recoverable" not in notices[0].text.lower() or "unrecoverable" in notices[0].text.lower()
    )
    assert (mirror / "alpha" / "SKILL.md").exists()


def test_home_scope_managed_stays_warn(tmp_path, monkeypatch):
    """~/.claude/skills is user-owned (HATS-465) — never healed, hint instructs
    no ai-hats verb (the old 'self init removes it' was factually wrong here)."""
    project = tmp_path / "project"
    project.mkdir()
    session, result, fake_home, _ = _setup(project, tmp_path, monkeypatch)
    mirror = _plant_managed_mirror(fake_home)

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["warn"]
    assert "self init" not in notices[0].text
    assert (mirror / "alpha" / "SKILL.md").exists()
    assert (mirror / ".ai-hats-managed").exists()


def test_heal_failure_fails_open(tmp_path, monkeypatch):
    """Exception inside the sweep → WARN naming it, launch proceeds."""
    project = tmp_path / "project"
    project.mkdir()
    session, result, _, _ = _setup(project, tmp_path, monkeypatch)
    _plant_managed_mirror(project)

    def boom(project_dir):
        raise RuntimeError("disk on fire")

    monkeypatch.setattr("ai_hats.plugin_dir.drop_legacy_skills_mirror", boom)

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["warn"]
    assert "disk on fire" in notices[0].text


def test_mixed_verdicts_heal_plus_warn(tmp_path, monkeypatch):
    """Project managed mirror heals (NOTE) while a home 'differs' copy still
    warns — one notice per concern."""
    project = tmp_path / "project"
    project.mkdir()
    session, result, fake_home, _ = _setup(project, tmp_path, monkeypatch)
    _plant_managed_mirror(project)
    stale = fake_home / ".claude" / "skills" / "alpha"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# old copy\n")

    notices = _runner(project)._check_skill_collisions(session, result)

    assert [n.level for n in notices] == ["note", "warn"]
    assert str(stale) in notices[1].text
