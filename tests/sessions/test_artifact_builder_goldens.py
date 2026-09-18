"""Characterization goldens per (surface, run_mode) — the refactor's net (HATS-1207 S1).

Pins the whole ``--dry-run-json`` payload for every registered surface in both
run modes under the default policy: values, not shapes. The predecessor asserted
only ``isinstance(…, list)`` and stayed green across S2–S4 while three
behaviours changed under it — a net that cannot fail reads as coverage without
being any.

Synthetic library + pinned ``HOME`` keep the payload dependent on this repo's
code alone. Two things a golden cannot pin and blanks instead, each named at
the point it is blanked: a digest that folds an absolute path in, and the bytes
of a file that embeds the temp project's paths.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from ai_hats_core.layout import ProjectLayout

from ai_hats.assembler import Assembler
from ai_hats.models import ProjectConfig
from ai_hats.paths import PROJECT_CONFIG
from ai_hats.session_artifacts import RunMode, assemble_brief
from ai_hats.session_plan import preview
from ai_hats.surface_registry import surface_names

SURFACES = sorted(surface_names())
GOLDEN_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "artifact_builder_goldens"

# Set to re-record every golden. Regenerating is a deliberate act: read the diff
# and be able to say which slice caused each line of it, or you have laundered a
# regression into the baseline.
UPDATE = os.environ.get("AI_HATS_UPDATE_GOLDENS") == "1"

#: Files whose bytes embed the temp project's absolute paths, per surface —
#: their digest and size cannot match across two machines by construction.
PATH_EMBEDDING = {
    "codex": {".ai-hats-session.json"},
    "opencode": {"opencode.json", "hooks.json"},
}


@pytest.fixture
def project(tmp_path: Path, monkeypatch) -> Path:
    """A composable project on a synthetic library, isolated from the user's
    home; an empty Codex home stands where that surface insists on one."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    (tmp_path / "home" / ".codex").mkdir(parents=True)
    for name in ("AI_HATS_CODEX_BASE_HOME", "CODEX_HOME", "XDG_CONFIG_HOME", "GEMINI_CONFIG_DIR"):
        monkeypatch.delenv(name, raising=False)

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


def _payload(record: dict, project: Path, surface: str) -> dict:
    # Longest first: <project> lives under <tmp>, so substituting <tmp> first
    # would leave a half-rewritten project path behind.
    # <cache> first: it lives under <tmp> and carries a path-derived digest that
    # would otherwise pin a machine-specific key into the golden (HATS-1398);
    # codex spells that key alone inside its session home, so it goes last.
    cache = ProjectLayout.at(project).cache.root
    subs = [
        (str(cache), "<cache>"),
        (str(project), "<project>"),
        (str(project.parent / "home"), "<home>"),
        (str(project.parent), "<tmp>"),
        (cache.name, "<cache-key>"),
    ]
    payload = _normalize(record, subs)
    # The composition's digests fold absolute library paths in (a tree at two
    # paths is two skills), so under tmp they are machine-specific by design;
    # the content digests beside them are what a golden can pin.
    payload["composition"] = _blank_path_derived(payload["composition"])
    for row in payload["materialized"]:
        if Path(row["target"]).name in PATH_EMBEDDING.get(surface, ()):
            row["digest"] = row["size"] = "<path-embedding bytes>"
    return payload


def _blank_path_derived(obj):
    if isinstance(obj, dict):
        return {
            k: ("<path-derived>" if k == "digest" else _blank_path_derived(v))
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_blank_path_derived(o) for o in obj]
    return obj


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
    shown = preview(
        ProjectLayout.at(project), role="test-role", provider=surface, run_mode=RunMode.HITL
    )

    _assert_golden(f"{surface}-hitl", _payload(shown.record, project, surface))


@pytest.mark.parametrize("surface", SURFACES)
def test_golden_automate_default_policy(project: Path, surface: str):
    layout = ProjectLayout.at(project)
    shown = preview(
        layout,
        role="test-role",
        provider=surface,
        run_mode=RunMode.AUTOMATE,
        brief=assemble_brief(layout, task="demo", ticket_id=""),
    )

    _assert_golden(f"{surface}-automate", _payload(shown.record, project, surface))


def test_every_registered_surface_has_its_two_goldens():
    """A surface added later must record its pair, not slip past the net."""
    recorded = {p.stem for p in GOLDEN_DIR.glob("*.json")}
    assert recorded == {f"{s}-{m}" for s in SURFACES for m in ("hitl", "automate")}
