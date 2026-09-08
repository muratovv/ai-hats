#!/usr/bin/env bash
# HATS-632 — tool-call-hygiene PreToolUse Bash guard.
#
# Non-blocking just-in-time nudge: when Claude is about to run a raw shell
# command that a dedicated Claude tool covers (grep/find/cat/sed -i/...), inject
# an additionalContext reminder to use the tool instead. The audience is the
# AGENT, not the user.
#
# Contract (the convention HATS-660 reuses): stdin = Claude Code hook payload
# JSON; read .tool_input.command; on a covered PURE invocation ->
#   exit 0 + {"hookSpecificOutput":{"hookEventName":"PreToolUse",
#             "additionalContext":"<nudge>"}}
# otherwise exit 0 with no stdout. A permissionDecision is NEVER emitted, so the
# command is never blocked and never auto-approved (mutating sed -i/awk stay
# under the normal permission flow).
#
# Conservative by design: any pipe / && / || / ; / subshell / backtick /
# redirection / here-doc, or a git/build command, is legitimately Bash -> allow
# with no nudge. A missed nudge is fine; a spurious one is just noise, never a
# block. Kill switch: AI_HATS_TOOL_HYGIENE_OFF=1 -> immediate no-op. Provider
# asymmetry: Claude consumes this; the Gemini provider is a no-op.
set -uo pipefail

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# shellcheck source=../../../hooks/bypass_journal.sh
if ! . "$(dirname "$0")/bypass_journal.sh" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
    ai_hats_journal_catch() {
        echo "[catch-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

# --- kill switch -------------------------------------------------------------
if [[ "${AI_HATS_TOOL_HYGIENE_OFF:-}" == "1" ]]; then
    ai_hats_journal_bypass hatch AI_HATS_TOOL_HYGIENE_OFF
    exit 0
fi

# --- read payload + extract the command --------------------------------------
payload="$(cat || true)"
[[ -z "$payload" ]] && exit 0

# One pass for both fields: a second pass over the same payload is a second
# chance for the two to disagree about which call they read. The flag goes on
# line 1 and the command takes every line after it, because the command is the
# field that can be MULTI-LINE (HATS-1709, the shape borrowed from
# pre_bash_lifetime_guard.sh).
extract_fields() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '((.tool_input.run_in_background // false) | tostring), (.tool_input.command // "")' <<<"$payload" 2>/dev/null
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
print("true" if ti.get("run_in_background") else "false")
print(ti.get("command") or "")
' <<<"$payload"
        return
    fi
    echo ""  # no JSON parser -> fail-safe allow
}

fields="$(extract_fields)"
# `$(…)` strips trailing newlines, so a payload with no command leaves the flag
# alone on the only line — without this the flag itself would be judged as one.
[[ "$fields" != *$'\n'* ]] && exit 0
background="${fields%%$'\n'*}"
cmd="${fields#*$'\n'}"
[[ -z "${cmd//[[:space:]]/}" ]] && exit 0

# trim leading whitespace
cmd="${cmd#"${cmd%%[![:space:]]*}"}"

# A runner NAME inside a quoted argument is not a runner CALL (HATS-1819). Every
# structural test below reads THIS, not the raw line, so recording "pytest ... 31
# passed" in a work_log does not read as running pytest. Two passes, and the order
# is the point:
#   1. UNWRAP a `-c` body — `bash -c '<runner> | tail'` really does run the runner.
#      This was never caught (the old regex needed the name space-bounded, and a
#      quote is not a space), so it is new reach, not a repair.
#   2. DELETE every remaining quoted span, which is argument text, not commands.
# The alternation is left-to-right, so whichever quote opens first closes its span.
# No sed (or an unparsable line) leaves this empty and every test below goes quiet —
# the file's own bias, stated at the allowlist: a missed nudge is acceptable, a
# spurious one is noise.
# `sed` is LINE-oriented, and a quote pairs the way the shell pairs it: across
# newlines. Folding the command onto one line for the two passes and unfolding
# after is what makes that true here — without it an opening quote whose pair sat
# on a later line never paired, and a multi-line `--log` body was read as
# commands (HATS-1709; measured on this card's own work-log writes, which is the
# role's commonest write). The unfold is not cosmetic: the boundaries below spell
# a word gap as `[[:space:]]`, which a newline is and the sentinel is not.
if command -v sed >/dev/null 2>&1 && command -v tr >/dev/null 2>&1; then
    cmd_bare="$(printf '%s' "$cmd" \
        | tr '\n' '\001' \
        | sed -E "s/-c[[:space:]]+'([^']*)'/-c \1/g; s/-c[[:space:]]+\"([^\"]*)\"/-c \1/g" 2>/dev/null \
        | sed -E "s/'[^']*'|\"[^\"]*\"//g" 2>/dev/null \
        | tr '\001' '\n')"
else
    # Degrading to silence is right; degrading SILENTLY is not
    # (dev_rule_silent_fallback) — without this line the exit-code checks just
    # stop and no log ever says why.
    ai_hats_journal_bypass fail-open "sed or tr absent — exit-code checks skipped"
    cmd_bare=""
fi

# --- check for return code masking in test/check runners (HATS-1436) -----------
# Detect test runner commands piped or chained in ways that mask non-zero exit codes.
#
# The names come from `test_runners.json`, shared with wt_interpreter_gate.py so a
# runner added once is known to both guards (HATS-1856). `python_interpreters` is
# deliberately NOT read here: a bare `python foo.py` returns a status nobody
# claimed was a check, and nudging it would cry wolf on every script.
runners_json="$(dirname "$0")/test_runners.json"

# Embedded mirror of that file's alternation — the last resort when it is
# unreadable. Kept in sync by tests/test_shared_test_runners.py.
runner_alt_fallback='pytest|python[[:space:]]+-m[[:space:]]+pytest|python3[[:space:]]+-m[[:space:]]+pytest|python[[:space:]]+-m[[:space:]]+unittest|python3[[:space:]]+-m[[:space:]]+unittest|ruff|make|ci-local\.sh|gates\.sh|go[[:space:]]+test|cargo[[:space:]]+test|npm[[:space:]]+test|npm[[:space:]]+run[[:space:]]+test|yarn[[:space:]]+test|pnpm[[:space:]]+test'

build_runner_alt() {
    local raw="" name alt=""
    if command -v jq >/dev/null 2>&1; then
        raw="$(jq -r '[.python_runners[]?, .standalone_checkers[]?, .delegating[]?, .foreign_runners[]?] | .[]' \
            "$runners_json" 2>/dev/null)"
    fi
    if [[ -z "$raw" ]] && command -v python3 >/dev/null 2>&1; then
        raw="$(python3 -c '
import json, sys
try:
    data = json.load(open(sys.argv[1]))
except Exception:
    raise SystemExit(0)
for group in ("python_runners", "standalone_checkers", "delegating", "foreign_runners"):
    for entry in data.get(group) or ():
        if isinstance(entry, str) and entry.strip():
            print(entry)
' "$runners_json" 2>/dev/null)"
    fi
    [[ -z "$raw" ]] && return 1
    while IFS= read -r name; do
        [[ -z "$name" ]] && continue
        name="${name//./\\.}"          # a literal dot in gates.sh
        name="${name// /[[:space:]]+}"  # one word gap -> any run of whitespace
        alt="${alt:+$alt|}$name"
    done <<<"$raw"
    [[ -z "$alt" ]] && return 1
    printf '%s' "$alt"
}

if ! runner_alt="$(build_runner_alt)"; then
    ai_hats_journal_bypass degraded "unreadable test_runners.json — using the embedded mirror"
    runner_alt="$runner_alt_fallback"
fi
# A leading `/` opens the name as surely as a space does: `bash scripts/gates.sh`
# is the form this project prescribes and the form both measured incidents took,
# and a boundary of whitespace alone left every path-qualified runner invisible
# (HATS-1709). The corpus was all bare names, so nothing went red. The trailing
# boundary stays strict — `Makefile` and `make-a-thing.md` are not `make`.
runner_rx="(^|[[:space:]|;&]|/)($runner_alt)($|[[:space:]|;&])"
if [[ "$cmd_bare" =~ $runner_rx ]]; then
    # HATS-1819 — the third case, and the one no masking check can see: the status
    # is the runner's, correct, and simply not depended on. `;` sequences, it does
    # not gate, so the mutation runs on red exactly as it runs on green. Checked
    # before masking and outside the `preserved` guard below: `set -o pipefail`
    # fixes whose status you read, never whether the next command honours it.
    mutate_rx=';[[:space:]]*(git[[:space:]]+(commit|push|add|merge|tag|rebase)|rack[[:space:]]+transition)'
    if [[ "$cmd_bare" =~ $mutate_rx ]]; then
        msg="a state-mutating command follows ';' after a check/test runner — ';' sequences but does not gate, so the mutation runs whatever the runner returned. Chain it with '&&' if it must not run on red. This is the case the masking checks cannot see: the status was yours and correct, and the next action simply did not depend on it."
        ai_hats_journal_catch dev_rule_exit_code_provenance nudge "$cmd"
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
        exit 0
    fi

    # What counts as preserving the status depends on the SHELL the command will
    # run in (HATS-1798). `set -o pipefail` is correct in bash and zsh both.
    # `${PIPESTATUS[0]}` is bash-only: zsh has no such name, so `exit
    # "${PIPESTATUS[0]}"` becomes `exit ""` -> 0, silently, for every run. Its
    # zsh spelling is the lowercase `${pipestatus[1]}`, indexed from 1. Exempting
    # the uppercase name outright let the silent-zero through and nudged the one
    # spelling that works here — so it is excused only under an explicit bash.
    # These fix WHICH status a PIPELINE reports and say nothing about what a `;`
    # or `||` runs next, so the exemption belongs to the pipe test ALONE. Sharing
    # it with the chain tests let `set -o pipefail` anywhere on the line excuse a
    # trailing echo — the measured incident: the shell-level defence was present
    # and the status reported was still the echo's (HATS-1709). Matched as a
    # TOKEN for the same reason a name is: `--junit=pipefail.xml` disarmed a
    # substring test.
    pipe_preserved=""
    if [[ "$cmd_bare" =~ (^|[[:space:]])pipefail([^[:alnum:]._-]|$) || "$cmd_bare" == *"pipestatus"* ]]; then
        pipe_preserved=1
    elif [[ "$cmd_bare" == *"PIPESTATUS"* && "$cmd_bare" =~ (^|[[:space:]])bash([[:space:]]|$) ]]; then
        pipe_preserved=1
    fi

    pipe_rx='\|[[:space:]]*(tail|head|grep|rg|tee)'
    semi_rx=';[[:space:]]*(echo|true|exit[[:space:]]+0)'
    or_rx='\|\|[[:space:]]*(echo|true|exit[[:space:]]+0)'
    # `; echo $? > file` captures the status for the agent to read rather
    # than printing and losing it, so it is not masking at any path. Whether
    # that file belongs to THIS run is the rule's business, not the hook's.
    # Only the chain grounds are excused — a pipe still masks.
    capture_rx=';[[:space:]]*echo[[:space:]]+\$\?[[:space:]]*>'
    masked=""
    if [[ -z "$pipe_preserved" ]] && [[ "$cmd_bare" =~ $pipe_rx ]]; then
        masked=pipe
    elif [[ "$cmd_bare" =~ $semi_rx || "$cmd_bare" =~ $or_rx ]]; then
        # The capture form preserves in the foreground and MASKS in the
        # background, and one flag is the whole difference: backgrounded, the
        # completion notice reports the status of the whole command, so the
        # trailing echo is the number the agent is told. Measured — a tier that
        # returned 1 on 30 failures was announced as "exit code 0" (HATS-1709).
        if [[ "$background" == "true" ]]; then
            masked=notice
        elif [[ ! "$cmd_bare" =~ $capture_rx ]]; then
            masked=chain
        fi
    fi
    if [[ -n "$masked" ]]; then
        if [[ "$masked" == notice ]]; then
            msg="a backgrounded runner ends in another command — the completion notice reports the status of the WHOLE command, so that trailing element is the number you are told, and a red run arrives as 'exit code 0'. Measured. Put the capture inside and let the status out: timeout <n> bash -c '<runner>; rc=\$?; echo \$rc > <file>; exit \$rc' > <log> 2>&1 — then the notice and the file agree. Read the file either way: for a background launch a notice is never evidence of the run."
        elif [[ "$masked" == pipe ]]; then
            msg="exit code masking detected in test runner command — a pipeline's status is the LAST stage's, so the runner's is lost. Use set -o pipefail (correct in bash and zsh), or redirect and read the log in a separate call. \${PIPESTATUS[0]} is bash-only: in zsh it is unset, so exiting on it returns 0 for every run — the zsh name is \${pipestatus[1]}."
        else
            msg="exit code masking detected in test runner command — a command after ';' or '||' becomes the status of the whole thing, so the runner's is lost. set -o pipefail does NOT help here: it fixes which status a PIPELINE reports, never what runs next. Drop the trailing command and read the log in a separate call, or capture the status with '; echo \$? > <file>' and read that file."
        fi
        ai_hats_journal_catch dev_rule_exit_code_provenance nudge "$cmd"
        printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
        exit 0
    fi
fi

# --- allowlist: anything compound / piped / redirected is legitimately Bash --
# Bias to allow: a pipe, &&/||, ;, command-substitution, backtick, here-doc, or
# any redirection means the command is doing real shell work no single tool
# covers. A covered token inside a quoted string (e.g. grep 'a|b') also trips
# this and is allowed — a missed nudge is acceptable, a spurious one is noise.
# shellcheck disable=SC2016  # single quotes are intentional: match the LITERAL
# characters $( | ` etc. in the command string, never expand them.
case "$cmd" in
    *'|'* | *'&'* | *';'* | *'$('* | *'`'* | *'>'* | *'<'*) exit 0 ;;
esac

# --- map the leading command token to its dedicated tool ---------------------
read -r tok _rest <<<"$cmd"
tool=""
case "$tok" in
    grep | rg | egrep | fgrep) tool="Grep" ;;
    find)                      tool="Glob" ;;
    cat | head | tail)         tool="Read" ;;
    ls)
        # Only a recursive listing maps to Glob; plain `ls` is fine.
        [[ "$cmd" =~ (^|[[:space:]])-[a-zA-Z]*R([[:space:]]|$) ]] && tool="Glob"
        ;;
    sed | awk)
        # Only the in-place / rewrite form maps to Edit; stream use is fine.
        [[ "$cmd" =~ (^|[[:space:]])-i ]] && tool="Edit"
        ;;
esac
[[ -z "$tool" ]] && exit 0

# --- emit the non-blocking nudge (fixed text per tool; no command interpolation
#     so the additionalContext string is always valid JSON) -------------------
# The payload carries no tool inventory (tool_name / tool_input / cwd only), so
# the nudge cannot know whether the tool exists in this session — it says "if
# available" rather than naming a tool that may not be there (HATS-1630).
case "$tool" in
    Grep) msg="raw grep/rg search detected — dev_rule_tool_call_hygiene: if the Grep tool is available, prefer it (native ripgrep, structured paginated output, no shell parse); if not, batch your searches instead of running them one by one.";;
    Glob) msg="raw find / ls -R detected — dev_rule_tool_call_hygiene: if the Glob tool is available, prefer it (pattern matching without a recursive shell walk); if not, scope the walk tightly instead of listing broadly.";;
    Read) msg="raw cat/head/tail detected — dev_rule_tool_call_hygiene: if the Read tool is available, prefer it (numbered lines, safe pagination, no context flood); if not, bound the output instead of dumping whole files.";;
    Edit) msg="in-place sed/awk edit detected — dev_rule_tool_call_hygiene: if the Edit tool is available, prefer it (uniqueness-checked; prevents silent multi-replace); if not, verify the match is unique before rewriting.";;
esac
ai_hats_journal_catch dev_rule_tool_call_hygiene nudge "$cmd"
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
exit 0
