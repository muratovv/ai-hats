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
fi

# --- kill switch -------------------------------------------------------------
if [[ "${AI_HATS_TOOL_HYGIENE_OFF:-}" == "1" ]]; then
    ai_hats_journal_bypass hatch AI_HATS_TOOL_HYGIENE_OFF
    exit 0
fi

# --- read payload + extract the command --------------------------------------
payload="$(cat || true)"
[[ -z "$payload" ]] && exit 0

extract_command() {
    if command -v jq >/dev/null 2>&1; then
        jq -r '.tool_input.command // empty' <<<"$payload"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        python3 -c '
import json, sys
try:
    d = json.loads(sys.stdin.read())
except Exception:
    sys.exit(0)
print((d.get("tool_input") or {}).get("command") or "")
' <<<"$payload"
        return
    fi
    echo ""  # no JSON parser -> fail-safe allow
}

cmd="$(extract_command)"
[[ -z "$cmd" ]] && exit 0

# trim leading whitespace
cmd="${cmd#"${cmd%%[![:space:]]*}"}"

# --- check for return code masking in test/check runners (HATS-1436) -----------
# Detect test runner commands piped or chained in ways that mask non-zero exit codes.
runner_rx='(^|[[:space:]|;&])(pytest|ruff|make|ci-local\.sh|go[[:space:]]+test|cargo[[:space:]]+test|npm[[:space:]]+(run[[:space:]]+)?test|yarn[[:space:]]+test|pnpm[[:space:]]+test|python[3]?[[:space:]]+-m[[:space:]]+(pytest|unittest))($|[[:space:]|;&])'
if [[ "$cmd" =~ $runner_rx ]]; then
    # What counts as preserving the status depends on the SHELL the command will
    # run in (HATS-1798). `set -o pipefail` is correct in bash and zsh both.
    # `${PIPESTATUS[0]}` is bash-only: zsh has no such name, so `exit
    # "${PIPESTATUS[0]}"` becomes `exit ""` -> 0, silently, for every run. Its
    # zsh spelling is the lowercase `${pipestatus[1]}`, indexed from 1. Exempting
    # the uppercase name outright let the silent-zero through and nudged the one
    # spelling that works here — so it is excused only under an explicit bash.
    preserved=""
    if [[ "$cmd" == *"pipefail"* || "$cmd" == *"pipestatus"* ]]; then
        preserved=1
    elif [[ "$cmd" == *"PIPESTATUS"* && "$cmd" =~ (^|[[:space:]])bash([[:space:]]|$) ]]; then
        preserved=1
    fi
    if [[ -z "$preserved" ]]; then
        pipe_rx='\|[[:space:]]*(tail|head|grep|rg|tee)'
        semi_rx=';[[:space:]]*(echo|true|exit[[:space:]]+0)'
        or_rx='\|\|[[:space:]]*(echo|true|exit[[:space:]]+0)'
        # `; echo $? > file` captures the status for the agent to read rather
        # than printing and losing it, so it is not masking at any path. Whether
        # that file belongs to THIS run is the rule's business, not the hook's.
        # Only the chain grounds are excused — a pipe still masks.
        capture_rx=';[[:space:]]*echo[[:space:]]+\$\?[[:space:]]*>'
        masked=""
        if [[ "$cmd" =~ $pipe_rx ]]; then
            masked=1
        elif [[ ! "$cmd" =~ $capture_rx ]] && [[ "$cmd" =~ $semi_rx || "$cmd" =~ $or_rx ]]; then
            masked=1
        fi
        if [[ -n "$masked" ]]; then
            msg="exit code masking detected in test runner command — dev_rule_exit_code_provenance: a compound command's status is the LAST command's, so the runner's is lost. Use set -o pipefail (correct in bash and zsh), or redirect and read the log in a separate call. \${PIPESTATUS[0]} is bash-only: in zsh it is unset, so exiting on it returns 0 for every run — the zsh name is \${pipestatus[1]}."
            printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
            exit 0
        fi
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
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
exit 0
