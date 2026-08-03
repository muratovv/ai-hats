"""HATS-1407 — the two bypass-journal writers must not drift apart.

The shell twin serves git hooks, the Python twin serves the stdlib-only
PreToolUse hooks. They are separate implementations of one line format, so a
field added to one and forgotten in the other would split the journal into two
incompatible halves that a reader silently mis-parses.
"""

from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HOOKS = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library/hooks"
SH = HOOKS / "bypass_journal.sh"
PY = HOOKS / "bypass_journal.py"


def _python_fields() -> tuple[str, ...]:
    spec = importlib.util.spec_from_file_location("bypass_journal", PY)
    module = importlib.util.module_from_spec(spec)
    sys.modules["bypass_journal"] = module
    spec.loader.exec_module(module)
    return module.FIELDS


def _shell_fields() -> tuple[str, ...]:
    """Read the exported schema by sourcing the file, as a consumer would."""
    res = subprocess.run(
        ["bash", "-c", f'. "{SH}" && printf "%s" "$AI_HATS_BYPASS_FIELDS"'],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    return tuple(res.stdout.split())


LIBRARY = HOOKS.parent


def _skill_hook_scripts() -> list[Path]:
    """Every script under a skill's `hooks/` dir, both library layers."""
    return sorted(
        p
        for layer in ("core", "usage")
        for p in (LIBRARY / layer / "skills").glob("*/hooks/*")
        if p.suffix in (".py", ".sh") and not p.name.startswith("bypass_journal")
    )


def _required_helper(script: Path) -> Path | None:
    """The journal twin a script needs beside it, or None if it journals nothing.

    Any sibling spelling counts — `$(dirname "$0")/` and `${HOOK_DIR}/` are both
    in use. A git gate reaches one directory up (`../bypass_journal.sh`) and is
    deliberately not a match.
    """
    body = script.read_text(errors="replace")
    if script.suffix == ".py" and "from bypass_journal import" in body:
        return script.parent / "bypass_journal.py"
    if script.suffix == ".sh" and "bypass_journal.sh" in body.replace("../bypass_journal.sh", ""):
        return script.parent / "bypass_journal.sh"
    return None


def test_every_journalling_skill_hook_has_the_helper_beside_it():
    """HATS-1268 — the helper is a sibling, so it must ship as one.

    The flat `library/hooks/` copy used to manufacture that sibling; hooks now
    execute from the session skill mirror, which copies skill dirs verbatim.
    A consumer added without its copy degrades to the NOT RECORDED stub, which
    returns cleanly — invisible to every other test. Scoped to `hooks/`:
    `git_hooks/` scripts reach the helper via the `.githooks/` layout until
    HATS-1337 moves them in place.
    """
    missing = [
        str(script.relative_to(REPO_ROOT))
        for script in _skill_hook_scripts()
        if (helper := _required_helper(script)) is not None and not helper.is_file()
    ]
    assert not missing, f"hook scripts journalling without a sibling helper: {missing}"


def test_every_shipped_helper_copy_is_byte_identical_to_the_canon():
    """Package data stays the canon; git is what keeps the copies in sync."""
    canon = {"bypass_journal.py": PY.read_bytes(), "bypass_journal.sh": SH.read_bytes()}
    drifted = [
        str(copy.relative_to(REPO_ROOT))
        for layer in ("core", "usage")
        for copy in (REPO_ROOT / HOOKS.parent / layer / "skills").glob("*/hooks/bypass_journal.*")
        if copy.read_bytes() != canon[copy.name]
    ]
    assert not drifted, f"copies drifted from package data: {drifted}"


def test_the_two_writers_declare_the_same_fields():
    assert _shell_fields() == _python_fields()


def test_the_shell_printf_emits_exactly_its_declared_fields():
    """The exported list is only a claim — check it against what is written."""
    emitted = tuple(re.findall(r'"([a-z_]+)":"%s"', SH.read_text()))
    assert emitted == _shell_fields()


@pytest.mark.integration
def test_both_writers_produce_the_same_keys_on_a_real_repo(tmp_path: Path):
    """End-to-end: one repo, one line from each writer, identical key sets."""
    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)

    subprocess.run(
        ["bash", "-c", f'. "{SH}" && ai_hats_journal_bypass hatch SHELL_VAR'],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(HOOKS)!r});"
            " from bypass_journal import journal_bypass;"
            " journal_bypass('hatch', 'PY_VAR')",
        ],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    lines = (tmp_path / ".git/ai-hats/bypasses.jsonl").read_text().splitlines()
    assert len(lines) == 2, lines
    shell_entry, py_entry = (json.loads(line) for line in lines)

    assert set(shell_entry) == set(py_entry)
    assert shell_entry["reason"] == "SHELL_VAR"
    assert py_entry["reason"] == "PY_VAR"


@pytest.mark.integration
def test_both_writers_handle_multiline_and_escaping_identically(tmp_path: Path):
    """Assert both shell and python writers emit strictly valid single-line JSONL for multiline commands and quotes."""
    import os

    subprocess.run(["git", "init", "--quiet"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.email", "t@e.x"], cwd=str(tmp_path), check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=str(tmp_path), check=True)

    multiline_cmd = 'echo "hello"\necho "world"\ttab'

    env = dict(os.environ)
    env["CMD_VAL"] = multiline_cmd
    subprocess.run(
        [
            "bash",
            "-c",
            f'. "{SH}" && ai_hats_journal_bypass hatch SHELL_VAR "$CMD_VAL" "session-1"',
        ],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
        env=env,
    )

    subprocess.run(
        [
            sys.executable,
            "-c",
            f"import sys; sys.path.insert(0, {str(HOOKS)!r});"
            " from bypass_journal import journal_bypass;"
            " journal_bypass('hatch', 'PY_VAR', cmd=sys.argv[1], session_id='session-1')",
            multiline_cmd,
        ],
        cwd=str(tmp_path),
        check=True,
        capture_output=True,
    )

    raw_content = (tmp_path / ".git/ai-hats/bypasses.jsonl").read_text()
    lines = raw_content.splitlines()
    assert len(lines) == 2, f"Expected 2 lines in JSONL, got {len(lines)}. Raw:\n{raw_content}"

    shell_entry = json.loads(lines[0])
    py_entry = json.loads(lines[1])

    assert shell_entry["cmd"] == multiline_cmd
    assert py_entry["cmd"] == multiline_cmd
