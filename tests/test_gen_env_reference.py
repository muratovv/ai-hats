"""The generator behind `docs/reference-env.md`, and the staleness it must refuse.

A `--check` nobody has made fail proves nothing, so every source the page reads
from gets a positive control here: edit a declared default, or the literal at a
call site the generator reads by AST, and the check must go red until the page
is regenerated. What the check cannot see — a doc sentence drifting from its own
code — is stated on the page rather than tested, because it is not testable.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "gen_env_reference.py"


def _load():
    spec = importlib.util.spec_from_file_location("gen_env_reference", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load()


DECL = '''"""A stand-in for a distribution's env declarations."""


class Budget:
    __slots__ = ("name", "default", "doc")

    def __init__(self, name, default, doc):
        self.name, self.default, self.doc = name, default, doc


class Override:
    __slots__ = ("name", "default", "doc", "foreign", "pin", "sentinel")

    def __init__(self, name, default, doc, foreign=False, pin=None, sentinel=None):
        self.name, self.default, self.doc = name, default, doc
        self.foreign, self.pin, self.sentinel = foreign, pin, sentinel
'''

MAIN_DECL = (
    DECL
    + """

BUDGETS = (
    Budget("AI_HATS_HOOK_TIMEOUT_S", 60.0, "Seconds the hook chain for one tool call may spend."),
    Budget("AI_HATS_PIPELINE_KEEP_N", 10, "Pipeline-run directories kept before pruning."),
)

OVERRIDES = (
    Override("AI_HATS_DIR", "`<project>/.agent/ai-hats`", "The framework base dir.",
             pin="the session's base dir, written at spawn"),
    Override("AI_HATS_TRASH_DIR", "`$TMPDIR`/ai-hats", "Where destructive ops snapshot.",
             sentinel=("-", "hard-delete instead")),
    Override("XDG_CACHE_HOME", "", "Ranks under `AI_HATS_CACHE_HOME`.", True),
)
"""
)

SIBLING_DECL = (
    DECL
    + """

OVERRIDES = (
    Override("RACK_ROOTS_FILE", "`~/.ai-hats/roots.yaml`", "The cross-project roots registry."),
)
"""
)

PY_HOOK = '''"""A stand-in for the copied-in comment-length hook."""


def run(src):
    findings = _comment_run_findings(src, _env_int("AI_HATS_COMMENT_MAX_LINES", 3))
    findings += _docstring_findings(
        src,
        _env_int("AI_HATS_DOCSTRING_MAX_LINES", 10),
        _env_int("AI_HATS_DOCSTRING_MAX_CHARS", 700),
    )
    return findings
'''

SH_HOOK = """#!/usr/bin/env bash
gate_marker_sweep() {
    local keep="${AI_HATS_GATE_MARKER_KEEP_DAYS:-30}"
    find "$1" -type f -mtime "+${keep}" -delete
}
"""

MAIN_RELPATH = "src/fixture_main/knobs.py"
SIBLING_RELPATH = "packages/fixture-sibling/src/fixture_sibling/knobs.py"


def _hook_relpath(suffix: str) -> str:
    """The real relpath of a call site the generator reads its number out of."""
    return next(s.relpath for s in mod.HOOK_BUDGETS if s.relpath.endswith(suffix))


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    for relpath, body in (
        (MAIN_RELPATH, MAIN_DECL),
        (SIBLING_RELPATH, SIBLING_DECL),
        (_hook_relpath(".py"), PY_HOOK),
        (_hook_relpath(".sh"), SH_HOOK),
    ):
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    return root


def _check(root: Path) -> int:
    return mod.main(["--check", "--root", str(root)])


def _write(root: Path) -> int:
    return mod.main(["--write", "--root", str(root)])


def _page(root: Path) -> str:
    return (root / mod.PAGE_RELPATH).read_text(encoding="utf-8")


def _swap(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    assert old in text, f"{path} no longer contains {old!r} — the control cannot run"
    path.write_text(text.replace(old, new), encoding="utf-8")


# --- the two directions of the gate ----------------------------------------


def test_a_missing_page_is_refused_and_writing_it_clears_the_refusal(tmp_path):
    root = _tree(tmp_path)
    assert _check(root) == 1
    assert _write(root) == 0
    assert _check(root) == 0


def test_an_edited_declaration_goes_red_until_the_page_is_regenerated(tmp_path):
    root = _tree(tmp_path)
    _write(root)
    _swap(root / MAIN_RELPATH, '"AI_HATS_HOOK_TIMEOUT_S", 60.0', '"AI_HATS_HOOK_TIMEOUT_S", 90.0')
    assert _check(root) == 1
    assert _write(root) == 0
    assert _check(root) == 0
    assert "| 90.0 |" in _page(root)


def test_an_edited_call_site_literal_goes_red_until_the_page_is_regenerated(tmp_path):
    root = _tree(tmp_path)
    _write(root)
    _swap(root / _hook_relpath(".py"), 'MAX_CHARS", 700', 'MAX_CHARS", 900')
    assert _check(root) == 1
    assert _write(root) == 0
    assert _check(root) == 0
    assert "| 900 |" in _page(root)


def test_an_edited_shell_default_goes_red_until_the_page_is_regenerated(tmp_path):
    root = _tree(tmp_path)
    _write(root)
    _swap(root / _hook_relpath(".sh"), ":-30}", ":-45}")
    assert _check(root) == 1
    assert _write(root) == 0
    assert "| 45 |" in _page(root)


# --- a source that cannot be read is a named refusal, not a dropped row -----


def test_a_call_site_that_stopped_reading_the_name_is_a_named_refusal(tmp_path, capsys):
    root = _tree(tmp_path)
    _write(root)
    _swap(root / _hook_relpath(".py"), '_env_int("AI_HATS_COMMENT_MAX_LINES", 3)', "3")
    assert _check(root) == 1
    assert "AI_HATS_COMMENT_MAX_LINES" in capsys.readouterr().err


def test_a_tree_that_declares_nothing_is_a_named_refusal(tmp_path, capsys):
    root = _tree(tmp_path)
    (root / MAIN_RELPATH).unlink()
    (root / SIBLING_RELPATH).unlink()
    assert _check(root) == 1
    assert "BUDGETS or OVERRIDES" in capsys.readouterr().err


# --- what the page has to say ----------------------------------------------


def test_a_sibling_distribution_reaches_the_page(tmp_path):
    """Declarations are found by shape, so a distribution that cannot import the
    main package still gets its rows."""
    root = _tree(tmp_path)
    _write(root)
    assert "`RACK_ROOTS_FILE`" in _page(root)


def test_foreign_names_stand_apart_from_ours(tmp_path):
    root = _tree(tmp_path)
    _write(root)
    ours, _, foreign = _page(root).partition("## Honoured from other tools")
    assert "`AI_HATS_DIR`" in ours and "`XDG_CACHE_HOME`" not in ours
    assert "`XDG_CACHE_HOME`" in foreign
    assert "Not ours" in foreign


def test_a_pin_and_a_sentinel_survive_into_the_page(tmp_path):
    root = _tree(tmp_path)
    _write(root)
    page = _page(root)
    assert "`AI_HATS_DIR` †" in page and "written at spawn" in page
    assert "path \\| `-`" in page and "Set to `-` to hard-delete instead." in page


def test_the_page_names_the_families_it_leaves_out(tmp_path):
    root = _tree(tmp_path)
    _write(root)
    page = _page(root)
    assert "Bypass hatches" in page and "constants.py" in page
    assert "Spawn-envelope" in page and "ADR-0025" in page and "ADR-0020" in page


def test_the_committed_page_is_current():
    """The same claim the `env-reference` CI stage makes, on the real tree."""
    assert mod.main(["--check"]) == 0
