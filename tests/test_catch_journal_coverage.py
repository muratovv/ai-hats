"""HATS-1634 — a gate that fires without recording it is a sensor nobody reads.

The ratchet for catch, mirroring `test_bypass_journal_coverage.py`: every gate
that can refuse, escalate or nudge must record the firing. KNOWN_UNCAUGHT may
shrink, never grow.

It proves *the file records a catch*, not *every branch does* — no static check
can pair a branch with a call. What it catches is the failure that made this
task: a gate wired to nothing.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
LIB = REPO_ROOT / "packages/ai-hats-library/src/ai_hats_library"

#: Every layer that ships a gate, and every directory one can live in. The
#: `ai-hats-dev` rows are not symmetry: `quality-gate` moved there, and a glob
#: set blind to a layer reports the gates in it as clean.
HOOK_GLOBS = tuple(
    f"{layer}/skills/*/{where}/*.{ext}"
    for layer in ("core", "usage", "ai-hats-dev")
    for where in ("hooks", "git_hooks", "lib")
    for ext in ("sh", "py")
)

#: Scripts under a hooks dir that decide nothing — journal plumbing, a reader, a
#: sourced classifier, or provisioning. Nothing fires, so nothing is caught.
NOT_A_GATE = {
    "bypass_journal.py": "the writer itself",
    "bypass_journal.sh": "the writer itself",
    "pre-push-bypass-report.sh": "the journal's reader; never blocks",
    "post-commit-bypass-stamp.sh": "stamps sha onto existing rows",
    "shared_state_classifier.sh": "sourced classifier — echoes a verdict, owns no outcome",
    "provision-venv.sh": "warn-continue provisioning, not a gate",
}

#: gate -> why it does not record a catch yet. Remove an entry when you wire it;
#: `test_the_documented_gaps_are_still_gaps` refuses one you forgot to remove.
#: This seed IS the S3/S4 worklist and must reach `{}` before the task closes.
KNOWN_UNCAUGHT: dict[str, str] = {}

#: How a gate signals it did NOT wave the call through.
VERDICT_RE = re.compile(
    r"""permissionDecision   # runtime deny / ask
      | additionalContext    # runtime advisory nudge
      | \bexit\s+[12]\b      # git tier block / checks-channel refuse
      | sys\.exit\(\s*[12]\s*\)
      | BLOCKED
    """,
    re.VERBOSE,
)

CATCH_RE = re.compile(r"(?:ai_hats_)?journal_catch\b")

#: Lines that NAME the writer without calling it. Counting these was the first
#: version's bug: deleting wt_gate.py's only call left the import behind and the
#: ratchet stayed green while the gate recorded nothing.
_NOT_A_CALL = (
    "from bypass_journal import",
    "def journal_catch",
    "ai_hats_journal_catch()",
    # A usage line in the writer's own header. Following a `source` made eight
    # git-tier gates read as wired on the strength of that one comment.
    "#",
)


def gate_files() -> list[Path]:
    return sorted(p for glob in HOOK_GLOBS for p in LIB.glob(glob) if p.name not in NOT_A_GATE)


def fires_a_verdict(text: str) -> bool:
    return bool(VERDICT_RE.search(text))


def records_a_catch(text: str) -> bool:
    """True only when the file CALLS the writer — naming it does not count."""
    return any(
        CATCH_RE.search(stripped)
        for line in text.splitlines()
        if not (stripped := line.strip()).startswith(_NOT_A_CALL)
    )


#: A plain `. <literal path>`: a sourced body IS the sourcer's body, so a shim's
#: call may live there. Literal-only — a `$VAR` path guessed wrong would forgive
#: a gate that records nothing.
_SOURCE_LINE_RE = re.compile(r"^\s*(?:if\s+!\s+)?(?:\.|source)\s")
_REL_PATH_RE = re.compile(r"((?:\.\./|\./)[\w./-]*\.sh)")


def sourced_files(path: Path) -> list[Path]:
    """Files ``path`` sources by a resolvable literal path.

    Reads the relative path out of the line rather than parsing the shell
    expression around it — the real spelling carries ``${BASH_SOURCE[0]}``,
    ``$(cd …)`` and ``&&``, and a parser for those would fail closed on the
    next spelling somebody writes.
    """
    out: list[Path] = []
    for line in path.read_text(errors="replace").splitlines():
        if not _SOURCE_LINE_RE.match(line):
            continue
        found = _REL_PATH_RE.search(line)
        if not found:
            continue  # a `$VAR` path names no file — never guess one
        target = (path.parent / found.group(1)).resolve()
        if target.is_file():
            out.append(target)
    return out


def effective_text(path: Path) -> str:
    """What the gate actually runs: its own body plus the bodies it sources."""
    bodies = [path.read_text(errors="replace")]
    bodies += [p.read_text(errors="replace") for p in sourced_files(path)]
    return "\n".join(bodies)


def uncaught() -> dict[str, str]:
    """Gates that can fire but never record it."""
    out: dict[str, str] = {}
    for path in gate_files():
        if fires_a_verdict(path.read_text(errors="replace")) and not records_a_catch(
            effective_text(path)
        ):
            out[path.name] = str(path.relative_to(LIB))
    return out


def test_discovery_finds_the_gates():
    """Green must mean 'checked and clean', never 'matched nothing'.

    The floor sits just under today's inventory so that losing a whole LAYER —
    the failure that let four `ai-hats-dev` gates read as clean — is red, not a
    quieter list. A gate legitimately deleted still passes.
    """
    files = gate_files()
    assert len(files) >= 22, [str(p.relative_to(LIB)) for p in files]


def test_a_gate_that_denies_without_recording_is_reported():
    assert fires_a_verdict('printf \'{"permissionDecision":"deny"}\'')
    assert not records_a_catch('printf \'{"permissionDecision":"deny"}\'')


def test_a_gate_that_records_is_not_reported():
    text = 'ai_hats_journal_catch dev_rule_foo nudge "$cmd"\nexit 1'
    assert fires_a_verdict(text)
    assert records_a_catch(text)


def test_importing_the_writer_without_calling_it_does_not_count():
    """The first ratchet counted the import, so deleting the only call stayed green."""
    text = (
        "from bypass_journal import journal_bypass, journal_catch\n"
        "\n"
        "    def journal_catch(rule: str, verdict: str, **_kw) -> bool:\n"
        "        return False\n"
        'print(json.dumps({"permissionDecision": "deny"}))\n'
    )
    assert fires_a_verdict(text)
    assert not records_a_catch(text)


def test_a_shim_is_credited_with_what_its_lib_records(tmp_path):
    """`done-gate.sh` is nine lines over a shared lib — the call lives in the lib.

    Without following the source, the three quality-gate shims read as gates that
    refuse and record nothing, and the honest fix would be three KNOWN_UNCAUGHT
    entries for gates that are in fact wired.
    """
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "gate.sh").write_text(
        'gate_exit() {\n  ai_hats_journal_catch "$1" refuse\n  exit 2\n}\n'
    )
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shim = hooks / "done-gate.sh"
    shim.write_text(
        '. "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/../lib/gate.sh" || exit 2\n'
        'gate_main done-gate "$STAGES" "$@"\n'
    )
    assert sourced_files(shim) == [(lib / "gate.sh").resolve()]
    assert fires_a_verdict(shim.read_text())
    assert records_a_catch(effective_text(shim))


def test_a_shim_goes_red_when_its_lib_stops_recording(tmp_path):
    """The half that proves the credit above is earned, not granted by shape."""
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / "gate.sh").write_text("gate_exit() {\n  exit 2\n}\n")
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    shim = hooks / "done-gate.sh"
    shim.write_text('. "$(dirname "$0")/../lib/gate.sh" || exit 2\ngate_main done-gate\n')
    assert not records_a_catch(effective_text(shim))


def test_a_comment_naming_the_writer_is_not_a_call():
    """The writer's own usage header, reached through a `source`, credited eight
    git-tier gates that record nothing."""
    assert not records_a_catch("# ai_hats_journal_catch <rule> <verdict> [cmd]\nexit 1")
    assert records_a_catch('ai_hats_journal_catch rule deny "$cmd"  # still a call')


def test_a_source_through_a_variable_is_not_followed(tmp_path):
    """`source "$CLASSIFIER"` names no file; forgiving it would hide a real gap."""
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    gate = hooks / "guard.sh"
    gate.write_text('source "$CLASSIFIER"\nexit 1\n')
    assert sourced_files(gate) == []


def test_the_shell_fallback_stub_does_not_count_as_a_call():
    text = 'ai_hats_journal_catch() {\n  echo "nope" >&2\n}\nexit 1'
    assert not records_a_catch(text)


def test_every_gate_that_can_fire_records_the_catch():
    offenders = {k: v for k, v in uncaught().items() if k not in KNOWN_UNCAUGHT}
    assert not offenders, (
        "these gates can refuse or nudge without recording it — add the "
        f"journal_catch call, or document the gap in KNOWN_UNCAUGHT: {offenders}"
    )


def test_the_documented_gaps_are_still_gaps():
    """A wired entry must leave the list, so the ratchet cannot go stale."""
    stale = sorted(set(KNOWN_UNCAUGHT) - set(uncaught()))
    assert not stale, f"now recording catches — drop from KNOWN_UNCAUGHT: {stale}"
