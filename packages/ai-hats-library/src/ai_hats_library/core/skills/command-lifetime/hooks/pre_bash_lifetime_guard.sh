#!/usr/bin/env bash
# HATS-1873 — command-lifetime PreToolUse Bash guard.
#
# Nothing bounds how long a shell command launched on the agent's behalf stays
# alive. The harness budget does not close it: a foreground call over its
# timeout is MOVED TO THE BACKGROUND, not stopped, and the session-end reaper
# (bounded_proc_shutdown) only runs when the session ends. Field evidence: three
# wait-loops, 21h25m alive, 83 min of CPU between them, found by hand in `ps`.
#
# Contract: stdin = hook payload JSON; read .tool_input.command and
# .tool_input.run_in_background.
#   unbounded loop / unbounded background launch -> exit 0 + permissionDecision
#     "deny" + permissionDecisionReason
#   known long-running command with no bound     -> exit 0 + additionalContext
#   anything else                                -> exit 0, no stdout
#
# Why deny for the first two and only a nudge for the third: a loop with no
# bound and a background launch with no bound cannot terminate on their own, so
# there is nothing for advice to appeal to. A merely long command finishes; a
# deny there would fire on routine work, and a gate that cries wolf gets
# switched off — worth less than the nudge it replaced.
#
# Why the guard never supplies the bound itself: `updatedInput` is a real
# capability here (safety_gate.py answers `ask` + a rewrite in one reply), so
# auto-wrapping in `timeout 600` is available and is deliberately not done. The
# number in `timeout N` has to be one the agent chose, or exit 124 arrives from
# a budget nobody set and no reader can interpret it.
#
# Hatch: AI_HATS_LIFETIME_ACK=1 for a deliberately long-lived process. It must
# be in the environment that LAUNCHED the agent — this hook runs before the
# command it judges is a process, so a per-command prefix never reaches it.
set -uo pipefail

# shellcheck source=../../../hooks/bypass_journal.sh
if ! . "$(dirname "$0")/bypass_journal.sh" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

if [[ "${AI_HATS_LIFETIME_ACK:-}" == "1" ]]; then
    ai_hats_journal_bypass hatch AI_HATS_LIFETIME_ACK
    exit 0
fi

payload="$(cat || true)"
[[ -z "$payload" ]] && exit 0

# One parser pass for both fields, tab-separated: a second pass over the same
# payload is a second chance for the two to disagree about which call they read.
extract_fields() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '[(.tool_input.command // ""), ((.tool_input.run_in_background // false) | tostring)] | @tsv' <<<"$payload" 2>/dev/null
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
ti = d.get("tool_input") or {}
print("%s\t%s" % (ti.get("command") or "", "true" if ti.get("run_in_background") else "false"))
' <<<"$payload" 2>/dev/null
        return
    fi
    return 1
}

if ! fields="$(extract_fields)" || [[ -z "$fields" ]]; then
    # Fail OPEN — a guard that cannot read the payload must not wedge the
    # session. Silently, though, is what dev_rule_silent_fallback forbids: the
    # journal line is the only thing that separates "nothing to flag" from
    # "this gate has been dark for a week".
    ai_hats_journal_bypass fail-open "no JSON parser (jq/python3) — command-lifetime checks skipped"
    exit 0
fi

cmd="${fields%%$'\t'*}"
background="${fields##*$'\t'}"
[[ -z "$cmd" ]] && exit 0

# A loop NAME inside a quoted argument is not a loop being RUN (HATS-1819).
# Every structural test below reads cmd_bare, never the raw line, so a work-log
# entry quoting `until …; do :; done` does not read as running one. Two passes,
# and the order is the point:
#   1. UNWRAP a `-c` body — `bash -c 'until …; do :; done'` really does run it.
#   2. DELETE every remaining quoted span, which is argument text.
if command -v sed >/dev/null 2>&1; then
    cmd_bare="$(printf '%s' "$cmd" \
        | sed -E "s/-c[[:space:]]+'([^']*)'/-c \1/g; s/-c[[:space:]]+\"([^\"]*)\"/-c \1/g" 2>/dev/null \
        | sed -E "s/'[^']*'|\"[^\"]*\"//g" 2>/dev/null)"
else
    ai_hats_journal_bypass fail-open "sed absent — command-lifetime checks skipped"
    exit 0
fi

emit_deny() {
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"%s"}}\n' "$1"
    exit 0
}

emit_nudge() {
    printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$1"
    exit 0
}

# A `timeout` that is a COMMAND, not the word inside someone's prose.
bounded_by_timeout() {
    [[ "$cmd_bare" =~ (^|[[:space:]|\;\&\(])timeout[[:space:]] ]]
}

# --- A: a loop with nothing to stop it ---------------------------------------
# Only `while` / `until`: a `for … in <list>` is bounded by the list it walks,
# so it is not a lifetime question at all.
if [[ "$cmd_bare" =~ (^|[[:space:]\;\&\(])(while|until)[[:space:]] ]] && [[ "$cmd_bare" == *do* ]]; then
    bounded=""
    # Three ways an author can already have bounded it. Each is a FALSE-NEGATIVE
    # bias on purpose: a loop wrongly allowed costs one runaway a person can
    # kill, a loop wrongly refused costs every session that writes a legitimate
    # one, and the second is how a gate gets switched off.
    if bounded_by_timeout; then
        bounded=1
    elif [[ "$cmd_bare" =~ (^|[[:space:]\;\&\(])(while|until)[[:space:]][^\;]*read([[:space:]]|$) ]]; then
        # `while read -r line; do … done < file` terminates at EOF. The most
        # common legitimate loop in this codebase's own hooks — denying it would
        # make the guard unusable on the first day.
        bounded=1
    elif [[ "$cmd_bare" == *"++"* || "$cmd_bare" == *"+="* || "$cmd_bare" == *'=$(('* ]]; then
        # An increment means the author wrote a counter; take their word for it.
        bounded=1
    fi
    if [[ -z "$bounded" ]]; then
        emit_deny "unbounded loop refused (command-lifetime): this while/until loop has no timeout wrapper, no read-until-EOF, and no counter, so nothing makes it stop. A wait whose condition never becomes true then runs until a person finds it in ps — the incident behind this guard burned 83 minutes of CPU over 21 hours. Bound it: wrap the whole command in 'timeout <seconds> bash -c ...' (exit 124 tells you the bound was hit), add an iteration counter, or use the Monitor tool, which is built for waiting on a condition. Also put a sleep in the body: a loop with no sleep spins a core at full speed."
    fi
fi

# --- B: a background launch with nothing to stop it --------------------------
# Secondary to A on purpose. This flag says what the agent ASKED for, and the
# harness also backgrounds a foreground call that outlives its budget — after
# this hook has already answered. So the flag catches the deliberate case and
# check A catches the one that made the incident.
if [[ "$background" == "true" ]] && ! bounded_by_timeout; then
    emit_deny "unbounded background launch refused (command-lifetime): a background process is not bounded by the Bash tool's timeout — that budget bounds the CALL, and a foreground command which exceeds it is moved to the background rather than stopped. Nothing then reaps this until the session ends. Wrap it: 'timeout <seconds> <command>'. If it is meant to outlive the turn (a dev server you will stop yourself), ask the supervisor to set AI_HATS_LIFETIME_ACK=1 in the launching environment — a prefix on this command cannot reach the guard, which runs before the command is a process."
fi

# --- C: a long-running command with no bound -> advice, never a refusal -------
# Deliberately NOT test runners: `tool_call_hygiene_guard.sh` already speaks on
# that surface, and two nudges about one pytest run is how a channel gets
# tuned out. Installs, fetches and image pulls are the ones that hang on a
# network nobody is watching.
if ! bounded_by_timeout; then
    long_rx='(^|[[:space:]|\;\&\(])(pip[3]?[[:space:]]+install|uv[[:space:]]+pip[[:space:]]+install|npm[[:space:]]+(install|ci)|yarn[[:space:]]+install|pnpm[[:space:]]+install|brew[[:space:]]+(install|upgrade|update)|apt-get[[:space:]]+install|curl|wget|git[[:space:]]+clone|docker[[:space:]]+(build|pull))([[:space:]]|$)'
    if [[ "$cmd_bare" =~ $long_rx ]]; then
        emit_nudge "long-running command with no time bound (command-lifetime): installs and network fetches hang on a remote nobody is watching, and the Bash tool's budget backgrounds them rather than stopping them. Prefer 'timeout <seconds> <command>' — exit 124 then tells you the bound was hit instead of a stall you have to notice yourself. Advice only; nothing is blocked."
    fi
fi

exit 0
