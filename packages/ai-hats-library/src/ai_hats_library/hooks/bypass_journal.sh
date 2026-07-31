#!/usr/bin/env bash
# ai-hats managed — do not edit
# HATS-1407 — durable record of every gate bypass. Sourced, never executed.
#
# A hatch (AI_HATS_*_ACK / _SKIP / _OFF / YOLO) and a fail-open branch both let
# a commit past a gate while printing only to stderr, which no one can read an
# hour later. This appends one JSONL line per bypass to
#   $(git rev-parse --git-common-dir)/ai-hats/bypasses.jsonl
# — the COMMON dir, so a bypass from a worktree lands in the journal the
# reviewer reads on the main checkout.
#
# Usage, from inside a hook, before the `exit 0` that skips the gate:
#   . "$(dirname "$0")/../bypass_journal.sh"    # .githooks/<event>.d/ -> .githooks/
#   ai_hats_journal_bypass hatch AI_HATS_PRIVACY_ACK
#
# `sha` is empty for pre-commit (the commit does not exist yet) and stamped by
# the post-commit hook; `head_before` pins the parent so the two can be joined.

# Exported so the contract test can source this file and diff the list against
# the Python twin's — the two writers must not drift apart.
export AI_HATS_BYPASS_FIELDS="ts event hook kind reason head_before branch session_id sha"

_ai_hats_json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '%s' "$s"
}

# ai_hats_journal_bypass <kind> <reason>
#   kind   : hatch | fail_open
#   reason : the env var name (hatch) or the missing prerequisite (fail_open)
ai_hats_journal_bypass() {
    local kind="${1:-unknown}" reason="${2:-unspecified}"
    local git_dir journal ts hook event head branch session

    if ! git_dir="$(git rev-parse --git-common-dir 2>/dev/null)"; then
        # Not a git repo (or git is broken). Refusing to be silent about it:
        # an unrecorded bypass is the defect this file exists to remove.
        echo "[bypass-journal] NOT RECORDED ($kind: $reason) — no git dir" >&2
        return 0
    fi

    journal="${git_dir}/ai-hats/bypasses.jsonl"
    if ! mkdir -p "${git_dir}/ai-hats" 2>/dev/null; then
        echo "[bypass-journal] NOT RECORDED ($kind: $reason) — cannot create ${git_dir}/ai-hats" >&2
        return 0
    fi

    ts="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    hook="$(basename "${0:-unknown}")"
    event="${AI_HATS_HOOK_EVENT:-unknown}"
    head="$(git rev-parse HEAD 2>/dev/null || printf '')"
    branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || printf '')"
    session="${AI_HATS_SESSION_ID:-}"

    # pre-commit runs before the commit object exists — post-commit stamps `sha`.
    local sha=""
    [[ "$event" == "pre-commit" ]] || sha="$head"

    if ! printf '{"ts":"%s","event":"%s","hook":"%s","kind":"%s","reason":"%s","head_before":"%s","branch":"%s","session_id":"%s","sha":"%s"}\n' \
        "$(_ai_hats_json_escape "$ts")" \
        "$(_ai_hats_json_escape "$event")" \
        "$(_ai_hats_json_escape "$hook")" \
        "$(_ai_hats_json_escape "$kind")" \
        "$(_ai_hats_json_escape "$reason")" \
        "$(_ai_hats_json_escape "$head")" \
        "$(_ai_hats_json_escape "$branch")" \
        "$(_ai_hats_json_escape "$session")" \
        "$(_ai_hats_json_escape "$sha")" >> "$journal" 2>/dev/null; then
        echo "[bypass-journal] NOT RECORDED ($kind: $reason) — write to $journal failed" >&2
    fi
    return 0
}
