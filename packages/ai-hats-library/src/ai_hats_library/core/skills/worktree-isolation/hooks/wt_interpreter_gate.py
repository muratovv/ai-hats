#!/usr/bin/env python3
"""HATS-1856 — warn, at call time, that a check run will measure the wrong checkout.

The existing guards compare the import against the tests, so `wt exec` hides the
skew from both by putting the worktree on PYTHONPATH while every subprocess still
resolves elsewhere. This one compares the INTERPRETER against the worktree, and
does it before the run. Warns only where the mismatch is provable, printing both
resolved paths so the verdict needs no adjudication. Never gates: exit 0 +
additionalContext, never a permissionDecision. Kill switch:
AI_HATS_WT_INTERP_OFF=1. Stdlib-only (system python3 via shebang, inline git).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# The hooks are stdlib-only, so the journal arrives as a flattened sibling.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from shell_walk import walk
except ImportError:  # the walk is this guard's eyes — it must not guess without them
    walk = None  # type: ignore[assignment]

try:
    from bypass_journal import journal_bypass, journal_catch
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False

    def journal_catch(rule: str, verdict: str, **_kw) -> bool:
        print(
            f"[catch-journal] NOT RECORDED ({rule}: {verdict}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_KILL_SWITCH = "AI_HATS_WT_INTERP_OFF"
_RUNNERS_FILENAME = "test_runners.json"

#: Embedded mirror of the groups this guard reads from ``test_runners.json`` —
#: the last resort when the file shipped beside this script is unreadable. Kept
#: in sync by tests/test_shared_test_runners.py.
_DEFAULT_INTERPRETERS = ("python", "python3", "pytest")
_DEFAULT_DELEGATES = ("make", "ci-local.sh", "gates.sh")

#: What a delegating runner really resolves to: `make test` names no interpreter,
#: so the recipe's would-be `pytest` is the one whose checkout decides.
_DELEGATE_PROXY = "pytest"

#: `make` is only a check run when the TARGET says so. `make lint` resolves no
#: project import, and nudging it would make the guard cry wolf on every build —
#: the one thing that would teach the agent to stop reading it. A delegate that
#: is itself a script (`gates.sh`) names its purpose already, so it needs no
#: target.
_TEST_TARGET_RE = re.compile(r"test|check|\bci\b|e2e|integration|coverage")


def _load_runners() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """``(interpreters, delegates)`` from the shared list beside this script.

    Only the first word of each entry matters here: this guard classifies the
    head of a command, so ``python -m pytest`` and ``python`` are one name to it.
    Falling back to the embedded mirror is a degraded verdict, so it is recorded
    (HATS-1829)."""
    path = Path(__file__).resolve().parent / _RUNNERS_FILENAME
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        journal_bypass(
            "degraded",
            f"no readable {_RUNNERS_FILENAME} ({exc!r}) — deciding on the embedded mirror",
            hook="wt_interpreter_gate.py",
        )
        return _DEFAULT_INTERPRETERS, _DEFAULT_DELEGATES

    def heads(*groups: str) -> tuple[str, ...]:
        out: list[str] = []
        for group in groups:
            for entry in data.get(group) or ():
                if isinstance(entry, str) and entry.split():
                    head = entry.split()[0]
                    if head not in out:
                        out.append(head)
        return tuple(out)

    interpreters = heads("python_interpreters", "python_runners")
    delegates = heads("delegating")
    if not interpreters or not delegates:
        journal_bypass(
            "degraded",
            f"{_RUNNERS_FILENAME} names no runners — deciding on the embedded mirror",
            hook="wt_interpreter_gate.py",
        )
        return _DEFAULT_INTERPRETERS, _DEFAULT_DELEGATES
    return interpreters, delegates


_INTERPRETERS, _DELEGATES = _load_runners()

#: Cheap pre-filter: everything expensive (git, PATH resolution) happens only
#: after a runner name appears at all. A leading `/` counts as a boundary — the
#: whole point is catching `.venv/bin/pytest`, and demanding whitespace before
#: the name skipped every path-spelled runner there is.
_RUNNER_RE = re.compile(
    r"(^|[\s|;&/])("
    + "|".join(re.escape(n) for n in (*_INTERPRETERS, *_DELEGATES))
    + r")([\s|;&]|$)"
)


def _git_worktree_root(directory: str) -> Path | None:
    """The root of the LINKED worktree at ``directory``, else None.

    None covers every case with nothing to warn about: not a git tree, or the
    main checkout — where the checkout's own interpreter is the right one.
    Mirrors ``wt_gate._git_info``'s --git-dir != --git-common-dir test, inline
    because the hook runs under the system interpreter without ai_hats."""
    try:
        result = subprocess.run(
            [  # noqa: S607
                "git",
                "rev-parse",
                "--path-format=absolute",
                "--git-dir",
                "--git-common-dir",
                "--show-toplevel",
            ],
            cwd=directory,
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
    except (subprocess.SubprocessError, OSError):
        return None
    lines = [ln for ln in result.stdout.splitlines() if ln.strip()]
    if len(lines) != 3:
        return None
    git_dir, common_dir, toplevel = lines
    if Path(git_dir).resolve() == Path(common_dir).resolve():
        return None  # main checkout
    return Path(toplevel).resolve()


def _venv_prefix(spelled: Path) -> Path | None:
    """The venv ``spelled`` belongs to, read the way CPython decides ``sys.prefix``.

    A venv's ``bin/python`` is a symlink to the base interpreter, so what the
    binary resolves to answers a question nobody asked. ``pyvenv.cfg`` beside the
    invoked path is what actually decides where imports come from."""
    bindir = spelled.parent
    for candidate in (bindir, bindir.parent):
        try:
            if (candidate / "pyvenv.cfg").is_file():
                return candidate
        except OSError:
            return None
    return None


def _spelled(spelling: str, cwd: Path, search_path: str | None) -> Path | None:
    """The file ``spelling`` will execute, symlinks left alone, or None when it
    cannot be pinned down.

    Lexical on purpose: ``normpath`` collapses ``..`` without asking the
    filesystem, so a venv's ``bin/python`` still names the venv it lives in."""
    if "/" in spelling:
        try:
            return Path(os.path.normpath(cwd / spelling))
        except (OSError, ValueError):
            return None
    found = shutil.which(spelling, path=search_path if search_path else os.environ.get("PATH"))
    return Path(found) if found else None


def _deciding_executable(segment: list[str]) -> str | None:
    """Which executable's checkout decides this command's result, if any.

    A non-Python runner (``go test``, ``npm test``) does not read the worktree's
    venv at all, so it is not this guard's business and returns None."""
    head = segment[0]
    name = Path(head).name
    # `bash scripts/gates.sh` runs the script as surely as naming it does.
    # Not for `-c`: what a body runs is a second command line, and reading one
    # word of it would be a guess rather than a resolution.
    if name in ("bash", "sh", "zsh") and len(segment) > 1 and not segment[1].startswith("-"):
        return _deciding_executable(segment[1:])
    if name in _INTERPRETERS:
        # A bare interpreter only counts when it is running something we know is
        # a check: `python script.py` reads no venv-installed console script.
        if name.startswith("python") and not any(
            Path(t).name in _INTERPRETERS for t in segment[1:]
        ):
            return None
        return head
    if name in _DELEGATES:
        if not name.endswith(".sh") and not any(_TEST_TARGET_RE.search(t) for t in segment[1:]):
            return None
        return _DELEGATE_PROXY
    return None


def _finding(command: str, cwd: Path) -> tuple[str, Path, Path] | None:
    """The executable that proves a mismatch: how it was spelled, where it
    resolves, and the worktree it was measured against.

    The worktree is decided per segment rather than once up front, because a
    leading ``cd`` moves the agent into one within the same call — the shape a
    payload-only reading of ``cwd`` would classify as the main checkout."""
    steps = walk(command, cwd)
    if steps is None:
        return None  # unparsable, or a cd we lost -> prove nothing

    roots: dict[Path, Path | None] = {}
    for segment, effective_cwd, path_override in steps:
        spelling = _deciding_executable(segment)
        if spelling is None:
            continue
        if effective_cwd not in roots:
            roots[effective_cwd] = (
                _git_worktree_root(str(effective_cwd)) if effective_cwd.is_dir() else None
            )
        worktree = roots[effective_cwd]
        if worktree is None:
            continue  # main checkout or non-git -> its own interpreter is right
        spelled = _spelled(spelling, effective_cwd, path_override)
        if spelled is None:
            continue  # unresolvable -> nothing proven
        # The venv decides, and only where there is none does the real file.
        # Resolving first follows a venv's bin/python out to the base interpreter
        # and reports the worktree's own interpreter as foreign.
        venv = _venv_prefix(spelled)
        if venv is None:
            try:
                spelled = spelled.resolve()
            except (OSError, ValueError):
                continue
        home = venv or spelled
        if not home.is_relative_to(worktree):
            return (spelling, spelled, worktree)
    return None


def _message(spelling: str, resolved: Path, worktree: Path) -> str:
    """What the agent reads. Both paths are resolved facts, so nothing here needs
    adjudicating — the one inference (that a delegating recipe calls the proxy) is
    named as an inference."""
    venv_python = worktree / ".venv" / "bin" / "python"
    fix = (
        "  ./.venv/bin/python -m pytest ...   (from inside the worktree)"
        if venv_python.is_file()
        else "  uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e '.[dev]'   (no venv here yet)"
    )
    return (
        "GUARDRAIL (worktree-isolation): this run will not measure the worktree you "
        "stand in.\n"
        f"  you stand in           : {worktree}\n"
        f"  but `{spelling}` resolves : {resolved}\n"
        "That interpreter is outside the worktree, so its editable install, its "
        "console scripts, and every subprocess it spawns read the checkout it was "
        "installed from — not the sources under you. PYTHONPATH does not fix this: "
        "it swaps the import path, not the interpreter, which is why a green or red "
        "result here is someone else's.\n"
        "Re-run it against the worktree:\n"
        f'  PATH="{worktree}/.venv/bin:$PATH" <your command>\n'
        f"{fix}\n"
        f"Kill switch (supervisor only): {_KILL_SWITCH}=1"
    )


def main() -> int:
    if os.environ.get(_KILL_SWITCH) == "1":
        journal_bypass("hatch", _KILL_SWITCH, hook="wt_interpreter_gate.py")
        return 0

    if walk is None:
        journal_bypass("fail-open", "shell_walk.py missing", hook="wt_interpreter_gate.py")
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        # Fail-open, but recorded (HATS-1373).
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook="wt_interpreter_gate.py")
        return 0

    # One dialect: the surface's bridge translates before spawning this
    # (`ai_hats.surfaces.agy.claude_hook_adapter`, HATS-1776).
    tool_input = payload.get("tool_input")
    command = (tool_input or {}).get("command") if isinstance(tool_input, dict) else None
    if not isinstance(command, str) or not _RUNNER_RE.search(command):
        return 0  # not a check run -> nothing expensive happens

    # `cwd` follows the agent: it is "the new directory after Claude runs `cd`",
    # which is precisely the directory whose sources are at stake. The hook is
    # spawned there too, so getcwd() is the same answer when the key is absent.
    cwd_raw = (payload.get("cwd") or "").strip() or os.getcwd()
    try:
        cwd = Path(cwd_raw).resolve()
    except (OSError, ValueError):
        return 0

    finding = _finding(command, cwd)
    if finding is None:
        return 0

    journal_catch("worktree-isolation", "nudge", hook="wt_interpreter_gate.py", cmd=command)
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "additionalContext": _message(*finding),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
