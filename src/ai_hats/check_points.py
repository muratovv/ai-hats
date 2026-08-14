"""Row resolution and composition-time validation (HATS-1140, HATS-1545).

Loud by construction: every way a declared gate can fail to install raises
``CheckBindingError`` here, at composition, rather than reporting into
``CompositionResult.errors`` — that list is tolerated silently on the
implicit-role path (``composition_seam``), which is the fail-open this channel
exists to remove.

**Scoped to the app ai-hats itself fires** since HATS-1541 (ADR-0019 D11). "Loud
at composition" still covers everything structural — the skill composes, the
script exists under it and can exec — but what a row MEANS under a foreign app
is checked by the application that owns it, when it next runs. Since HATS-1545
the app is a key of the declaration rather than a prefix of a point name, so
ai-hats no longer needs to know any application's namespaces to route a row.
"""  # comment-length: allow — what left the catalog, and why, is the decision

from __future__ import annotations

import json
import string
import sys
from hashlib import sha1
from collections.abc import Callable, Iterable, Sequence, Set as AbstractSet
from dataclasses import replace
from pathlib import Path
from typing import Any

from ai_hats_core import ResolvedCheck, ResolvedComponent

from .libraries.models import AppBinding, CheckBindingError, resolve_namespace

#: The worktree application key. ai-hats fires it, so it reads its cargo.
WT_APP = "wt"

#: The session-start application key (HATS-1581). ai-hats fires this one too:
#: the launch is its own lifecycle, owned by no other application.
AI_HATS_APP = "ai-hats"

#: The one point of that app: fired once per session, before the launch.
STARTUP_POINT = "startup"

#: The roster, not the authority: used ONLY to name a block nobody collects
#: (R9). Each integration names its own key at its own call site.
KNOWN_APPS: frozenset[str] = frozenset({"rack", WT_APP, AI_HATS_APP})


def wt_points() -> dict[str, bool]:
    """The ``wt`` app's own points, mapped to whether ``on_error: warn`` is legal.

    ADR-0019 D4: failure policy at a data-protection point belongs here, not to
    the row's author.

    One entry, because a point is a name WITH a call site. ``create`` and
    ``teardown[merge|discard|cleanup]`` sat here fired by nobody: a binding to
    one validated at composition and then never ran — the silent no-op ADR-0019
    exists to remove. HATS-1577 removed the names so that binding is refused;
    HATS-1146 returns them together with the call site, not before it.
    """  # comment-length: allow — why the catalog shrank is the decision
    return {"pre-merge": False}


def ai_hats_points() -> dict[str, bool]:
    """The ``ai-hats`` app's own points, mapped to whether ``on_error: warn`` is legal.

    ``startup`` permits ``warn``: no data is protected at the launch, unlike the
    wt teardown points D4 fixes at ``refuse``, so whether a stale gate blocks the
    session is the declaring role's call.
    """
    return {STARTUP_POINT: True}


#: Every app ai-hats fires itself, mapped to the points it fires for that app.
#: A row under one of these keys is validated here; every other key is carried
#: to whoever owns it, unread.
_OWNED_POINTS: dict[str, Callable[[], dict[str, bool]]] = {
    WT_APP: wt_points,
    AI_HATS_APP: ai_hats_points,
}


def owns_app(app: str) -> bool:
    """Whether ai-hats itself fires ``app`` (and so validates and runs its rows)."""
    return app in _OWNED_POINTS


def resolve_checks(
    declared: Sequence[AppBinding],
    skills: Iterable[ResolvedComponent],
    *,
    removed_skills: AbstractSet[str] = frozenset(),
) -> tuple[ResolvedCheck, ...]:
    """Resolve every declared row to an absolute script, deduped by identity."""
    if not declared:
        return ()
    by_name = {resolve_namespace(skill.name): skill for skill in skills}
    removed = {resolve_namespace(name) for name in removed_skills}
    resolved: dict[tuple[str, tuple[str, ...], str, str], ResolvedCheck] = {}
    for row in declared:
        # Shape before lookup: a `run` with no slash names a SKILL of "gate.sh"
        # and would be reported as an uncomposed skill — the wrong defect.
        if "/" not in row.run or not row.script.strip():
            raise CheckBindingError(
                f"{_label(row)}: 'run:' is '<skill>/<script>' — "
                f"{row.run!r} names no script inside a skill"
            )
        skill = by_name.get(resolve_namespace(row.skill))
        if skill is None:
            _report_missing_skill(row, removed)
            continue
        script_path = _resolve_script(row, skill)
        if owns_app(row.app):
            _validate_owned_points(row)
        check = ResolvedCheck(
            app=row.app,
            path=row.path,
            run=row.run,
            at=row.at,
            cargo=row.cargo,
            on_error=row.on_error,
            script_path=script_path,
            declared_by=row.declared_by,
        )
        resolved[row.identity()] = _stricter(resolved.get(row.identity()), check)
    _warn_unclaimed_apps(declared)
    return tuple(resolved.values())


def _stricter(existing: ResolvedCheck | None, incoming: ResolvedCheck) -> ResolvedCheck:
    """Dedup by identity: the strictest ``on_error`` wins, so a later relaxation
    cannot disarm an earlier gate. The first declaration site keeps the slot —
    position and ``declared_by`` follow composition order. Declaring the same row
    twice is legal and warns (R5): the alternative would force a project to fork
    a shipped role to add a second declarer."""  # comment-length: allow — R5 is a reversal of the earlier ruling
    if existing is None:
        return incoming
    print(
        f"WARN: {_label(existing)} is declared again by {incoming.declared_by!r} — "
        f"the gate installs ONCE, with on_error: "
        f"{'refuse' if 'refuse' in (existing.on_error, incoming.on_error) else existing.on_error}",
        file=sys.stderr,
    )
    if existing.on_error == "refuse" or incoming.on_error != "refuse":
        return existing
    return replace(existing, on_error="refuse")


def _warn_unclaimed_apps(declared: Sequence[AppBinding]) -> None:
    """A block no integration collects is a gate that can never fire (R9)."""
    for app in sorted({row.app for row in declared} - KNOWN_APPS):
        declarers = sorted({row.declared_by for row in declared if row.app == app})
        print(
            f"WARN: composition.apps.{app} is declared by {', '.join(repr(d) for d in declarers)}, "
            f"but no integration in this build collects {app!r} (known: "
            f"{', '.join(sorted(KNOWN_APPS))}) — those rows will never fire",
            file=sys.stderr,
        )


#: "the caller did not supply one" — distinct from ``None``, which is a caller
#: stating there IS no session. Same spelling as ``check_resolve.FROM_ENV``.
_FROM_ENV: Any = object()


def _label(check: ResolvedCheck | AppBinding) -> str:
    return f"checks: {check.declared_by!r} binds {check.run} under apps.{check.app}"


#: This channel's own words for each outcome. Its own on purpose: in ai-hats a
#: "hook" is a channel (git_hooks, runtime_hooks, worktree) and a binding line is
#: not one, so the primitive's wording would name the wrong subsystem (HATS-1572).
#: Keyed by the enum member, never by its ``value``: a string key would re-open
#: the seam the typed outcome exists to close, and the exhaustiveness test below
#: it could not then be written.
def _sayings() -> dict:
    from .hook_exec import HookOutcomeKind as K

    return {
        K.NOT_EXECUTABLE: "the script is there but cannot be executed — chmod +x it",
        K.COMMAND_NOT_FOUND: "the script could not be executed: command not found",
        K.EXEC_FAILED: "the script could not be executed",
        K.LOG_UNUSABLE: "its log could not be opened, so the run was refused rather than unrecorded",
        K.SIGNALLED: "the check was killed",
        K.TIMED_OUT: "the check ran past its budget and was stopped",
        K.EXITED: "the check broke",
        K.NO_TIME_LEFT: "the check never started — the caller's lock had no time left",
    }


def check_failure_reason(check: ResolvedCheck, run, *, identity: Any = _FROM_ENV) -> str:
    """What an operator reads when a bound check does not pass.

    Two classes, kept apart (HATS-1572). A child that RAN and refused speaks for
    itself, verbatim. A channel that never got to run one says so, and says why —
    reading those two as one message sends the operator to fix a script when what
    needs fixing is which bytes the session resolved.

    The wording is this channel's; every FACT the primitive established — the
    path, the errno, the signal, the budget, the child's own text, whether the
    tail was cut — travels through untouched.
    """  # comment-length: allow — the two classes ARE the contract
    from .hook_exec import HookOutcomeKind, with_truncation_note

    if run.ok:
        return ""
    # stderr is the fallback exactly as in the primitive: a gate that reports the
    # ordinary shell way must not read as one that refused without saying why.
    said = (run.said or run.stderr).strip()
    if run.kind is HookOutcomeKind.REFUSED:
        verdict = said or f"{_label(check)} — it refused (exit 2) without saying why"
        return with_truncation_note(verdict, run) + _stale_mirror_note(check, identity)
    if run.kind is HookOutcomeKind.SCRIPT_MISSING:
        return (
            f"{_label(check)} — the check did not run: {check.script_path} is not there. "
            f"{_absent_bytes(identity)}"
        )
    head = f"{_label(check)} — {_named(run)}"
    if run.detail:
        head += f": {run.detail}"
    return with_truncation_note(f"{head}\n{said}" if said else head, run)


def _stale_mirror_note(check: ResolvedCheck, identity: Any) -> str:
    """Why a refusal may be about the session rather than the tree (HATS-1651).

    A session executes the skill bytes frozen at its launch (ADR-0019 D9), on
    purpose. The cost is that the gate and whatever writes what the gate checks
    drift apart as the session ages, and the refusal that follows reads as a
    broken branch. This says which of the two to fix, and appears ONLY when the
    two really differ — a notice on every refusal is a notice nobody reads.

    Silent when there is nothing to compare: outside a session nothing was
    frozen, and an unreadable file is the ``SCRIPT_MISSING`` path's business.
    """  # comment-length: allow — when it must NOT appear is half the contract
    from .check_resolve import CheckResolutionError, session_identity

    live = check.source_path
    if live is None:
        # Nothing re-based this check, so no envelope is worth reading — and
        # asking for one a refusal does not depend on is how a gate's verdict
        # gets replaced by a complaint about the environment (HATS-1594).
        return ""
    if identity is _FROM_ENV:
        # Both production callers leave it defaulted, so resolving here is not a
        # convenience — without it this note is unreachable outside its tests.
        try:
            identity = session_identity()
        except CheckResolutionError as exc:
            return f"\n\n(whether these bytes are current could not be told: {exc})"
    if identity is None:
        return ""
    try:
        if live.read_bytes() == check.script_path.read_bytes():
            return ""
    except OSError as exc:
        return f"\n\n(whether {check.script_path} is current could not be told: {exc})"
    return (
        f"\n\nNOTE: this verdict came from stale bytes. Session {identity.id!r} runs the "
        f"{identity.provider} mirror {check.script_path}, frozen at launch, and "
        f"{live} has changed since. The gate and whatever writes what it checks can "
        f"disagree that way. Restart the session, or run this outside a session."
    )


def _absent_bytes(identity: Any) -> str:
    """The class-(b) half of the sentence, with the session read here and nowhere
    earlier (the ``FROM_ENV`` idiom of :mod:`check_resolve`).

    Read on this path only: eagerly would re-arm what HATS-1594 removed, and
    reading it in the CALLER would let an envelope this outcome does not depend
    on replace a gate's refusal with a complaint about the envelope.
    """
    from .check_resolve import CheckResolutionError, absent_bytes_notice, session_identity

    if identity is _FROM_ENV:
        try:
            identity = session_identity()
        except CheckResolutionError as exc:
            # Degraded, never silent: the operator still learns the gate did not
            # run, and why we cannot say which bytes it looked for.
            return f"which bytes it resolved against cannot be told — {exc}"
    return absent_bytes_notice(identity)


def _named(run) -> str:
    """This channel's phrase for an outcome, with the exit status where it adds
    something the phrase does not already carry."""
    from .hook_exec import HookOutcomeKind

    if run.kind is HookOutcomeKind.NOT_EXECUTABLE and run.exit_code == 126:
        # Pre-flight refuses a non-+x script before it ever runs, so a 126 from a
        # CHILD means something IT invoked could not run — never chmod this one.
        return "the script ran, but something it invoked could not be executed (exit 126)"
    # A kind with no phrase yet still says what the primitive knows, never less.
    phrase = _sayings().get(run.kind) or run.reason
    if run.kind is HookOutcomeKind.SIGNALLED or run.exit_code in (None, 0):
        return phrase
    return f"{phrase} (exit {run.exit_code})"


def _resolve_script(row: AppBinding, skill: ResolvedComponent) -> Path:
    """Resolve the script and prove it can actually run (ADR-0019 D6).

    Containment first: an unresolved join lets ``../../../x.sh`` — and a bare
    absolute path — read anything on disk.
    """
    label = _label(row)
    skill_dir = skill.source_path.resolve()
    script_path = (skill_dir / row.script).resolve()
    if not script_path.is_relative_to(skill_dir):
        raise CheckBindingError(
            f"{label}: {script_path} is outside the skill's directory {skill_dir} — "
            f"a row may only run scripts the declaring skill ships"
        )
    if not script_path.is_file():
        raise CheckBindingError(f"{label}: script not found at {script_path}")
    data = script_path.read_bytes()
    if not data.strip():
        raise CheckBindingError(f"{label}: script is empty — a no-op gate is a broken gate")
    if not data.startswith(b"#!"):
        raise CheckBindingError(
            f"{label}: script has no shebang ('#!') first line — it would fail to exec"
        )
    if not script_path.stat().st_mode & 0o111:
        raise CheckBindingError(
            f"{label}: script is not executable — the session skill mirror copies modes "
            f"verbatim, so a non-executable script is dead at every bound point"
        )
    return script_path


def _report_missing_skill(row: AppBinding, removed: AbstractSet[str]) -> None:
    """A recorded removal is a warn; anything else is a typo and is loud."""
    label = _label(row)
    if resolve_namespace(row.skill) not in removed:
        raise CheckBindingError(
            f"{label}, but the composition composes no skill {row.skill!r} — "
            f"a row never pulls the skill in (ADR-0019 D2); compose it or fix the name"
        )
    print(
        f"WARN: {label}, but an overlay removed that skill — dropping the "
        f"row and continuing; the gate will NOT fire",
        file=sys.stderr,
    )


def _validate_owned_points(row: AppBinding) -> None:
    """An app ai-hats fires is ai-hats's own, so its point NAMES are validated here.

    That a row names at least one point is checked for every app, at parse time
    (``_app_row``); this is the half only the owner can do. The point set comes
    from ``_OWNED_POINTS`` rather than one app's function, so a second owned app
    (HATS-1581) cannot be validated against the first one's catalog.
    """
    label = _label(row)
    points = _OWNED_POINTS[row.app]()
    for name in row.at:
        if name not in points:
            raise CheckBindingError(
                f"{label} at {name!r}, which is not a point of the {row.app!r} app "
                f"(known: {', '.join(sorted(points))})"
            )
        if row.on_error == "warn" and not points[name]:
            raise CheckBindingError(
                f"{label} at {name!r} with on_error: warn — that point protects data, "
                f"so its failure policy is fixed at 'refuse' (ADR-0019 D4)"
            )


#: Characters a row component keeps verbatim in a log name.
_LITERAL = frozenset(string.ascii_letters + string.digits + "._-")


def _escaped(part: str) -> str:
    """One row component as a filename-safe token, REVERSIBLY.

    A skill name carries a namespace separator and a script is a relative path,
    so both must lose their slashes; replacing them would collapse ``a/b.sh``
    and ``a-b.sh`` onto one name, which is the truncation defect again. ``/``
    therefore becomes ``+`` (readable) and every other non-literal byte becomes
    ``%XX`` — including ``+`` and ``%`` themselves, so the mapping decodes and
    two different components can never produce the same token. ``~`` is
    non-literal too, which is what makes it a safe joiner.
    """  # comment-length: allow — why it escapes rather than replaces is the fix
    out = []
    for char in part:
        if char in _LITERAL:
            out.append(char)
        elif char == "/":
            out.append("+")
        else:
            out.extend(f"%{byte:02X}" for byte in char.encode())
    return "".join(out)


def check_log_token(check: ResolvedCheck) -> str:
    """One row's dedup identity as a filename-safe token (HATS-1137).

    ONE function for every point that logs. ``run_hook`` truncates the log it is
    handed, so a name built from anything coarser than the row's identity lets a
    second row wipe the first one's file while the first one's reason goes on
    pointing at it. HATS-1540 reintroduced exactly that at ``wt:pre-merge`` by
    naming the log after the script's basename; sharing this is what stops the
    next point from doing it again. The path is in the token because two
    backlogs may bind the same script (HATS-1545 R6).
    """  # comment-length: allow — the defect recurred once already
    trail = "".join(f"~{_escaped(part)}" for part in check.path)
    skill = _escaped(resolve_namespace(check.skill))
    return f"{_escaped(check.app)}{trail}~{skill}~{_escaped(check.script)}{_cargo_tag(check)}"


def _cargo_tag(check: ResolvedCheck) -> str:
    """A short digest of the rest of the row's identity — ``at`` and cargo.

    Always appended, never conditionally: the token must not be COARSER than the
    identity ``resolve_checks`` keys on, or two rows that survive dedup share a
    log name and the second truncates the first one's transcript while the first
    one's reason still points at it (HATS-1137, again in HATS-1540, again here).
    A discriminator that is present only "when needed" is that same bug waiting
    for the case its condition did not foresee.
    """  # comment-length: allow — the defect recurred twice; the token rule is why
    payload = json.dumps({"at": list(check.at), **dict(check.cargo)}, sort_keys=True, default=str)
    return f"~{sha1(payload.encode()).hexdigest()[:8]}"


__all__ = [
    "AI_HATS_APP",
    "KNOWN_APPS",
    "STARTUP_POINT",
    "WT_APP",
    "CheckBindingError",
    "ai_hats_points",
    "check_log_token",
    "owns_app",
    "resolve_checks",
    "wt_points",
]
