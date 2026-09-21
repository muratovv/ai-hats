"""HATS-1917 — a hook row's tag must name the row, not just its matcher.

A skill may now declare two scripts on one matcher. The tag is what the catch
journal, the gate-broken channel and a refusal message call the hook that acted,
so two rows sharing one label would report a firing without saying which gate
fired — against exactly the telemetry HATS-1634 built.

All five surfaces are checked on the manifest their plan writes: they derive
the rows from one ``manifest_rows``, so this holds the derivation AND that every
surface still puts it in its manifest. The composition is real — a fixture skill
on disk, no patched imports — so this also exercises the validator that now
allows the second row at all.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from ai_hats_core import ComponentKind, CompositionResult, ResolvedComponent
from ai_hats_core.layout import ProjectLayout

from ai_hats.materialization import WriteKind
from ai_hats.surface_registry import get_surface, surface_names
from tests._plan_helpers import composition_of, planned

SKILL = "two-guards"
EVENT = "PreToolUse"
SESSION_ID = "test-session-id"


def _skill_with_two_bash_hooks(base: Path) -> ResolvedComponent:
    """A skill dir declaring two PreToolUse/Bash scripts, both present on disk."""
    skill_dir = base / SKILL
    skill_dir.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {SKILL}", "ai_hats:", "  runtime_hooks:", f"    {EVENT}:"]
    for script in ("hooks/first_gate.sh", "hooks/second_gate.sh"):
        lines += ["      - matcher: Bash", f"        script: {script}"]
        sp = skill_dir / script
        sp.parent.mkdir(parents=True, exist_ok=True)
        sp.write_text("#!/usr/bin/env bash\nexit 0\n")
        sp.chmod(0o755)  # a declared gate ships executable (HATS-1862 drops one that does not)
    lines += ["---", f"# {SKILL}"]
    (skill_dir / "SKILL.md").write_text("\n".join(lines) + "\n")
    return ResolvedComponent(name=SKILL, component_type=ComponentKind.SKILL, source_path=skill_dir)


def _result(skill: ResolvedComponent) -> CompositionResult:
    return CompositionResult(name="r", priorities=[], rules=[], skills=[skill], injections=[])


@pytest.fixture
def homes(tmp_path: Path, monkeypatch) -> None:
    """The surfaces that project a person's home plan from a pinned one."""
    codex_home = tmp_path / "codex-home"
    codex_home.mkdir()
    monkeypatch.setenv("AI_HATS_CODEX_BASE_HOME", str(codex_home))
    monkeypatch.delenv("CODEX_HOME", raising=False)
    monkeypatch.delenv("CODEX_SQLITE_HOME", raising=False)
    monkeypatch.setenv("AI_HATS_OPENCODE_CONFIG_HOME", str(tmp_path / "config-home"))


def _rows(tmp_path: Path, surface_name: str) -> list[dict]:
    project = tmp_path / "proj"
    project.mkdir(exist_ok=True)
    layout = ProjectLayout.at(project)
    surface = get_surface(surface_name)
    plan = planned(
        surface,
        composition_of(_result(_skill_with_two_bash_hooks(tmp_path / "skills")), layout=layout),
        layout=layout,
        root=layout.cache.session(SESSION_ID),
    )
    manifest = next(
        e for e in plan.entries if e.kind is WriteKind.WRITE_TEXT and e.target.name == "hooks.json"
    )
    document = json.loads(manifest.content)
    # agy's dispatcher reads the flat ``{event: rows}``; the others a versioned envelope.
    rows = document["hooks"] if "version" in document else document
    return rows.get(EVENT, [])


@pytest.mark.parametrize("surface", sorted(surface_names()))
def test_both_rows_survive_with_distinct_tags(tmp_path, homes, surface):
    rows = _rows(tmp_path, surface)
    assert len(rows) == 2, f"{surface}: a row went missing: {rows}"
    tags = [row["tag"] for row in rows]
    assert len(set(tags)) == 2, f"{surface}: two rows share one tag: {tags}"


@pytest.mark.parametrize("surface", sorted(surface_names()))
def test_the_tag_still_starts_with_the_owner_and_skill(tmp_path, homes, surface):
    """The sweeper matches the `ai-hats:` prefix and the display name reads
    segment 1, so extending the tag must not disturb its head."""
    for row in _rows(tmp_path, surface):
        assert row["tag"].startswith(f"ai-hats:{SKILL}:{EVENT}:Bash"), row["tag"]
