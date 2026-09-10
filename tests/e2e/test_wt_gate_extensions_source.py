"""e2e (HATS-1829)

flow:   a maintainer widens the worktree gate by adding an extension to
        `code_extensions.json` inside the project's own copy of the skill
cmds:
    ai-hats self init -p claude -r maintainer --no-wizard
expect: the file the composed PreToolUse chain allowed before that edit is denied
        after it, with nothing else changed
why:    the JSON is the documented knob, and the only road it reaches the gate by is
        the copy the session materializes beside the hook — the second source path
        the script carried pointed at a `library/` prefix no layout has produced
        since the monorepo move, so nothing tested whether the live road works

flow:   a maintainer runs a session whose materialized skill lost that data file
cmds:
    ai-hats self init -p claude -r maintainer --no-wizard
expect: the gate still denies a .py edit in the main checkout, and the bypass journal
        says the verdict came from the embedded defaults
why:    falling through to `_DEFAULT_LANGS` in silence is what kept a dead source path
        looking alive for months
"""
# comment-length: allow — the four-field catalog block, schema in gen_e2e_catalog.py

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from _helpers.git import init_repo
from _helpers.hook_chain import build_session_settings, pretooluse_hooks, run_tool_chain

pytestmark = [pytest.mark.integration, pytest.mark.guards, pytest.mark.wt]

SKILL = "worktree-isolation"
EXTS_FILENAME = "code_extensions.json"
JOURNAL_REL = ".git/ai-hats/bypasses.jsonl"

#: An extension no shipped language group lists, so a deny on it can come from
#: nowhere but the project's own JSON.
PROJECT_EXT = ".zzz"


def _builtin_skill_dir() -> Path:
    """The shipped skill source, asked of the engine rather than spelled out —
    a hard-coded layer path is the very thing this card came from."""
    from ai_hats.paths import builtin_library_root

    root = builtin_library_root()
    assert root is not None, "no builtin library root — the install under test is broken"
    return root / "core" / "skills" / SKILL


def _install_project_override(project: Path) -> Path:
    """Put the skill in the project's own library layer, one extension richer.

    A whole-directory copy is what an override IS here: components resolve
    last-wins per directory, so `libraries/skills/<name>/` replaces the shipped
    one rather than merging into it.
    """
    dest = project / "libraries" / "skills" / SKILL
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        _builtin_skill_dir(),
        dest,
        dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    exts = dest / "hooks" / EXTS_FILENAME
    data = json.loads(exts.read_text())
    data["project_local"] = [PROJECT_EXT]
    exts.write_text(json.dumps(data, indent=2))
    return dest


def _wired_gate(settings: Path) -> Path:
    """The wt_gate command the session wired onto Edit, as a path."""
    wired = [c for c in pretooluse_hooks(settings, "Edit") if "wt_gate" in c]
    assert wired, f"worktree-isolation wt_gate is not wired in {settings} — scene did not play"
    return Path(wired[0].split()[0])


def _journal_lines(journal: Path) -> list[str]:
    """Journal records so far — an absent file is zero of them, not a crash."""
    return journal.read_text().splitlines() if journal.is_file() else []


def _edit(project: Path, env: dict, settings: Path, target: Path):
    return run_tool_chain(
        project,
        "Edit",
        {"file_path": str(target), "old_string": "a", "new_string": "b"},
        settings=settings,
        env=env,
    )


@pytest.fixture(scope="module")
def project(shared_launcher, tmp_path_factory):
    """A real maintainer project in a git repo — maintainer composes
    worktree-isolation, and the gate only speaks inside a main checkout."""
    launcher, base_env, _venv = shared_launcher
    env = dict(base_env)
    env.pop("PYTHONPATH", None)
    env["HOME"] = str(tmp_path_factory.mktemp("wt-gate-exts-home"))

    proj = tmp_path_factory.mktemp("wt-gate-exts-proj")
    init_repo(proj)
    result = subprocess.run(  # noqa: S603 - launcher path from the session fixture
        [str(launcher), "self", "init", "-p", "claude", "-r", "maintainer", "--no-wizard"],
        cwd=str(proj),
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode != 0:
        raise AssertionError(f"self init failed:\n{result.stdout}\n{result.stderr}")
    return proj, env


def test_an_extension_added_to_the_projects_json_changes_the_verdict(project):
    """One file, two sessions, opposite verdicts — the only difference is the JSON.

    The first verdict is this test's positive control: without it, a gate that
    denied everything would pass the second assertion just as well.
    """
    proj, env = project
    probe = proj / f"probe{PROJECT_EXT}"
    probe.write_text("payload\n")

    stock = build_session_settings(proj, role="maintainer", session_id="sid-exts-stock")
    before = _edit(proj, env, stock, probe)
    assert not before.gated, f"{PROJECT_EXT} is in no shipped language group; got {before}"

    _install_project_override(proj)

    overridden = build_session_settings(proj, role="maintainer", session_id="sid-exts-project")
    after = _edit(proj, env, overridden, probe)
    assert after.denied, f"the project's own {EXTS_FILENAME} must drive the gate; got {after}"
    assert SKILL in after.reason, f"the deny must name the discipline it enforces; got {after}"


def test_a_missing_extensions_file_is_recorded_before_the_defaults_decide(project):
    """The gate keeps working off `_DEFAULT_LANGS` — and now says so (HATS-1829).

    Fail-under-revert: drop the journal call from `_load_extensions` and the
    verdict below is unchanged, which is exactly the blindness this asserts away.
    """
    proj, env = project
    settings = build_session_settings(proj, role="maintainer", session_id="sid-exts-degraded")
    (_wired_gate(settings).parent / EXTS_FILENAME).unlink()

    journal = proj / JOURNAL_REL
    before = len(_journal_lines(journal))

    code = proj / "probe.py"
    code.write_text("x = 1\n")
    verdict = _edit(proj, env, settings, code)
    assert verdict.denied, f"the embedded defaults still cover .py; got {verdict}"

    added = [json.loads(line) for line in _journal_lines(journal)[before:]]
    degraded = [r for r in added if r.get("hook") == "wt_gate.py" and r.get("kind") == "degraded"]
    assert degraded, f"the fall-through to the defaults went unrecorded; journal added {added}"
    assert EXTS_FILENAME in degraded[0].get("reason", ""), (
        f"the record must name the file that went missing; got {degraded[0]}"
    )
