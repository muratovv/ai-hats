"""Tests for per-session plugin-dir materialization (HATS-307, HATS-294)."""

from __future__ import annotations

import json
import multiprocessing as mp
from pathlib import Path

import pytest

from ai_hats.composer import ResolvedComponent
from ai_hats.models import ComponentType
from ai_hats.plugin_dir import materialize_plugin_dir


def _make_skill(name: str, root: Path, body: str = "") -> ResolvedComponent:
    """Build a skill source dir on disk and the matching ResolvedComponent."""
    skill_dir = root / name
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body or f"---\nname: {name}\n---\n# {name}\n")
    return ResolvedComponent(
        name=name,
        component_type=ComponentType.SKILL,
        source_path=skill_dir,
        injection=body,
    )


def test_returns_target_plugin_dir(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    target = tmp_path / "session_cache" / "plugin"
    out = materialize_plugin_dir("test-role", [skill], tmp_path, target)
    assert out == target
    assert out.is_dir()


def test_plugin_json_shape(tmp_path: Path) -> None:
    target = tmp_path / "plugin"
    out = materialize_plugin_dir("judge-for-role", [], tmp_path, target)
    manifest = json.loads((out / ".claude-plugin" / "plugin.json").read_text())
    assert manifest["name"] == "ai-hats-judge-for-role"
    assert "version" in manifest


def test_copies_skill_directory(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill(
        "role-coherence-protocol",
        skills_root,
        body="---\nname: role-coherence-protocol\ndescription: x\n---\n# body\n",
    )
    out = materialize_plugin_dir("judge-for-role", [skill], tmp_path, tmp_path / "plugin")
    copied = out / "skills" / "role-coherence-protocol" / "SKILL.md"
    assert copied.exists()
    assert "role-coherence-protocol" in copied.read_text()


def test_copies_non_skill_md_assets_verbatim(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    # Drop a non-SKILL.md asset alongside; must be preserved.
    (skill.source_path / "fixture.txt").write_text("RAW_ASSET_<ai_hats_dir>")
    out = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")
    asset = out / "skills" / "alpha" / "fixture.txt"
    assert asset.exists()
    # Verbatim — placeholder must NOT be expanded in non-SKILL.md files.
    assert asset.read_text() == "RAW_ASSET_<ai_hats_dir>"


def test_expands_placeholder_in_skill_md(tmp_path: Path) -> None:
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill(
        "beta",
        skills_root,
        body="see <ai_hats_dir>/state for details",
    )
    out = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")
    body = (out / "skills" / "beta" / "SKILL.md").read_text()
    assert "<ai_hats_dir>" not in body
    assert ".agent/ai-hats/state" in body


def test_empty_skills_list_makes_empty_skills_dir(tmp_path: Path) -> None:
    out = materialize_plugin_dir("test-role", [], tmp_path, tmp_path / "plugin")
    skills_dir = out / "skills"
    assert skills_dir.is_dir()
    assert list(skills_dir.iterdir()) == []


def test_skips_non_directory_source_path(tmp_path: Path) -> None:
    # A ResolvedComponent whose source_path points to a file (not a dir)
    # must be skipped without raising.
    rogue = ResolvedComponent(
        name="rogue",
        component_type=ComponentType.SKILL,
        source_path=tmp_path / "missing-dir",
        injection="",
    )
    out = materialize_plugin_dir("test-role", [rogue], tmp_path, tmp_path / "plugin")
    skills_dir = out / "skills"
    assert list(skills_dir.iterdir()) == []


def test_overwrites_existing_target(tmp_path: Path) -> None:
    """HATS-294: target dir is wiped before population so the result is
    byte-stable for the same inputs (Fork E determinism contract).
    """
    target = tmp_path / "plugin"
    target.mkdir()
    (target / "leftover.txt").write_text("stale")
    materialize_plugin_dir("test-role", [], tmp_path, target)
    assert not (target / "leftover.txt").exists()
    assert (target / ".claude-plugin" / "plugin.json").exists()


def test_parallel_invocations_with_distinct_targets(tmp_path: Path) -> None:
    """Callers pass distinct targets to get isolated plugin dirs."""
    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill_a = _make_skill("alpha", skills_root)
    skill_b = _make_skill("beta", tmp_path / "src2")
    out_a = materialize_plugin_dir("role-a", [skill_a], tmp_path, tmp_path / "a")
    out_b = materialize_plugin_dir("role-b", [skill_b], tmp_path, tmp_path / "b")
    assert out_a != out_b
    assert (out_a / "skills" / "alpha").is_dir()
    assert (out_b / "skills" / "beta").is_dir()
    assert not (out_a / "skills" / "beta").exists()
    assert not (out_b / "skills" / "alpha").exists()


# Module-level so it is picklable under the multiprocessing "spawn" start
# method (the default on macOS; forced explicitly below for determinism).
def _hammer_materialize(args: tuple) -> list[str]:
    plugin_dir, project_dir, skills, iters, barrier = args
    barrier.wait()  # release all workers into the critical section together
    errors: list[str] = []
    for _ in range(iters):
        try:
            materialize_plugin_dir("stress-role", skills, project_dir, plugin_dir)
        except Exception as exc:  # noqa: BLE001 — record every failure mode
            errors.append(f"{type(exc).__name__}: {exc}")
    return errors


@pytest.mark.integration
def test_concurrent_same_target_is_safe(tmp_path: Path) -> None:
    """HATS-604: concurrent materialize on ONE shared target must be safe.

    ``materialize_plugin_dir`` was a non-atomic
    ``rmtree -> mkdir -> per-skill copytree``. Under process contention two
    callers that share a per-session plugin dir shredded each other
    (``ENOTEMPTY`` / ``EEXIST`` / ``ENOENT``). A per-dir ``filelock``
    serialises the rebuild.

    Fails-under-revert: on the pre-fix body the baseline errors on ~all of
    ``n_procs * iters`` calls, so the zero-errors assertion is reliably RED.

    Real subprocesses (``multiprocessing`` spawn) — faithful to the
    cross-process fcntl-advisory lock contract that same-process threads
    would NOT exercise; hence ``@pytest.mark.integration``.
    """
    src = tmp_path / "src"
    src.mkdir()
    # A handful of non-trivial skills so each copytree takes long enough to
    # widen the race window.
    skills = [
        _make_skill(f"skill-{i}", src, body="x" * 400 + f"\n# skill {i}\n")
        for i in range(6)
    ]
    # ALL workers target this ONE dir — models the per-session plugin-dir
    # collision (two processes that resolved the same session_id).
    target = tmp_path / "cache" / "sid" / "plugin"

    n_procs, iters = 6, 15
    ctx = mp.get_context("spawn")
    with ctx.Manager() as mgr:
        barrier = mgr.Barrier(n_procs)
        with ctx.Pool(n_procs) as pool:
            results = pool.map(
                _hammer_materialize,
                [(target, tmp_path, skills, iters, barrier) for _ in range(n_procs)],
            )

    errors = [e for sub in results for e in sub]
    assert not errors, (
        f"{len(errors)}/{n_procs * iters} concurrent materialize calls raced — "
        f"sample: {errors[:3]}"
    )
    # Crash-free is necessary but not sufficient — the final dir must be a
    # VALID plugin (byte-stable-rebuild contract holds under contention).
    manifest = target / ".claude-plugin" / "plugin.json"
    assert manifest.is_file(), "final plugin dir missing manifest"
    got = sorted(p.name for p in (target / "skills").iterdir())
    assert got == sorted(s.name for s in skills), (
        f"final skills set {got} != expected {sorted(s.name for s in skills)}"
    )


# ---------- duplicate_skill_registrations (HATS-901) ----------


def test_duplicate_registration_identical_user_copy(tmp_path: Path) -> None:
    """A byte-identical copy under `~/.claude/skills/` is a provable redundant
    duplicate — Claude Code registers it alongside the session plugin."""
    import shutil

    from ai_hats.plugin_dir import duplicate_skill_registrations

    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    plugin = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")

    home = tmp_path / "home"
    user_copy = home / ".claude" / "skills" / "alpha"
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

    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    plugin = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")

    home = tmp_path / "home"
    stale = home / ".claude" / "skills" / "alpha"
    stale.mkdir(parents=True)
    (stale / "SKILL.md").write_text("# frozen at an old library version\n")

    found = duplicate_skill_registrations(
        ["alpha"], project_dir=tmp_path, plugin_skills_root=plugin / "skills", home=home
    )

    assert [(c.name, c.verdict) for c in found] == [("alpha", "differs")]


def test_duplicate_registration_marker_listed_is_managed(tmp_path: Path) -> None:
    """Project-scope dir listed in `.ai-hats-managed` → ownership proven by the
    marker; the next `self bump` removes it."""
    from ai_hats.plugin_dir import duplicate_skill_registrations

    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    project = tmp_path / "project"
    plugin = materialize_plugin_dir("test-role", [skill], project, tmp_path / "plugin")

    mirror = project / ".claude" / "skills"
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


def test_duplicate_registration_none_when_clean(tmp_path: Path) -> None:
    """No same-name dirs anywhere → empty list (the everyday no-op path)."""
    from ai_hats.plugin_dir import duplicate_skill_registrations

    skills_root = tmp_path / "src"
    skills_root.mkdir()
    skill = _make_skill("alpha", skills_root)
    plugin = materialize_plugin_dir("test-role", [skill], tmp_path, tmp_path / "plugin")

    found = duplicate_skill_registrations(
        ["alpha"], project_dir=tmp_path, plugin_skills_root=plugin / "skills", home=tmp_path / "home"
    )

    assert found == []
