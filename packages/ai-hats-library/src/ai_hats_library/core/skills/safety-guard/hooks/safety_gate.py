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
# re-spelled: one module owns the nonce, the directory and the clock.
try:
    from consent_ticket import TICKET_ENV, TTL_SECONDS as TICKET_TTL
    from consent_ticket import mint as _mint_ticket
except ImportError:  # sibling absent -> no question to ask; the refusal still stands

    def _mint_ticket(task_id: str, **_kw) -> None:
        return None

    #: The literal is duplicated ONLY on this path, so a missing sibling cannot
    #: turn the deny-list entry below into a hole.
    TICKET_ENV = "AI_HATS_CONSENT_TICKET"
    TICKET_TTL = 0

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
WRAPPERS = ("sudo", "env", "nohup", "xargs", "time")


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
    for token in tokens:
        if "=" in token or token in WRAPPERS:
            continue
        return os.path.basename(token)
    return ""


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


# Consent flags the target PROCESS reads, so an inline prefix reaches them. Hook-read
# flags (AI_HATS_SHARED_STATE_ACK) need no entry — HATS-1639. The consent ticket is
# here too (HATS-1642): this guard mints it, so an agent typing one is forging it.
SELF_GRANT_FORBIDDEN = ("AI_HATS_YOLO", "AI_HATS_PLAN_ACK", "AI_HATS_MERGE_ACK", TICKET_ENV)


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


# ----- the supervisor's consent for `plan → execute` (HATS-1642) --------------

#: `rack transition` op flags that eat the NEXT token as their value. Without
#: this, `--log "execute"` — a note ABOUT the move — would read as the move.
RACK_VALUE_FLAGS = frozenset(
    {
        "--state", "--attach", "--freeze", "--rm", "--log", "--link", "--unlink",
        "--set", "--append", "--reason", "--resolution", "--final-state", "--tasks-dir",
    }
)  # fmt: skip

#: `--force` skips the rack's consent gate outright, so a prompt would be theatre.
RACK_UNGATED_FLAGS = frozenset({"--force"})

EXECUTE_STATE = "execute"


def bin_args(tokens, cmd_bin: str):
    """``tokens`` from the binary onward — env assignments and wrappers dropped."""
    for i, tok in enumerate(tokens):
        if os.path.basename(tok) == cmd_bin:
            return tokens[i:]
    return tokens


def execute_transition_target(args) -> str:
    """The task id iff ``args`` is ``rack transition <ID> execute``, else ``""``.

    One pass, because options come before the positional id as readily as after
    it (`rack transition --tasks-dir /t X execute`) — and the refusal tells the
    agent to re-run its command, so a shape that goes quiet makes it a liar.
    """
    rest = args[1:]
    if not rest or rest[0] != "transition":
        return ""
    task_id, wanted, i = "", False, 1
    while i < len(rest):
        tok = rest[i]
        if tok in RACK_UNGATED_FLAGS:
            return ""
        if tok in RACK_VALUE_FLAGS:
            if tok == "--state" and rest[i + 1 : i + 2] == [EXECUTE_STATE]:
                wanted = True
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        if not task_id:
            task_id = tok
        elif tok == EXECUTE_STATE:
            wanted = True
        i += 1
    return task_id if wanted else ""


def prefixed_command(cmd: str, rack_token: str, ordinal: int, total: int, assignment: str) -> str:
    """``cmd`` with ``assignment`` inserted before the ``ordinal``-th rack call.

    Prefixing the whole string would be wrong — in `cd x && rack …` the
    assignment would belong to `cd`. Counting occurrences against the commands
    the lexer actually found is what tells `rack ls && rack transition …` (two
    calls, two spellings) from a `rack` inside a quoted argument: on a mismatch
    this returns ``""``, and a rewrite the gate is unsure of is one it skips.
    """
    spots = list(re.finditer(r"(?<![\w./~-])" + re.escape(rack_token) + r"(?=\s)", cmd))
    if len(spots) != total or not (0 <= ordinal < total):
        return ""
    at = spots[ordinal].start()
    return f"{cmd[:at]}{assignment} {cmd[at:]}"


def target_cwd(cmd: str):
    """Where the rack call in ``cmd`` will actually run — ``None`` if unknowable.

    The store is resolved from the TARGET, not from the hook's own directory
    (the HATS-1647 policy its neighbour ``backlog_write_gate`` already follows):
    with `cd /other/repo && rack …` a hook-relative store means the supervisor
    clicks in one repo and `rack` looks in another.
    """
    where = Path.cwd()
    for tokens in parse_commands(cmd):
        if get_bin(tokens) != "cd":
            continue
        args = [tok for tok in bin_args(tokens, "cd")[1:] if not tok.startswith("-")]
        if len(args) != 1:
            return None  # `cd`, `cd -`, `cd a b`: not a destination we can name
        where = (where / os.path.expanduser(args[0])).resolve()
    return where


def consent_ask(cmd: str, tool_input: dict, cmd_key: str) -> dict:
    """The `ask` payload for a `plan → execute` transition, or ``{}``.

    One hook, one verdict (HATS-1253): the process that mints the ticket is the
    one that refuses a typed one, so the two can never disagree about which is
    which. Empty means there is nothing to ask — the rack's own gate still runs.
    """
    calls = [bin_args(t, "rack") for t in parse_commands(cmd) if get_bin(t) == "rack"]
    for index, args in enumerate(calls):
        task_id = execute_transition_target(args)
        if not task_id:
            continue
        token = args[0]
        ordinal = sum(1 for a in calls[:index] if a and a[0] == token)
        total = sum(1 for a in calls if a and a[0] == token)
        # The env channel stands in wherever no question can be asked — headless,
        # cron, a surface without runtime hooks. Set there, a prompt is redundant,
        # and in headless an `ask` does not prompt, it blocks.
        if os.environ.get("AI_HATS_PLAN_ACK") == "1":
            journal_bypass("hatch", "AI_HATS_PLAN_ACK", hook="safety_gate.py", cmd=cmd)
            return {}
        where = target_cwd(cmd)
        nonce = None if where is None else _mint_ticket(task_id, start=where, argv=args[1:])
        rewritten = (
            prefixed_command(cmd, token, ordinal, total, f"{TICKET_ENV}={nonce}") if nonce else ""
        )
        if not rewritten:
            # A gate that stopped asking looks exactly like one with nothing to
            # ask about (HATS-1373). The rack still refuses; say why it must.
            journal_bypass(
                "fail-open",
                f"no consent question raised for {task_id}: "
                + ("cannot place the ticket in the command" if nonce else "cannot mint a ticket"),
                hook="safety_gate.py",
                cmd=cmd,
            )
            return {}
        return {
            "permissionDecision": "ask",
            "permissionDecisionReason": (
                f"{task_id}: plan → execute needs your consent. This command carries a "
                f"ticket good for one transition of this card, in this session, for "
                f"{TICKET_TTL}s."
            ),
            # Answer in the key the surface spoke in: agy says `CommandLine`, and
            # a rewrite filed under `command` would be dropped in silence.
            "updatedInput": {**tool_input, cmd_key: rewritten},
        }
    return {}


#: Binaries that mutate whatever path they are handed. `sed` counts only with
#: `-i`; without it sed reads.
BACKLOG_MUTATORS = ("mkdir", "rmdir", "mv", "cp", "rm", "touch", "tee", "ln", "sed")
#: …except these two, judged on their DESTINATION alone: naming the card as the
#: source writes nothing, and reading a card out is what this gate leaves open.
#: `mv` is not among them — it empties the source as well.
DESTINATION_ONLY = ("cp", "ln")
#: Every shape bash writes a file with. `shlex(punctuation_chars=True)` hands the
#: operator over as ONE token, so `>|` (the noclobber escape) and `&>` are simply
#: not reachable by looking for `>`.
REDIRECTS = (">", ">>", ">|", "&>", "&>>", ">&")


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

        cmd_bin = get_bin(tokens)
        if not cmd_bin:
            continue

        args = bin_args(tokens, cmd_bin)

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

    tool_input = payload.get("tool_input")
    if not tool_input:
        tool_call = payload.get("toolCall") or {}
        tool_input = tool_call.get("args") or {}

    # Remember WHICH key carried the command: a rewrite has to answer in the same
    # one, and agy spells it `CommandLine` (HATS-1642 review).
    cmd_key = "command" if tool_input.get("command") else "CommandLine"
    cmd = (tool_input.get(cmd_key) or "").strip()
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
        ask = consent_ask(cmd, tool_input, cmd_key)
    except Exception as exc:
        # A consent path that touches the filesystem must never take the rest of
        # the gate down with it — `rm`, `mkfs`, `dd` are judged above (HATS-1647).
        journal_bypass("fail-open", f"consent ask failed: {exc!r}", hook="safety_gate.py", cmd=cmd)
        return 0
    if ask:
        _emit(ask)
    return 0


def _emit(decision: dict) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", **decision}}))


if __name__ == "__main__":
    sys.exit(main())
