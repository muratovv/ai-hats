"""HATS-1856 — `test_runners.json` is ONE source of runner names, and stays one.

Both Bash guards ship an embedded mirror for the case where the file is
unreadable. A mirror is only a fallback while it still says what the file says;
once it drifts, the two guards decide by different lists depending on whether a
read succeeded, which is worse than either list alone. These tests are what keeps
them equal.
"""

import importlib.util
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"
SHARED = LIB / "hooks/test_runners.json"
WT_HOOK = LIB / "core/skills/worktree-isolation/hooks/wt_interpreter_gate.py"
HYGIENE_HOOK = LIB / "core/skills/tool-call-hygiene/hooks/tool_call_hygiene_guard.sh"
LINKS = (
    LIB / "core/skills/worktree-isolation/hooks/test_runners.json",
    LIB / "core/skills/tool-call-hygiene/hooks/test_runners.json",
)


def _runners() -> dict:
    return json.loads(SHARED.read_text())


def _load_wt_hook():
    spec = importlib.util.spec_from_file_location("wt_interpreter_gate", WT_HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _expected_alternation() -> str:
    """The alternation ``build_runner_alt`` produces, spelled in Python."""
    data = _runners()
    names = [
        entry
        for group in ("python_runners", "standalone_checkers", "delegating", "foreign_runners")
        for entry in data.get(group) or ()
    ]
    return "|".join(n.replace(".", r"\.").replace(" ", "[[:space:]]+") for n in names)


def test_every_consumer_links_the_same_file():
    for link in LINKS:
        assert link.is_symlink(), f"{link} must be a symlink, not a copy"
        assert link.resolve() == SHARED.resolve(), f"{link} -> {link.resolve()}"


def test_groups_are_non_empty_lists_of_strings():
    data = _runners()
    for group in (
        "python_interpreters",
        "python_runners",
        "standalone_checkers",
        "delegating",
        "foreign_runners",
    ):
        entries = data.get(group)
        assert isinstance(entries, list) and entries, f"{group} must be a non-empty list"
        assert all(isinstance(e, str) and e.strip() for e in entries), group


def test_python_hook_mirror_matches_the_shared_file():
    mod = _load_wt_hook()
    data = _runners()

    def heads(*groups):
        out = []
        for group in groups:
            for entry in data.get(group) or ():
                head = entry.split()[0]
                if head not in out:
                    out.append(head)
        return tuple(out)

    assert mod._DEFAULT_INTERPRETERS == heads("python_interpreters", "python_runners")
    assert mod._DEFAULT_DELEGATES == heads("delegating")


def test_python_hook_reads_the_file_not_the_mirror():
    """Positive control: the mirror could match by luck if the read never happens."""
    mod = _load_wt_hook()
    assert mod._INTERPRETERS == mod._DEFAULT_INTERPRETERS
    assert mod._DELEGATES == mod._DEFAULT_DELEGATES
    # ...and the file is what produced them: an unreadable path degrades loudly.
    interpreters, delegates = mod._load_runners()
    assert interpreters == mod._INTERPRETERS
    assert delegates == mod._DELEGATES


def test_shell_hook_mirror_matches_the_shared_file():
    src = HYGIENE_HOOK.read_text()
    match = re.search(r"^runner_alt_fallback='([^']*)'$", src, re.MULTILINE)
    assert match, "runner_alt_fallback literal not found in the guard"
    # The shell literal escapes a dot as \\. inside single quotes; ERE sees one \.
    embedded = match.group(1).replace("\\\\.", "\\.")
    assert embedded == _expected_alternation()


def test_a_standalone_checker_is_not_an_interpreter_to_the_worktree_guard():
    """ruff reads files by path and imports nothing of the project.

    A ruff from another checkout lints exactly the files you name, so nudging it
    would be a false positive — and the guard's whole contract is that it never
    asks the agent to adjudicate one.
    """
    mod = _load_wt_hook()
    for name in _runners()["standalone_checkers"]:
        assert name not in mod._INTERPRETERS, f"{name} must not decide a checkout"


def test_bare_python_never_reaches_the_masking_nudge():
    """`python_interpreters` is the one group the hygiene guard must NOT read.

    A bare `python foo.py` returns a status nobody called a check; nudging it
    would fire on every script the agent runs.
    """
    for name in _runners()["python_interpreters"]:
        assert f"|{name}|" not in f"|{_expected_alternation()}|"
