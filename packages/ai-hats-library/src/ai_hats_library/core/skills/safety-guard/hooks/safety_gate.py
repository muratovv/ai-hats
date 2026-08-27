#!/usr/bin/env python3
"""Enforces `global_rule_destructive_actions` by blocking destructive commands.

The rule protects PATHS, so this gate matches paths, not binary names: a name
match cannot tell `rm -rf /tmp/scratch` from `rm -rf /`, and denying both
contradicts `global_rule_resource_hygiene` (HATS-1253). The same path matching
carries `rule_backlog_discipline` §1 (HATS-1647): a raw mutation under the
tracker backlog is refused with the `rack` recipe. Not handled here: `git push`
(pre_bash_shared_state_guard.sh), worktrees (wt_gate.py).
"""

import json
import os
import re
import shlex
import sys
from pathlib import Path

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# The hooks are stdlib-only, so the journal arrives as a flattened sibling.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from bypass_journal import journal_bypass
except ImportError:  # helper absent -> say so; never skip quietly

    def journal_bypass(kind: str, reason: str, **_kw) -> bool:
        print(
            f"[bypass-journal] NOT RECORDED ({kind}: {reason}) — bypass_journal.py missing",
            file=sys.stderr,
        )
        return False


_backlog_off_journaled = False

# HATS-1642 — the ticket the ask hands to the rack process. Imported, never
# re-spelled: one module owns the nonce and the directory.
try:
    from consent_ticket import TICKET_ENV
    from consent_ticket import mint as _mint_ticket
    from consent_ticket import tickets_dir as _tickets_dir
except ImportError:  # sibling absent -> no question to ask; the refusal still stands

    def _mint_ticket(task_id: str, **_kw) -> None:
        return None

    def _tickets_dir(_start=None) -> None:
        return None

    #: The literal is duplicated ONLY on this path, so a missing sibling cannot
    #: turn the deny-list entry below into a hole.
    TICKET_ENV = "AI_HATS_CONSENT_TICKET"

# HATS-1735 — the grant: one answer covering a window of moves. Absent sibling
# means no grant can be read, so every question is asked exactly as before.
try:
    import consent_gate as _grant_engine
    from consent_gate import Outcome as _GrantOutcome
except ImportError:  # engine not beside us -> no grant road, only the old one
    _grant_engine = None
    _GrantOutcome = None

# HATS-1754 — the spellings a guarded binary arrives under, shared with the
# permission lint so the two cannot drift. A missing sibling costs the guard its
# sight of every runner spelling, so it is recorded rather than assumed.
_spellings_off_journaled = False
try:
    from consent_spellings import MODULE_BINARIES as _MODULE_BINARIES
    from consent_spellings import RUNNERS as _RUNNERS
    from consent_spellings import is_interpreter as _is_interpreter
    from consent_spellings import module_binary as _module_binary
except ImportError:  # sibling absent -> only the bare spelling is seen; recorded on first use
    _RUNNERS = ()
    _MODULE_BINARIES = {}

    def _is_interpreter(_token: str) -> bool:
        return False

    def _module_binary(_tokens):
        global _spellings_off_journaled
        if not _spellings_off_journaled:
            _spellings_off_journaled = True
            journal_bypass("fail-open", "consent_spellings.py missing", hook="safety_gate.py")
        return []


# HATS-1816 — the ONE reading of a guarded verb, shared with the session wrapper.
# Absent sibling costs the guard its sight of every verb, so it is recorded.
try:
    from consent_gate import operations as _operations
except ImportError:
    _operations = None

#: Names already reported absent, so the fail-open is said once and not per call.
_OPERATIONS_OFF: set = set()

#: `None` is a MEANINGFUL value for ``module`` — it is what "the registry is not
#: here" looks like — so the default cannot be spelled with it.
_DEFAULT = object()


def _read_operation(
    operation: str, surface: str, args, *, module=_DEFAULT, said=None, journal=None
):
    """The registry's reading of ``args`` (binary first), or ``None``.

    A gate that quietly stopped reading looks exactly like a gate with nothing
    to guard, so the missing registry is journaled rather than assumed away.
    The three collaborators are parameters with real defaults: a test that had
    to patch this module would be patching the code under test.
    """
    module = _operations if module is _DEFAULT else module
    said = _OPERATIONS_OFF if said is None else said
    journal = journal_bypass if journal is None else journal
    if module is None:
        if "operations" not in said:
            said.add("operations")
            journal("fail-open", "consent_gate.operations missing", hook="safety_gate.py")
        return None
    return module.read(operation, surface, list(args)[1:])


# HATS-1647 — the tracker predicate shares its resolver and its wording with the
# Edit/Write half of the gate: two texts for one rule is how the coarser one wins.
try:
    from backlog_write_gate import verdict_for as _backlog_verdict
except ImportError:  # sibling absent -> predicate off; recorded on first use

    def _backlog_verdict(_path: str) -> str:
        global _backlog_off_journaled
        if not _backlog_off_journaled:
            _backlog_off_journaled = True
            journal_bypass("fail-open", "backlog_write_gate.py missing", hook="safety_gate.py")
        return ""


#: Set to "1" when the supervisor approved a specific destructive command.
#: Mirrors AI_HATS_PLAN_ACK / AI_HATS_MERGE_ACK.
DESTRUCTIVE_ACK = "AI_HATS_DESTRUCTIVE_ACK"

# A fast path for "not restorable from the repo + toolchain", not its definition
# (HATS-1430: `runs` added after gitignored experiment transcripts matched none).
PROTECTED_SUFFIXES = (".db", ".sqlite", ".sqlite3", ".sql", ".dump")
PROTECTED_NAMES = ("terraform.tfstate",)
PROTECTED_DIRS = ("volumes", "data", "storage", "runs")

SQL_CLIENTS = ("psql", "mysql", "mariadb", "sqlite3", "sqlcmd", "mongo", "clickhouse-client")

OPERATORS = (";", "&&", "||", "|", "&")

#: Binaries that run ANOTHER binary. The list is names only — how many operands
#: each one eats before the real command is deliberately NOT modelled; see
#: :func:`command_slices` for why.
WRAPPERS = (
    "sudo", "doas", "env", "nohup", "xargs", "time", "timeout",
    "nice", "ionice", "stdbuf", "setsid", "chrt", "taskset", "command",
    *_RUNNERS,
)  # fmt: skip

#: Every shape bash writes a file with. `shlex(punctuation_chars=True)` hands the
#: operator over as ONE token, so `>|` (the noclobber escape) and `&>` are simply
#: not reachable by looking for `>`.
REDIRECTS = (">", ">>", ">|", "&>", "&>>", ">&")


def parse_commands(cmd_string: str):
    """Split into logical commands as token lists, honouring quotes.

    A regex split on the operators turns a quoted argument into a phantom
    command — a ripgrep regex containing ``|git push`` synthesised a push that
    was never issued (HATS-1253 R5).
    """
    try:
        lexer = shlex.shlex(cmd_string, posix=True, punctuation_chars=True)
        lexer.whitespace_split = True
        tokens = list(lexer)
    except ValueError:
        return []  # unbalanced quotes — nothing reliable to inspect

    commands, current = [], []
    for tok in tokens:
        if tok in OPERATORS:
            if current:
                commands.append(current)
                current = []
        else:
            current.append(tok)
    if current:
        commands.append(current)
    return commands


def get_bin(tokens):
    """The binary ``tokens`` runs, env assignments skipped."""
    for token in tokens:
        if "=" in token:
            continue
        return os.path.basename(token)
    return ""


def _is_operand(token: str) -> bool:
    """A token that cannot be a command: an option, or a bare duration/number."""
    return token.startswith("-") or bool(re.fullmatch(r"\d+(\.\d+)?[smhd]?", token))


def command_slices(tokens):
    """Every token slice that could be the command ``tokens`` actually runs.

    A wrapper eats a variable number of operands — `timeout 5`, `sudo -u root`,
    `nice -n 10`, `stdbuf -oL` — and modelling each one's option arity is a
    losing game: the entry the table gets wrong makes the gate BLIND, not merely
    imprecise. That is exactly how `timeout 5 rm -rf /` was allowed while
    `rm -rf /` was denied (HATS-1682, measured). So when a wrapper leads, every
    later slice is offered to the checks and a dangerous binary cannot hide
    behind an operand nobody counted.

    A slice is offered twice when it spells `<interpreter> -m <module>`: once as
    typed, once as the binary that module runs (HATS-1754). The `-c` payload is
    ONE token and never becomes a slice — an interpreter's argument is not a
    command, and reading it as one is how a quoted string synthesised a call
    that was never issued (HATS-1253 R5).
    """

    def _command_at(index: int) -> int:
        while index < len(tokens) and "=" in tokens[index]:
            index += 1  # `env FOO=1 rack …`: the assignment belongs to the shell
        return index

    start = _command_at(0)
    if start >= len(tokens):
        return []
    slices = [tokens[start:]]
    if os.path.basename(tokens[start]) in WRAPPERS:
        seen = {start}
        for i in range(start + 1, len(tokens)):
            j = _command_at(i)
            if j >= len(tokens) or j in seen or _is_operand(tokens[j]):
                continue
            seen.add(j)
            slices.append(tokens[j:])
    return slices + [call for call in map(_module_binary, slices) if call]


def slice_for(tokens, name: str):
    """The slice of ``tokens`` whose command is ``name`` — ``[]`` when none is.

    Reading past a wrapper matters on the permissive paths too: `env FOO=1 rack …`
    and `timeout 180 rack …` are everyday spellings, and a gate that cannot see
    the `rack` in them asks no question at all (HATS-1682).
    """
    for candidate in command_slices(tokens):
        if get_bin(candidate) == name:
            return candidate
    return []


def without_shell_redirects(args):
    """``args`` minus what the SHELL consumes, so it matches the process's argv.

    A redirection never reaches the child's `sys.argv`, but the lexer hands it
    over as tokens — `2>&1` arrives as `2`, `>&`, `1`. Binding a consent ticket
    to those three made `rack` compute a different argv and refuse a click the
    supervisor had already given (HATS-1682, live probe).
    """
    kept, i = [], 0
    while i < len(args):
        token = args[i]
        if token in REDIRECTS:
            if kept and kept[-1].isdigit():
                kept.pop()  # the fd the redirection applies to
            i += 2  # the operator and its target
            continue
        kept.append(token)
        i += 1
    return kept


#: One line per process: the ack is documented as per-single-command, so its
#: presence means THIS command was waved through (HATS-1407).
_journaled = False


def _acked() -> bool:
    global _journaled
    if os.environ.get(DESTRUCTIVE_ACK) != "1":
        return False
    if not _journaled:
        _journaled = True
        journal_bypass("hatch", DESTRUCTIVE_ACK, hook="safety_gate.py")
    return True


def _ack_hint(reason: str) -> str:
    return (
        f"{reason} If the supervisor approved this, re-run the single command as: "
        f"{DESTRUCTIVE_ACK}=1 <command>"
    )


def _paths(args):
    return [t for t in args[1:] if not t.startswith("-")]


def is_catastrophic(path: str) -> bool:
    """Filesystem root or the user's home — no consent flag opens these."""
    expanded = os.path.expandvars(os.path.expanduser(path))
    if path.rstrip("/*") == "" or expanded.rstrip("/*") == "":
        return True
    return expanded.rstrip("/") == os.path.expanduser("~").rstrip("/")


def is_protected(path: str) -> bool:
    """A path `global_rule_destructive_actions` names as protected data."""
    stripped = path.rstrip("/")
    base = os.path.basename(stripped)
    if base in PROTECTED_NAMES or base == ".env" or base.startswith(".env."):
        return True
    if any(base.endswith(suffix) for suffix in PROTECTED_SUFFIXES):
        return True
    return any(seg in PROTECTED_DIRS for seg in stripped.split("/") if seg)


def check_rm(args) -> str:
    paths = _paths(args)
    for path in paths:
        if is_catastrophic(path):
            return (
                f"Stopped: `rm` targets {path!r} — refusing to delete the filesystem root "
                f"or your home directory. No consent flag overrides this."
            )
    if _acked():
        return ""
    for path in paths:
        if is_protected(path):
            return _ack_hint(
                f"Stopped: `rm` targets protected data ({path}) per "
                f"global_rule_destructive_actions."
            )
    return ""


def check_dangerous_bin(cmd_bin: str, args) -> str:
    # Real invocations are mkfs.ext4 / mkfs.xfs — bare "mkfs" alone never matched.
    if cmd_bin == "mkfs" or cmd_bin.startswith("mkfs."):
        return f"Stopped: {cmd_bin} formats a filesystem — no agent use case."
    if cmd_bin == "dd" and not _acked():
        for token in args:
            if token.startswith("of=/dev/"):
                return _ack_hint(f"Stopped: `dd` writing to a device ({token}) destroys a disk.")
    return ""


def check_sed_inplace(args) -> bool:
    """True when this `sed` rewrites its file instead of reading it."""
    return any(tok == "-i" or (tok.startswith("-i") and not tok.startswith("--")) for tok in args)


def check_sed(args) -> str:
    if _acked():
        return ""
    if check_sed_inplace(args):
        return _ack_hint("Stopped: `sed -i` edits files in place.")
    return ""


def check_sql(cmd_bin: str, args) -> str:
    """Scoped to DB clients so a bug report naming the phrase is not a command."""
    if cmd_bin not in SQL_CLIENTS or _acked():
        return ""
    joined = " ".join(args).lower()
    for sub in ("drop table", "drop database"):
        if sub in joined:
            return _ack_hint(f"Stopped: destructive SQL detected ({sub}).")
    return ""


#: Point-agnostic pre-approval; where no question can be asked — headless, cron,
#: a surface without runtime hooks — this is the channel (HATS-1642 fork 2).
CONSENT_ACK = "AI_HATS_CONSENT_ACK"

# Consent flags the target PROCESS reads, so an inline prefix reaches them. Hook-read
# flags (AI_HATS_SHARED_STATE_ACK) need no entry — HATS-1639. The consent ticket is
# here too (HATS-1642): this guard mints it, so an agent typing one is forging it.
SELF_GRANT_FORBIDDEN = (
    "AI_HATS_YOLO", "AI_HATS_PLAN_ACK", "AI_HATS_MERGE_ACK", CONSENT_ACK, TICKET_ENV,
)  # fmt: skip


def check_self_grant(args) -> str:
    """The agent must not hand itself consent inline; exporting it is the user's call."""
    for token in args:
        upper = token.upper()
        for flag in SELF_GRANT_FORBIDDEN:
            if not upper.startswith(f"{flag}="):
                continue
            if flag == TICKET_ENV:
                return (
                    f"Stopped: {flag} is minted by this guard when the supervisor answers "
                    "the prompt — it is not typed. Run the transition without a prefix."
                )
            return (
                f"Stopped: {flag} cannot be granted inline — a guard the agent can "
                "switch off is not a guard. Export it in the environment instead."
            )
    return ""


# ----- the supervisor's consent, where the ROLE declared it (HATS-1682) -------

#: Kept per state only where the flag already MEANT that edge. `AI_HATS_MERGE_ACK`
#: is NOT here: it approves `ai-hats wt merge`, and reading it as consent for
#: `review → done` is exactly how the edge into master went unasked (HATS-1682).
LEGACY_ACK_BY_TARGET = {"execute": "AI_HATS_PLAN_ACK"}

#: The wt engine's own pre-approval, honoured on the point it was written for —
#: `ai-hats wt merge` — and nowhere else.
WT_MERGE_ACK = "AI_HATS_MERGE_ACK"


def _declared_points() -> list:
    """The consent declaration this session was launched with, or ``[]``.

    A role property reaching the guard the way every other one does:
    `AI_HATS_SESSION_IDENTITY` names the session dir and
    `role_materialization.json` there carries the composed declaration.
    Launch-frozen on purpose — the surface asks by what the session started
    with, not by whatever the library says a moment later. A MISSING `consent`
    key is an absent declaration, not a corrupt file: read as unreadable, a
    session predating the key lost consent for its whole life (HATS-1682 B5).
    """
    envelope = os.environ.get("AI_HATS_SESSION_IDENTITY", "")
    if not envelope:
        return []  # outside a session there is no role, so nothing declared
    try:
        session_dir = json.loads(envelope)["session_dir"]
        report = json.loads(
            (Path(session_dir) / "role_materialization.json").read_text(encoding="utf-8")
        )
        return list(report.get("consent", []))
    except (ValueError, OSError, KeyError, TypeError) as exc:
        # In a session but unable to read what it declared: a gate that stopped
        # asking looks exactly like one with nothing to ask about (HATS-1373).
        journal_bypass(
            "fail-open", f"consent declaration unreadable: {exc!r}", hook="safety_gate.py"
        )
        return []


def _grant_policy() -> tuple:
    """Operation types the role declared under ``apps.consent_gate`` (HATS-1735)."""
    declared = []
    for entry in _declared_points():
        if not isinstance(entry, dict):
            continue
        path = entry.get("path")
        if entry.get("app") != "consent_gate" or not isinstance(path, list) or not path:
            continue
        operation = str(path[0])
        if operation and operation not in declared:
            declared.append(operation)
    return tuple(declared)


def grant_covers(op_type: str, subject: str, cmd: str) -> bool:
    """Has a live grant already answered this, so no question is needed?

    Conservative on purpose, and deliberately NOT identical to the engine's own
    check. This half only ever SUPPRESSES a question, so every doubt — no
    envelope, no engine, a target it cannot place inside the session's project —
    resolves to "ask anyway". The engine stays the authority and stays
    fail-closed, so the two disagreeing costs at most one extra question and can
    never cost an unasked move (HATS-1735).
    """  # comment-length: allow — why the two halves may disagree IS the contract
    identity = _envelope()
    if not identity:
        return False
    project_dir = identity.get("project_dir") or ""
    where = target_cwd(cmd)
    if not project_dir or where is None:
        return False
    try:
        anchor = Path(project_dir).resolve()
        target = Path(where).resolve()
    except OSError as exc:
        journal_bypass(
            "fail-open", f"consent grant target unresolved: {exc!r}", hook="safety_gate.py"
        )
        return False
    if target != anchor and anchor not in target.parents:
        return False  # outside this session's project: never suppress the question
    verdict = _grant_check(op_type, subject, identity, anchor)
    if verdict is None or verdict.outcome is not _GrantOutcome.GRANTED:
        return False
    # No journal line here: this is a PEEK. The engine records the USE moments
    # later on every road that reaches it, and a line from both halves made one
    # operation look like two (ADR-0029 D11 / P6, HATS-1736).
    return True


def _envelope() -> dict:
    """The identity envelope as a dict, or ``{}`` when there is no session."""
    raw = os.environ.get("AI_HATS_SESSION_IDENTITY", "")
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except ValueError as exc:
        journal_bypass("fail-open", f"identity envelope unreadable: {exc!r}", hook="safety_gate.py")
        return {}
    return data if isinstance(data, dict) else {}


def _grant_check(op_type: str, subject: str, identity: dict, anchor):
    """Ask the engine, or ``None`` when it is not on the path beside us."""
    if _grant_engine is None:
        return None
    return _grant_engine.check(
        _grant_engine.Operation(op_type, subject=subject),
        session_id=identity.get("id") or "",
        store_root=_grant_engine.store_root_from(identity.get("session_cache_dir")),
        project_dir=anchor,
        policy=_grant_policy(),
    )


def _operation_rows(operation: str) -> tuple[dict, ...]:
    """Declared rows carried by one external operation adapter."""
    return tuple(
        entry
        for entry in _declared_points()
        if isinstance(entry, dict)
        and entry.get("app") == "consent_gate"
        and entry.get("path")
        and entry["path"][0] == operation
    )


def _operation_points(operation: str) -> tuple[str, ...]:
    """Protected points carried by one external operation adapter."""
    return tuple(
        str(entry["selector"]) for entry in _operation_rows(operation) if entry.get("selector")
    )


def declared_consent_targets() -> frozenset:
    """States the session's role declared consent on entering, from the envelope.

    Read from the ``to`` field the composition already parsed — never by cutting
    the selector here. The declaration is a role property, so it reaches the
    guard the way every other role-derived fact does: `AI_HATS_SESSION_IDENTITY` names the session
    dir and `role_materialization.json` there carries the composed declaration.
    Launch-frozen on purpose — the surface asks by the declaration the session
    was started with, not by whatever the library says a moment later.

    The field was empty on every row between HATS-1755 and HATS-1790 — the parser
    keyed on the app the row stood under, and consent had moved out of it — so
    this cut the name instead and the sentence above stopped being true. Cutting
    is what HATS-1719 removed: the copy of a grammar goes quiet, not red, when
    the grammar moves.
    """
    targets = set()
    for entry in _operation_rows("rack.transition"):
        target = entry.get("to")
        if target:
            targets.add(str(target))
    return frozenset(targets)


#: `rack transition` op flags that eat the NEXT token as their value. Without
#: this, `--log "execute"` — a note ABOUT the move — would read as the move.
RACK_VALUE_FLAGS = frozenset(
    {
        "--state", "--attach", "--freeze", "--rm", "--log", "--link", "--unlink",
        "--set", "--append", "--reason", "--resolution", "--final-state", "--tasks-dir",
    }
)  # fmt: skip

#: Read-only verbs: they move nothing, so a prompt on them is pure friction.
RACK_READ_VERBS = frozenset({"context", "ls"})

#: Ops that annotate a card and touch no FSM edge. Enumerated POSITIVELY — the
#: complement ("everything that is not plan → execute") grows a hole with every
#: flag the rack gains, and `done` alone carries a merge into master.
RACK_SAFE_OPS = frozenset({"--log", "--set", "--append", "--attach", "--link", "--rm"})

#: Options that carry no op at all: they shape output or routing.
RACK_SAFE_OPTS = frozenset({"--json", "--tasks-dir"})


#: The `ai-hats wt` operations a role can declare: (type, point, headline).
#: A table because there are two of them — `wt.discard` joined in ADR-0031 D4 —
#: and two copies of one loop is how the second one goes quiet.
WT_OPERATIONS = (
    ("wt.merge", "pre-merge", "merging {subject} into master needs your consent."),
    ("wt.discard", "pre-discard", "discarding {subject} destroys its work."),
)


def wt_subject(operation: str, args) -> str:
    """What ``ai-hats wt <verb>`` will act on, or ``""`` for another call.

    The branch may be omitted — the CLI detects it from the cwd — so this is a
    LABEL for the question, never the binding. What binds is the invocation.
    """
    reading = _read_operation(operation, "ai-hats", args)
    return "" if reading is None else reading.subject


def merge_branch(args) -> str:
    """What ``ai-hats wt merge`` will merge, or ``""`` for another call."""
    return wt_subject("wt.merge", args)


def transition_target(args):
    """``(task id, target state)`` for a `rack transition`, else ``("", "")``.

    Which target NEEDS consent is not decided here — that is the role's
    declaration (HATS-1682). This only reads the move out of the command line.

    NO flag turns the reading off. There used to be a `RACK_UNGATED_FLAGS` set
    holding `--force`, and the whole set is gone rather than that one entry:
    consent is not a property of the command, so nothing ADDED to the command
    can remove it. `--force` now reads as any other unrecognised flag.
    """  # comment-length: allow — why the exemption set is gone, not shortened
    reading = _read_operation("rack.transition", "rack", args)
    return ("", "") if reading is None else (reading.subject, reading.target)


def auto_allowed(args) -> bool:
    """True when this rack call is routine enough to spare the supervisor a click.

    Routine means: it annotates, it does not move. A bare token after the task id
    IS a state op (`transition <ID> done`), and `done` tears the worktree down
    into master — a shared-state write that `rule_pause_before_shared_state_write`
    wants paused, never waved through. Anything unrecognised returns False, which
    is silence rather than allow: the ordinary permission flow still decides.
    """
    verb = args[1] if len(args) > 1 else ""
    if verb in RACK_READ_VERBS:
        return True
    if verb != "transition":
        return False
    rest, i, task_id, op = args[2:], 0, "", False
    while i < len(rest):
        tok = rest[i]
        if tok in RACK_SAFE_OPS:
            if i + 1 >= len(rest):
                return False
            i, op = i + 2, True
            continue
        if tok in RACK_SAFE_OPTS:
            i += 2 if tok == "--tasks-dir" else 1
            continue
        if tok.startswith("-") or task_id:
            return False  # an unenumerated flag, or a state op after the id
        task_id, i = tok, i + 1
    return bool(task_id and op)


def allow_verdict(cmd: str) -> dict:
    """`allow` iff EVERY command in the line is a routine rack call (or a `cd`).

    A chain is only as allowable as its least allowable link: waving through
    `rack transition X --log y && git push` would auto-approve the push.
    """
    commands = parse_commands(cmd)
    if not commands:
        return {}
    for tokens in commands:
        if slice_for(tokens, "cd"):
            continue
        rack = slice_for(tokens, "rack")
        if not rack or not auto_allowed(without_shell_redirects(rack)):
            return {}
    return {
        "permissionDecision": "allow",
        "permissionDecisionReason": (
            "routine rack bookkeeping: this call annotates a card and moves no "
            "state. The gated moves are answered by this guard, not by a rule."
        ),
    }


def prefixed_command(cmd: str, anchor: str, ordinal: int, total: int, assignment: str) -> str:
    """``cmd`` with ``assignment`` inserted before the ``ordinal``-th ``anchor``.

    The anchor is the first token of the LOGICAL COMMAND carrying the gated
    call, never the gated binary itself: `timeout 180 rack …` rewritten in front
    of `rack` hands the wrapper a `VAR=VAL` operand, and of the thirteen names
    in :data:`WRAPPERS` only `env` tolerates one — the rest exit 127
    (HATS-1682 A2). Prefixing the whole STRING would be wrong the other way, in
    `cd x && rack …` binding the assignment to `cd`, so the insertion goes at
    the head of its own segment. Counting occurrences against the segments the
    lexer actually found is what tells `rack ls && rack transition …` from a
    `rack` inside a quoted argument: on a mismatch this returns ``""``, and a
    rewrite the gate is unsure of is one it refuses rather than guesses at.
    """  # comment-length: allow — the placement rule and both ways to get it wrong
    spots = list(re.finditer(r"(?<![\w./~-])" + re.escape(anchor) + r"(?=\s)", cmd))
    if len(spots) != total or not (0 <= ordinal < total):
        return ""
    at = spots[ordinal].start()
    return f"{cmd[:at]}{assignment} {cmd[at:]}"


def anchored_calls(cmd: str, name: str) -> list:
    """Every logical command in ``cmd`` running ``name``, ready to be rewritten.

    Yields ``(anchor, ordinal, total, argv)``. The ordinal counts SEGMENTS whose
    first token matches, not calls to ``name``, because that is what
    :func:`prefixed_command` goes looking for in the raw string.
    """
    segments = parse_commands(cmd)
    heads = [tokens[0] for tokens in segments]
    found = []
    for index, tokens in enumerate(segments):
        call = slice_for(tokens, name)
        if not call:
            continue
        head = heads[index]
        found.append(
            (
                head,
                sum(1 for h in heads[:index] if h == head),
                sum(1 for h in heads if h == head),
                without_shell_redirects(call),
            )
        )
    return found


def target_cwd(cmd: str):
    """Where the rack call in ``cmd`` will actually run — ``None`` if unknowable.

    The store is resolved from the TARGET, not from the hook's own directory
    (the HATS-1647 policy its neighbour ``backlog_write_gate`` already follows):
    with `cd /other/repo && rack …` a hook-relative store means the supervisor
    clicks in one repo and `rack` looks in another.
    """
    where = Path.cwd()
    for tokens in parse_commands(cmd):
        cd = slice_for(tokens, "cd")
        if not cd:
            continue
        args = [tok for tok in cd[1:] if not tok.startswith("-")]
        if len(args) != 1:
            return None  # `cd`, `cd -`, `cd a b`: not a destination we can name
        where = (where / os.path.expanduser(args[0])).resolve()
    return where


def _python_module_calls(cmd: str, binary: str) -> list:
    """Every ``<interpreter> -m <module>`` call in ``cmd`` that launches ``binary``.

    The modules come from the SHARED table and the interpreter from its predicate
    — neither from a literal here. Measured (HATS-1781): matching the module name
    against one hard-coded string saw `-m ai_hats_rack` and missed
    `-m ai_hats_rack.cli`, which the table knows and the allow-rule lint probes,
    so the lint called that spelling guarded while this boundary waved it through.
    """
    modules = {name for name, launched in _MODULE_BINARIES.items() if launched == binary}
    if not modules:
        _no_spellings_table()
        return []
    calls = []
    for tokens in parse_commands(cmd):
        for args in command_slices(tokens):
            if not _is_interpreter(get_bin(args)):
                continue
            for index, argument in enumerate(args[:-1]):
                if argument == "-m" and args[index + 1] in modules:
                    calls.append(args[index + 2 :])
                    break
    return calls


def _no_spellings_table() -> None:
    """An empty table means the sibling is gone; blindness is recorded, not assumed."""
    global _spellings_off_journaled
    if not _spellings_off_journaled:
        _spellings_off_journaled = True
        journal_bypass("fail-open", "consent_spellings.py missing", hook="safety_gate.py")


def _wrapper_bypass(operation: str) -> dict:
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": (
            f"Stopped: {operation} is protected by this role. Use the canonical "
            "session wrapper command; alternate executable lookup bypasses it."
        ),
    }


def _changes_command_lookup(cmd: str, name: str) -> bool:
    for tokens in parse_commands(cmd):
        call = slice_for(tokens, name)
        if not call:
            continue
        prefix = tokens[: len(tokens) - len(call)]
        if any(
            partitioned[0] == "PATH" for token in prefix if (partitioned := token.partition("="))[1]
        ):
            return True
        wrapper_index = next(
            (index for index, token in enumerate(prefix) if "=" not in token), None
        )
        if wrapper_index is None:
            continue
        wrapper = os.path.basename(prefix[wrapper_index])
        wrapper_args = prefix[wrapper_index + 1 :]
        if wrapper in {"command", "sudo", "doas", *_RUNNERS}:
            return True
        if wrapper != "env":
            continue
        for index, argument in enumerate(wrapper_args):
            if argument in {"-", "-i", "--ignore-environment"}:
                return True
            if argument in {"-u", "--unset"} and wrapper_args[index + 1 : index + 2] == ["PATH"]:
                return True
            if argument in {"-uPATH", "--unset=PATH"}:
                return True
    return False


def _wrapper_bypass_verdict(cmd: str, *, targets=None, points=None) -> dict:
    """Refuse a protected operation arriving under a spelling that skips the wrapper.

    The two declaration readers are TAKEN, not fetched: resolving them three frames
    in is what forced a caller to patch this module to test it, and a boundary that
    can only be tested by rewriting it is one nobody rewrites carefully (HATS-1781).
    Still resolved lazily — the declaration is only read once a candidate is found.
    """
    targets = targets if targets is not None else declared_consent_targets
    points = points if points is not None else _operation_points
    declared_targets = None
    rack_lookup_bypass = _changes_command_lookup(cmd, "rack")
    for anchor, _ordinal, _total, args in anchored_calls(cmd, "rack"):
        _task_id, target = transition_target(args)
        if not target:
            continue
        if declared_targets is None:
            declared_targets = targets()
        bypasses_path = (
            args[0] != "rack" or os.path.basename(anchor) == "command" or rack_lookup_bypass
        )
        if target in declared_targets and bypasses_path:
            return _wrapper_bypass("rack transition")
    for call in _python_module_calls(cmd, "rack"):
        _task_id, target = transition_target(["rack", *call])
        if not target:
            continue
        if declared_targets is None:
            declared_targets = targets()
        if target in declared_targets:
            return _wrapper_bypass("rack transition")

    declared: dict = {}
    wt_lookup_bypass = _changes_command_lookup(cmd, "ai-hats")
    for operation, point, _headline in WT_OPERATIONS:
        verb = f"ai-hats wt {operation.split('.')[1]}"
        for anchor, _ordinal, _total, call in anchored_calls(cmd, "ai-hats"):
            if not wt_subject(operation, call):
                continue
            if operation not in declared:
                declared[operation] = point in points(operation)
            bypasses_path = (
                call[0] != "ai-hats" or os.path.basename(anchor) == "command" or wt_lookup_bypass
            )
            if declared[operation] and bypasses_path:
                return _wrapper_bypass(verb)
        for call in _python_module_calls(cmd, "ai-hats"):
            if not wt_subject(operation, ["ai-hats", *call]):
                continue
            if operation not in declared:
                declared[operation] = point in points(operation)
            if declared[operation]:
                return _wrapper_bypass(verb)
    return {}


def consent_ask(cmd: str, tool_input: dict) -> dict:
    """The verdict for a call touching a point the ROLE declared consent on.

    Where to ask is not a judgement this hook makes — it reads the role's
    `composition.apps` rows and asks exactly on the points they name (HATS-1682).
    One hook, one verdict (HATS-1253): the process that mints the ticket is the
    one that refuses a typed one, so the two can never disagree about which is
    which.

    ``{}`` means NO declared point was matched — nothing to ask about, and the
    ordinary permission flow decides. Once a point IS matched the answer is
    `ask` or `deny`, never ``{}``: on a declared point silence is that flow's
    allow, which is how three everyday spellings merged into master unasked
    (HATS-1682 A4, measured).
    """  # comment-length: allow — the {} / ask / deny contract is the whole fix
    bypass = _wrapper_bypass_verdict(cmd)
    if bypass:
        return bypass

    declared = None
    for anchor, ordinal, total, args in anchored_calls(cmd, "rack"):
        task_id, target = transition_target(args)
        if not task_id:
            continue
        if declared is None:
            declared = declared_consent_targets()  # read once, and only if asked
        if target not in declared:
            continue
        # A live grant already answered this, for a window the supervisor named
        # — asking again is the click the grant was issued to remove (HATS-1735).
        if grant_covers("rack.transition", task_id, cmd):
            return {}
        # The env channel stands in wherever no question can be asked — headless,
        # cron, a surface without runtime hooks. Set there, a prompt is redundant,
        # and in headless an `ask` does not prompt, it blocks.
        for flag in (CONSENT_ACK, LEGACY_ACK_BY_TARGET.get(target, "")):
            if flag and os.environ.get(flag) == "1":
                journal_bypass("hatch", flag, hook="safety_gate.py", cmd=cmd)
                return {}
        return _ask_for(
            cmd,
            tool_input,
            args,
            anchor,
            ordinal,
            total,
            task_id,
            f"{task_id}: → {target} needs your consent.",
        )

    # `ai-hats wt merge` — the OTHER road into master (HATS-1130). Same question,
    # different engine, which is why one declaration has to cover both.
    for operation, point, headline in WT_OPERATIONS:
        for anchor, ordinal, total, call in anchored_calls(cmd, "ai-hats"):
            subject = wt_subject(operation, call)
            if not subject or point not in _operation_points(operation):
                continue
            if grant_covers(operation, subject, cmd):
                return {}
            legacy = (WT_MERGE_ACK,) if operation == "wt.merge" else ()
            for flag in (CONSENT_ACK, *legacy):
                if os.environ.get(flag) == "1":
                    journal_bypass("hatch", flag, hook="safety_gate.py", cmd=cmd)
                    return {}
            return _ask_for(
                cmd,
                tool_input,
                call,
                anchor,
                ordinal,
                total,
                subject,
                headline.format(subject=subject),
            )
    return {}


#: Said once, so the two questions cannot drift into describing different tickets.
_TICKET_TERMS = (
    "This command carries a ticket good for one use, in this session, for this exact "
    "call. The question does not expire: answer when you have read what you are "
    "approving."
)


def _ask_for(cmd, tool_input, args, anchor, ordinal, total, label, headline) -> dict:
    """Mint the ticket and put it on the call — or refuse, naming the obstacle.

    Never ``{}``: the caller only gets here on a point the ROLE declared, and
    there silence is an allow (HATS-1682 A4).
    """
    where = target_cwd(cmd)
    nonce = None if where is None else _mint_ticket(label, start=where, argv=args[1:])
    rewritten = (
        prefixed_command(cmd, anchor, ordinal, total, f"{TICKET_ENV}={nonce}") if nonce else ""
    )
    if rewritten:
        return {
            "permissionDecision": "ask",
            "permissionDecisionReason": f"{headline} {_TICKET_TERMS}",
            # One key, because one dialect reaches this script: the surface's
            # own bridge renames it on the way in and on the way back out
            # (`ai_hats.surfaces.agy.claude_hook_adapter`, HATS-1776).
            # a rewrite filed under `command` would be dropped in silence.
            "updatedInput": {**tool_input, "command": rewritten},
        }
    obstacle, note = _no_question(where, anchor, minted=bool(nonce))
    # The question vanished, and that still has to leave a trace (HATS-1373/1407)
    # — but the command is refused now, not waved through.
    journal_bypass(
        "no-question",
        f"consent question not raised for {label}, command denied: {note}",
        hook="safety_gate.py",
        cmd=cmd,
    )
    return {
        "permissionDecision": "deny",
        "permissionDecisionReason": f"Stopped: {headline} {obstacle}",
    }


def _no_question(where, anchor: str, *, minted: bool) -> tuple:
    """Why no question could be raised: what to tell the agent, and the journal."""
    if where is None:
        return (
            "This guard cannot tell where the command would run — its `cd` names "
            "no single destination — so it has nowhere to mint the one-use ticket "
            "that carries the answer. Re-run with one explicit `cd <dir> &&`, or "
            "from the project checkout with no `cd` at all.",
            "no single target directory",
        )
    if not minted:
        return (
            f"This guard could not open a consent-ticket store for {where} — the "
            "store lives in the project's git directory. Run the command from "
            "inside the project checkout, without a `cd` out of it.",
            "cannot mint a ticket",
        )
    return (
        f"This guard cannot place the ticket on the command: the word {anchor!r} "
        "occurs in the line more often than there are commands starting with it "
        "— typically inside a quoted option value — and a rewrite it is unsure "
        f"of is one it will not guess at. Re-run with {anchor!r} out of the "
        "option values, then add the note in a second call.",
        "cannot place the ticket in the command",
    )


#: Binaries that mutate whatever path they are handed. `sed` counts only with
#: `-i`; without it sed reads.
BACKLOG_MUTATORS = ("mkdir", "rmdir", "mv", "cp", "rm", "touch", "tee", "ln", "sed")
#: …except these two, judged on their DESTINATION alone: naming the card as the
#: source writes nothing, and reading a card out is what this gate leaves open.
#: `mv` is not among them — it empties the source as well.
DESTINATION_ONLY = ("cp", "ln")


def check_backlog_write(cmd_bin: str, args) -> str:
    """`rule_backlog_discipline` §1 — the tracker backlog is `rack`-only.

    Runs before the generic handlers so a tracker path gets the `rack` recipe
    rather than `sed -i`'s per-call ack: there is no per-call form here."""
    targets = [args[i + 1] for i, tok in enumerate(args) if tok in REDIRECTS and i + 1 < len(args)]
    if cmd_bin in BACKLOG_MUTATORS and (cmd_bin != "sed" or check_sed_inplace(args)):
        paths = _paths(args)
        targets.extend(paths[-1:] if cmd_bin in DESTINATION_ONLY else paths)
    for target in targets:
        reason = _backlog_verdict(target)
        if reason:
            return reason
    return ""


HANDLERS = {"sed": check_sed, "rm": check_rm}

#: Shells whose `-c` argument is a whole command line. To the outer lexer that
#: argument is ONE token, so every check above read past it — which is how
#: `bash -c "rm -rf /"` and an ack hidden behind `export` both went through.
SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "ash")
#: `-c`, and the bundles it really arrives in: `-lc`, `-lic`, `-euc`.
SHELL_C_FLAG = re.compile(r"\A-[a-z]*c\Z")
#: A wrapper nesting deeper than this is not a spelling anyone types.
MAX_WRAPPER_DEPTH = 3


def shell_payloads(cmd_bin: str, args) -> list:
    """The command lines a `sh -c '…'` style call hands to a shell."""
    if cmd_bin not in SHELLS:
        return []
    return [
        args[i + 1] for i, tok in enumerate(args) if SHELL_C_FLAG.match(tok) and i + 1 < len(args)
    ]


def check_command(cmd_string: str, depth: int = 0) -> str:
    for tokens in parse_commands(cmd_string):
        reason = check_self_grant(tokens)
        if reason:
            return reason

        # Every slice, not just the leading one: a wrapper hides the real binary
        # behind operands this gate deliberately does not count (HATS-1682).
        for args in command_slices(tokens):
            cmd_bin = get_bin(args)
            if not cmd_bin:
                continue

            if depth < MAX_WRAPPER_DEPTH:
                for payload in shell_payloads(cmd_bin, args):
                    reason = check_command(payload, depth + 1)
                    if reason:
                        return reason

            reason = check_backlog_write(cmd_bin, args)
            if reason:
                return reason

            reason = check_dangerous_bin(cmd_bin, args) or check_sql(cmd_bin, args)
            if reason:
                return reason

            handler = HANDLERS.get(cmd_bin)
            if handler:
                reason = handler(args)
                if reason:
                    return reason

    return ""


def main() -> int:
    if os.environ.get("AI_HATS_YOLO") == "1":
        journal_bypass("hatch", "AI_HATS_YOLO", hook="safety_gate.py")
        return 0

    try:
        payload = json.loads(sys.stdin.read())
    except Exception as exc:
        # Fail-open, but recorded: a gate that stopped seeing its payload looks
        # exactly like a gate with nothing to block (HATS-1373).
        journal_bypass("fail-open", f"unparsable payload: {exc!r}", hook="safety_gate.py")
        return 0

    tool_input = payload.get("tool_input") or {}
    # Remember WHICH key carried the command: a rewrite has to answer in the same
    # one, and agy spells it `CommandLine` (HATS-1642 review).
    cmd = (tool_input.get("command") or "").strip()
    if not cmd:
        return 0

    try:
        reason = check_command(cmd)
    except Exception as exc:
        # Fail-open, but recorded. A guard that dies mid-sweep also drops every
        # check it had not reached yet — `rm -rf /` among them (HATS-1647).
        journal_bypass("fail-open", f"cannot judge {cmd!r}: {exc!r}", hook="safety_gate.py")
        return 0

    if reason:
        _emit({"permissionDecision": "deny", "permissionDecisionReason": reason})
        return 0

    try:
        decision = consent_ask(cmd, tool_input) or allow_verdict(cmd)
    except Exception as exc:
        # A consent path that touches the filesystem must never take the rest of
        # the gate down with it — `rm`, `mkfs`, `dd` are judged above (HATS-1647).
        journal_bypass("fail-open", f"consent ask failed: {exc!r}", hook="safety_gate.py", cmd=cmd)
        return 0

    if decision:
        _emit(decision)
    return 0


def _emit(decision: dict) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))


if __name__ == "__main__":
    sys.exit(main())
