"""Tests for the per-session plugin as plan entries (HATS-307, HATS-294)."""

from __future__ import annotations

from ai_hats_core.layout import ProjectLayout

import json
import multiprocessing as mp
from pathlib import Path

import pytest

from ai_hats.materialization import describe_mkdir
from ai_hats.paths import (
    claude_dir,
    claude_plugin_manifest,
    claude_settings_json,
    claude_skills_dir,
)
from ai_hats.session_artifacts import RunMode, SessionPolicy
from ai_hats.surfaces.claude.plugin_dir import plan_plugin
from ai_hats.surfaces.plan import Launch, MaterializationPlan, apply
from tests._plan_helpers import composition_with, skill_of


def _make_skill(name: str, root: Path, body: str = "", *, layout: ProjectLayout | None = None):
    """Build a skill source dir on disk and the composed ``Skill`` over it."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body or f"---\nname: {name}\n---\n# {name}\n")
    return skill_of(skill_dir, layout=layout)


def _plan(identity: str, skills, plugin_dir: Path) -> MaterializationPlan:
    """The plugin's entries under a session root beside it."""
    root = plugin_dir.parent
    composition = composition_with(identity, skills)
    return MaterializationPlan(
        composition=composition,
        prompt=composition.prompt,
        surface="claude",
        run_mode=RunMode.HITL,
        policy=SessionPolicy(),
        root=root,
        entries=(describe_mkdir(root), *plan_plugin(composition, plugin_dir)),
        env={},
        launch=Launch(args=(), sdk_options=None),
    )


def _materialize(identity: str, skills, plugin_dir: Path) -> Path:
    apply(_plan(identity, skills, plugin_dir))
    return plugin_dir


def test_plugin_json_shape(tmp_path: Path) -> None:
    out = _materialize("role-judge", [], tmp_path / "s" / "plugin")
    manifest = json.loads((claude_plugin_manifest(out)).read_text())
    assert manifest["name"] == "ai-hats-role-judge"
    assert "version" in manifest


def test_the_manifest_names_the_expression_as_one_token(tmp_path: Path) -> None:
    out = _materialize("maintainer + sre", [], tmp_path / "s" / "plugin")
    assert json.loads(claude_plugin_manifest(out).read_text())["name"] == "ai-hats-maintainer-sre"


def test_copies_skill_directory(tmp_path: Path) -> None:
    skill = _make_skill(
        "role-coherence-protocol",
        tmp_path / "src",
        body="---\nname: role-coherence-protocol\ndescription: x\n---\n# body\n",
    )
    out = _materialize("role-judge", [skill], tmp_path / "s" / "plugin")
    copied = out / "skills" / "role-coherence-protocol" / "SKILL.md"
    assert copied.exists()
    assert "role-coherence-protocol" in copied.read_text()


def test_copies_non_skill_md_assets_verbatim(tmp_path: Path) -> None:
    skill_root = tmp_path / "src"
    skill_root.mkdir()
    # Drop a non-SKILL.md asset alongside; must be preserved.
    (skill_root / "alpha").mkdir()
    (skill_root / "alpha" / "SKILL.md").write_text("---\nname: alpha\n---\n# alpha\n")
    (skill_root / "alpha" / "fixture.txt").write_text("RAW_ASSET_<ai_hats_dir>")
    skill = skill_of(skill_root / "alpha", layout=ProjectLayout.at(tmp_path))
    out = _materialize("test-role", [skill], tmp_path / "s" / "plugin")
    asset = out / "skills" / "alpha" / "fixture.txt"
    assert asset.exists()
    # Verbatim — placeholder must NOT be expanded in non-SKILL.md files.
    assert asset.read_text() == "RAW_ASSET_<ai_hats_dir>"


def test_expands_placeholder_in_skill_md(tmp_path: Path) -> None:
    """The document the composition carries is the rendered one — the adapter
    expands it for the layout, the plan writes it over the copy."""
    skill = _make_skill(
        "beta",
        tmp_path / "src",
        body="see <ai_hats_dir>/state for details",
        layout=ProjectLayout.at(tmp_path),
    )
    out = _materialize("test-role", [skill], tmp_path / "s" / "plugin")
    body = (out / "skills" / "beta" / "SKILL.md").read_text()
    assert "<ai_hats_dir>" not in body
    assert ".agent/ai-hats/state" in body


def test_empty_skills_list_makes_empty_skills_dir(tmp_path: Path) -> None:
    out = _materialize("test-role", [], tmp_path / "s" / "plugin")
    skills_dir = out / "skills"
    assert skills_dir.is_dir()
    assert list(skills_dir.iterdir()) == []


def test_a_stray_file_inside_a_mirrored_skill_is_swept_and_one_beside_it_kept(
    tmp_path: Path,
) -> None:
    """No wipe — a session root is its own (HATS-1981): the tree sync leaves
    nothing stale inside a mirrored skill, and touches nothing it did not plan."""
    skill = _make_skill("alpha", tmp_path / "src")
    plugin_dir = tmp_path / "s" / "plugin"
    (plugin_dir / "skills" / "alpha").mkdir(parents=True)
    (plugin_dir / "skills" / "alpha" / "stale.txt").write_text("stale")
    (plugin_dir / "leftover.txt").write_text("someone else's")

    _materialize("test-role", [skill], plugin_dir)

    assert not (plugin_dir / "skills" / "alpha" / "stale.txt").exists()
    assert (plugin_dir / "skills" / "alpha" / "SKILL.md").is_file()
    assert (plugin_dir / "leftover.txt").read_text() == "someone else's"
    assert claude_plugin_manifest(plugin_dir).exists()


def test_parallel_invocations_with_distinct_targets(tmp_path: Path) -> None:
    """Callers plan distinct roots to get isolated plugin dirs."""
    skill_a = _make_skill("alpha", tmp_path / "src")
    skill_b = _make_skill("beta", tmp_path / "src2")
    out_a = _materialize("role-a", [skill_a], tmp_path / "a" / "plugin")
    out_b = _materialize("role-b", [skill_b], tmp_path / "b" / "plugin")
    assert out_a != out_b
    assert (out_a / "skills" / "alpha").is_dir()
    assert (out_b / "skills" / "beta").is_dir()
    assert not (out_a / "skills" / "beta").exists()
    assert not (out_b / "skills" / "alpha").exists()


# Module-level so it is picklable under the multiprocessing "spawn" start
# method (the default on macOS; forced explicitly below for determinism).
def _hammer_apply(args: tuple) -> list[str]:
    plugin_dir, skill_dirs, iters, barrier = args
    skills = [skill_of(d) for d in skill_dirs]
    barrier.wait()  # release all workers into the critical section together
    errors: list[str] = []
    for _ in range(iters):
        try:
            apply(_plan("stress-role", skills, plugin_dir))
        except Exception as exc:  # noqa: BLE001 — record every failure mode
            errors.append(f"{type(exc).__name__}: {exc}")
    return errors


@pytest.mark.integration
def test_concurrent_same_target_is_safe(tmp_path: Path) -> None:
    """HATS-604: concurrent application on ONE shared root must be safe.

    The builder's ``rmtree -> mkdir -> per-skill copytree`` shredded itself
    under process contention (``ENOTEMPTY`` / ``EEXIST`` / ``ENOENT``);
    ``apply`` serialises on the lock beside the root.

    Real subprocesses (``multiprocessing`` spawn) — faithful to the
    cross-process fcntl-advisory lock contract that same-process threads
    would NOT exercise; hence ``@pytest.mark.integration``.
    """
    src = tmp_path / "src"
    src.mkdir()
    # A handful of non-trivial skills so each sync takes long enough to
    # widen the race window.
    skill_dirs = []
    for i in range(6):
        d = src / f"skill-{i}"
        d.mkdir()
        (d / "SKILL.md").write_text("x" * 400 + f"\n# skill {i}\n")
        skill_dirs.append(d)
    # ALL workers target this ONE root — models the per-session plugin-dir
    # collision (two processes that resolved the same session_id).
    target = tmp_path / "cache" / "sid" / "plugin"

    n_procs, iters = 6, 15
    ctx = mp.get_context("spawn")
    with ctx.Manager() as mgr:
        barrier = mgr.Barrier(n_procs)
        with ctx.Pool(n_procs) as pool:
            results = pool.map(
                _hammer_apply,
                [(target, skill_dirs, iters, barrier) for _ in range(n_procs)],
            )

    errors = [e for sub in results for e in sub]
    assert not errors, (
        f"{len(errors)}/{n_procs * iters} concurrent applications raced — sample: {errors[:3]}"
    )
    # Crash-free is necessary but not sufficient — the final dir must be a
    # VALID plugin (byte-stable-rebuild contract holds under contention).
    manifest = claude_plugin_manifest(target)
    assert manifest.is_file(), "final plugin dir missing manifest"
    got = sorted(p.name for p in (target / "skills").iterdir())
    assert got == sorted(d.name for d in skill_dirs), (
        f"final skills set {got} != expected {sorted(d.name for d in skill_dirs)}"
    )


# ---------- drop_legacy_skills_mirror (HATS-901) ----------


def _seed_mirror(project: Path) -> Path:
    """Pre-HATS-294 mirror: marker + 2 managed dirs + 1 user-authored dir."""
    skills = claude_skills_dir(project)
    for name in ("audit-reviewer", "backlog-manager"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text("# stale export")
    (skills / "my-own-skill").mkdir()
    (skills / "my-own-skill" / "SKILL.md").write_text("# user-authored")
    (skills / ".ai-hats-managed").write_text("audit-reviewer\nbacklog-manager\n")
    return skills


def test_drop_mirror_removes_marker_listed_only(tmp_path: Path) -> None:
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    skills = _seed_mirror(tmp_path)

    drop_legacy_skills_mirror(tmp_path)

    assert not (skills / "audit-reviewer").exists()
    assert not (skills / "backlog-manager").exists()
    assert not (skills / ".ai-hats-managed").exists()
    assert (skills / "my-own-skill" / "SKILL.md").exists()


def test_drop_mirror_noop_without_marker(tmp_path: Path) -> None:
    """No marker → never guess ownership, leave the dir alone."""
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    skills = claude_skills_dir(tmp_path)
    (skills / "my-own-skill").mkdir(parents=True)
    (skills / "my-own-skill" / "SKILL.md").write_text("# user-authored")

    drop_legacy_skills_mirror(tmp_path)

    assert (skills / "my-own-skill" / "SKILL.md").exists()


def test_drop_mirror_idempotent(tmp_path: Path) -> None:
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    skills = _seed_mirror(tmp_path)

    drop_legacy_skills_mirror(tmp_path)
    drop_legacy_skills_mirror(tmp_path)

    assert not (skills / ".ai-hats-managed").exists()
    assert (skills / "my-own-skill" / "SKILL.md").exists()


def test_drop_mirror_returns_removed_names(tmp_path: Path) -> None:
    """HATS-907: the session-start heal note needs the swept names."""
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    _seed_mirror(tmp_path)

    assert drop_legacy_skills_mirror(tmp_path) == ["audit-reviewer", "backlog-manager"]
    assert drop_legacy_skills_mirror(tmp_path) == []  # second call: nothing left


def test_drop_mirror_skips_missing_marker_entries(tmp_path: Path) -> None:
    """A marker line whose dir was hand-deleted is not counted as removed."""
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    skills = claude_skills_dir(tmp_path)
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "SKILL.md").write_text("# stale export")
    (skills / ".ai-hats-managed").write_text("alpha\ngone-long-ago\n")

    assert drop_legacy_skills_mirror(tmp_path) == ["alpha"]


def test_drop_mirror_ignores_traversal_marker_lines(tmp_path: Path) -> None:
    """HATS-907 P1: marker content is untrusted (committable by a hostile
    repo) — only plain child names are victims; traversal lines are inert."""
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    project = tmp_path / "project"
    skills = claude_skills_dir(project)
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "SKILL.md").write_text("# stale export")
    sibling = claude_settings_json(project)
    sibling.write_text("{}")
    outside = project / ".env"
    outside.write_text("SECRET=1")
    abs_target = tmp_path / "abs-target.txt"
    abs_target.write_text("keep")
    (skills / ".ai-hats-managed").write_text(
        f"alpha\n../settings.json\n../../.env\n..\n.\n{abs_target}\n"
    )

    removed = drop_legacy_skills_mirror(project)

    assert removed == ["alpha"]
    assert sibling.exists()
    assert outside.exists()
    assert abs_target.exists()
    assert not (skills / "alpha").exists()
    assert not (skills / ".ai-hats-managed").exists()


def test_drop_mirror_refuses_symlinked_skills_dir(tmp_path: Path) -> None:
    """`.claude/skills` symlinked elsewhere (e.g. ~/.claude/skills): a
    "project-scope" sweep would gut the link target — refuse wholesale."""
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    real = claude_skills_dir(tmp_path / "home")
    (real / "alpha").mkdir(parents=True)
    (real / "alpha" / "SKILL.md").write_text("# user-level data")
    (real / ".ai-hats-managed").write_text("alpha\n")
    project = tmp_path / "project"
    claude_dir(project).mkdir(parents=True)
    (claude_skills_dir(project)).symlink_to(real, target_is_directory=True)

    assert drop_legacy_skills_mirror(project) == []
    assert (real / "alpha" / "SKILL.md").exists()
    assert (real / ".ai-hats-managed").exists()


def test_drop_mirror_refuses_home_aliased_project(tmp_path: Path, monkeypatch) -> None:
    """project_dir == home → `.claude/skills` IS the user-level dir; ai-hats
    never wrote there (HATS-465) — refuse the sweep."""
    from ai_hats.plugin_dir import drop_legacy_skills_mirror

    fake_home = tmp_path / "home"
    skills = claude_skills_dir(fake_home)
    (skills / "alpha").mkdir(parents=True)
    (skills / "alpha" / "SKILL.md").write_text("# user-level data")
    (skills / ".ai-hats-managed").write_text("alpha\n")
    monkeypatch.setattr(Path, "home", lambda: fake_home)

    assert drop_legacy_skills_mirror(fake_home) == []
    assert (skills / "alpha" / "SKILL.md").exists()


# ---------- duplicate_skill_registrations (HATS-901) ----------


def test_duplicate_registration_identical_user_copy(tmp_path: Path) -> None:
    """A byte-identical copy under `~/.claude/skills/` is a provable redundant
    duplicate — Claude Code registers it alongside the session plugin."""
    import shutil

    from ai_hats.plugin_dir import duplicate_skill_registrations

    skill = _make_skill("alpha", tmp_path / "src")
    plugin = _materialize("test-role", [skill], tmp_path / "s" / "plugin")

    home = tmp_path / "home"
    user_copy = claude_skills_dir(home) / "alpha"
    shutil.copytree(plugin / "skills" / "alpha", user_copy)

    found = duplicate_skill_registrations(
        ["alpha"],
        project_dir=tmp_path,
        plugin_skills_root=plugin / "skills",
        home=home,
    )

    assert [(c.name, c.verdict) for c in found] == [("alpha", "identical")]
    assert found[0].path == user_copy


def test_duplicate_registration_differing_content(tmp_path: Path) -> None:
    """Same name, different bytes → 'differs' (stale copy vs user-authored is
    unprovable without library history — user must review)."""
    from ai_hats.plugin_dir import duplicate_skill_registrations

    skill = _make_skill("alpha", tmp_path / "src")
    plugin = _materialize("test-role", [skill], tmp_path / "s" / "plugin")

    home = tmp_path / "home"
    stale = claude_skills_dir(home) / "alpha"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# frozen at an old library version\n")

    found = duplicate_skill_registrations(
        ["alpha"], project_dir=tmp_path, plugin_skills_root=plugin / "skills", home=home
    )

    assert [(c.name, c.verdict) for c in found] == [("alpha", "differs")]


def test_duplicate_registration_marker_listed_is_managed(tmp_path: Path) -> None:
    """Project-scope dir listed in `.ai-hats-managed` → ownership proven by the
    marker; session start auto-heals it (HATS-907)."""
    from ai_hats.plugin_dir import duplicate_skill_registrations

    skill = _make_skill("alpha", tmp_path / "src")
    project = tmp_path / "project"
    plugin = _materialize("test-role", [skill], tmp_path / "s" / "plugin")

    mirror = claude_skills_dir(project)
    (mirror / "alpha").mkdir(parents=True)
    (mirror / "alpha" / "SKILL.md").write_text("# stale export\n")
    (mirror / ".ai-hats-managed").write_text("alpha\n")

    found = duplicate_skill_registrations(
        ["alpha"],
        project_dir=project,
        plugin_skills_root=plugin / "skills",
        home=tmp_path / "home",
    )

    assert [(c.name, c.verdict) for c in found] == [("alpha", "managed")]


def test_duplicate_registration_scope_attribution(tmp_path: Path) -> None:
    """HATS-907: the session-start heal partitions by scope — home collisions
    are never healed (HATS-465), project ones may be."""
    from ai_hats.plugin_dir import duplicate_skill_registrations

    skill = _make_skill("alpha", tmp_path / "src")
    project = tmp_path / "project"
    plugin = _materialize("test-role", [skill], tmp_path / "s" / "plugin")
    home = tmp_path / "home"
    for base in (home, project):
        stale = claude_skills_dir(base) / "alpha"
        stale.mkdir(parents=True)
        (stale / "SKILL.md").write_text("# stale\n")

    found = duplicate_skill_registrations(
        ["alpha"], project_dir=project, plugin_skills_root=plugin / "skills", home=home
    )

    assert [(c.scope, c.path) for c in found] == [
        ("home", claude_skills_dir(home) / "alpha"),
        ("project", claude_skills_dir(project) / "alpha"),
    ]


def test_duplicate_registration_none_when_clean(tmp_path: Path) -> None:
    """No same-name dirs anywhere → empty list (the everyday no-op path)."""
    from ai_hats.plugin_dir import duplicate_skill_registrations

    skill = _make_skill("alpha", tmp_path / "src")
    plugin = _materialize("test-role", [skill], tmp_path / "s" / "plugin")

    found = duplicate_skill_registrations(
        ["alpha"],
        project_dir=tmp_path,
        plugin_skills_root=plugin / "skills",
        home=tmp_path / "home",
    )

    assert found == []


# ---------- drop_legacy_root_skills_mirrors (HATS-1172) ----------


def test_drop_legacy_root_skills_mirrors_removes_root_dirs(tmp_path: Path) -> None:
    from ai_hats.plugin_dir import drop_legacy_root_skills_mirrors

    agy_skills = tmp_path / ".agy" / "skills"
    gemini_skills = tmp_path / ".gemini" / "skills"
    cline_skills = tmp_path / ".cline" / "skills"
    agents = tmp_path / ".agents"

    for d in (agy_skills, gemini_skills, cline_skills, agents):
        d.mkdir(parents=True)
        (d / "dummy.txt").write_text("test")

    removed = drop_legacy_root_skills_mirrors(tmp_path)

    assert sorted(removed) == [".agents", ".agy/skills", ".cline/skills", ".gemini/skills"]
    assert not agy_skills.exists()
    assert not gemini_skills.exists()
    assert not cline_skills.exists()
    assert not agents.exists()
    # Empty parent dirs removed as well
    assert not (tmp_path / ".agy").exists()
    assert not (tmp_path / ".gemini").exists()
    assert not (tmp_path / ".cline").exists()


def test_drop_legacy_root_skills_mirrors_noop_when_absent(tmp_path: Path) -> None:
    from ai_hats.plugin_dir import drop_legacy_root_skills_mirrors

    assert drop_legacy_root_skills_mirrors(tmp_path) == []
