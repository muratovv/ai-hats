#!/usr/bin/env bash
# ai-hats managed — do not edit
# HATS-1486 — shell wrapper delegating bypass journaling to bypass_journal.py.
# Sourced, never executed.
#
# A hatch (AI_HATS_*_ACK / _SKIP / _OFF / YOLO) and a fail-open branch both let
# a commit past a gate while printing only to stderr. This delegates writing to
# bypass_journal.py located in the same directory.

# ai_hats_journal_bypass <kind> <reason> [cmd] [session_id]
#   kind   : hatch | fail_open
#   reason : the env var name (hatch) or the missing prerequisite (fail_open)
ai_hats_journal_bypass() {
    local kind="${1:-unknown}" reason="${2:-unspecified}" cmd_arg="${3:-}" session_arg="${4:-}"
    local helper_dir py_script hook_name

    if ! command -v python3 >/dev/null 2>&1; then
        echo "[bypass-journal] NOT RECORDED ($kind: $reason) — python3 missing" >&2
        return 0
    fi

    helper_dir="${BASH_SOURCE[0]%/*}"
    [[ "$helper_dir" == "${BASH_SOURCE[0]}" ]] && helper_dir="."
    py_script="${helper_dir}/bypass_journal.py"

    if [[ ! -f "$py_script" ]]; then
        echo "[bypass-journal] NOT RECORDED ($kind: $reason) — $py_script missing" >&2
        return 0
    fi

    hook_name="$(basename "${0:-unknown}")"

    python3 "$py_script" record \
        --kind "$kind" \
        --reason "$reason" \
        --hook "$hook_name" \
        --cmd "$cmd_arg" \
        --session-id "$session_arg" || true

    return 0
}

# ai_hats_journal_stamp_sha
#   pre-commit records `sha:""` because the commit does not exist yet. Called
#   from post-commit, this fills it in for the rows this commit came from.
ai_hats_journal_stamp_sha() {
    local git_dir journal helper_dir py_script
    git_dir="$(git rev-parse --git-common-dir 2>/dev/null)" || return 0
    journal="${git_dir}/ai-hats/bypasses.jsonl"
    [[ -f "$journal" ]] || return 0

    if ! command -v python3 >/dev/null 2>&1; then
        echo "[bypass-journal] sha NOT STAMPED — python3 missing" >&2
        return 0
    fi

    helper_dir="${BASH_SOURCE[0]%/*}"
    [[ "$helper_dir" == "${BASH_SOURCE[0]}" ]] && helper_dir="."
    py_script="${helper_dir}/bypass_journal.py"

    if [[ ! -f "$py_script" ]]; then
        echo "[bypass-journal] sha NOT STAMPED — $py_script missing" >&2
        return 0
    fi

    python3 "$py_script" stamp || true
    return 0
}

