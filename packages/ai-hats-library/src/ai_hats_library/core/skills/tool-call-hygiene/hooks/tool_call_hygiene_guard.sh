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

# --- kill switch -------------------------------------------------------------
[[ "${AI_HATS_TOOL_HYGIENE_OFF:-}" == "1" ]] && exit 0

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
case "$tool" in
    Grep) msg="raw grep/rg search detected — dev_rule_tool_call_hygiene: prefer the Grep tool (native ripgrep, structured paginated output, no shell parse).";;
    Glob) msg="raw find / ls -R detected — dev_rule_tool_call_hygiene: prefer the Glob tool (pattern matching without a recursive shell walk).";;
    Read) msg="raw cat/head/tail detected — dev_rule_tool_call_hygiene: prefer the Read tool (numbered lines, safe pagination, no context flood).";;
    Edit) msg="in-place sed/awk edit detected — dev_rule_tool_call_hygiene: prefer the Edit tool (uniqueness-checked; prevents silent multi-replace).";;
esac
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","additionalContext":"%s"}}\n' "$msg"
exit 0
