"""Characterization goldens per (surface, run_mode) — the refactor's net (HATS-1207 S1).

Pins the whole ``--dry-run --json`` payload for all six pairs under the default
policy: values, not shapes. The predecessor asserted only ``isinstance(…, list)``
and stayed green across S2–S4 while three behaviours changed under it — a net
that cannot fail reads as coverage without being any.

Recorded AFTER S2–S4 landed, not before as the plan intended (the slices were
committed first), so this is forward regression cover, not proof those slices
preserved bytes; pre-refactor payloads are at ``2914fa6e``. Synthetic library +
pinned ``HOME`` keep the payload dependent on this repo's code alone — but agy
bakes absolute paths into ``hooks.json``, so adding a hook script to the fixture
skill would make these goldens tmp-path flaky.
"""
# comment-length: allow — the provenance caveat must not be trimmed away: a
# reader who believes these goldens prove S2-S4 preserved bytes is misled.

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from ai_hats.assembler import Assembler
from ai_hats.dry_run import dry_run_automate, dry_run_hitl
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG, cache_root
from ai_hats.session_artifacts import SessionPolicy

SURFACES = ["claude", "agy", "cline"]
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "artifact_builder_goldens"

# Set to re-record every golden. Regenerating is a deliberate act: read the diff
# and be able to say which slice caused each line of it, or you have laundered a
# regression into the baseline.
UPDATE = os.environ.get("AI_HATS_UPDATE_GOLDENS") == "1"


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A composable project on a synthetic library, isolated from the user's home."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir()

    lib = tmp_path / "lib"
    skill = lib / "skills" / "s"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("---\nname: s\ndescription: x\n---\n# body\n")
    role = lib / "roles" / "test-role"
    role.mkdir(parents=True)
    (role / "config.yaml").write_text(
        "name: test-role\npriorities:\n  - Quality\n"
        "composition:\n  skills: [s]\ninjection: Role body.\n"
    )

    proj = tmp_path / "proj"
    proj.mkdir()
    ProjectConfig(provider="claude", library_paths=[str(lib)]).save(proj / PROJECT_CONFIG)
    asm = Assembler(proj, library_paths=[lib])
    asm.init()
    asm.set_role("test-role", provider_name="claude")
    return proj


def _normalize(obj, subs: list[tuple[str, str]]):
    """Rewrite machine-specific absolute paths to stable tokens, recursively."""
    if isinstance(obj, str):
        for needle, token in subs:
            obj = obj.replace(needle, token)
        return obj
    if isinstance(obj, list):
        return [_normalize(o, subs) for o in obj]
    if isinstance(obj, dict):
        return {k: _normalize(v, subs) for k, v in obj.items()}
    return obj


def _payload(report, project: Path) -> dict:
    # Longest first: <project> lives under <tmp>, so substituting <tmp> first
    # would leave a half-rewritten project path behind.
    # <cache> first: it lives under <tmp> and carries a path-derived digest that
    # would otherwise pin a machine-specific key into the golden (HATS-1398).
    subs = [
        (str(cache_root(project)), "<cache>"),
        (str(project), "<project>"),
        (str(project.parent / "home"), "<home>"),
        (str(project.parent), "<tmp>"),
    ]
    return _normalize(report.to_dict(), subs)


def _assert_golden(name: str, payload: dict) -> None:
    path = GOLDEN_DIR / f"{name}.json"
    if UPDATE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        pytest.skip(f"recorded golden {name}")
    assert path.is_file(), f"missing golden {path} — record it with AI_HATS_UPDATE_GOLDENS=1"
    expected = json.loads(path.read_text())
    assert payload == expected, (
        f"{name}: the delivered session changed.\n"
        "If the change is intended, say which slice caused it, then re-record "
        "with AI_HATS_UPDATE_GOLDENS=1."
    )


@pytest.mark.parametrize("surface", SURFACES)
def test_golden_hitl_default_policy(project: Path, surface: str):
    report = dry_run_hitl(project, role="test-role", provider=surface, policy=SessionPolicy())

    _assert_golden(f"{surface}-hitl", _payload(report, project))


@pytest.mark.parametrize("surface", SURFACES)
def test_golden_automate_default_policy(project: Path, surface: str):
    report = dry_run_automate(
        project, role="test-role", provider=surface, task="demo", policy=SessionPolicy()
    )

    _assert_golden(f"{surface}-automate", _payload(report, project))
