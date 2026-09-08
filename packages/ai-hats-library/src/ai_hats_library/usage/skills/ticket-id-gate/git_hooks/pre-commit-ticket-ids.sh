#!/usr/bin/env bash
# git pre-commit: refuse a tracker id in STAGED library prose.
#
# Ships with the `ticket-id-gate` skill, attached to the `skill-engineer` trait
# → installed only for the roles that author library content.
#
# The prefix is LEARNED from the project's own tracker card ids, never
# hardcoded: this script is itself shipped library content, so naming one
# project's prefix here would be the very leak it exists to refuse. Override
# with AI_HATS_TICKET_PREFIX; no tracker and no override means a loud no-op.
#
# Scope is STAGED files only, so the gate never retro-blocks a backlog it did
# not create. `hooks/` and `git_hooks/` are excluded — an id in code often IS
# the whole comment, and removing it there is a rewrite, not a deletion.
#
# Override (per commit, after confirming the id belongs):
#   AI_HATS_TICKET_IDS_ACK=1 git commit ...
set -uo pipefail

# shellcheck source=../../../../hooks/bypass_journal.sh
if ! . "${AI_HATS_BYPASS_JOURNAL:-$(dirname "$0")/../../../../hooks/bypass_journal.sh}" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
    ai_hats_journal_catch() {
        echo "[catch-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

if [[ "${AI_HATS_TICKET_IDS_ACK:-}" == "1" ]]; then
    echo "[ticket-ids] AI_HATS_TICKET_IDS_ACK=1 — allowing commit" >&2
    ai_hats_journal_bypass hatch AI_HATS_TICKET_IDS_ACK
    exit 0
fi

# The marker that keeps an id whose prose names a string a machine PRINTS.
marker='ticket-ids: allow'

prefix="${AI_HATS_TICKET_PREFIX:-}"
if [[ -z "$prefix" ]]; then
    # Learn it from a card id: the shortest `<prefix>-<digit>` run, so a
    # hyphenated prefix survives (MY-PROJ-42 -> MY-PROJ), matching rack.
    for _dir in "${AI_HATS_DIR:-.agent/ai-hats}"/tracker/backlog/tasks/*/; do
        [[ -d "$_dir" ]] || continue
        _id="$(basename "$_dir")"
        prefix="$(printf '%s' "$_id" | sed -n 's/^\(.*\)-[0-9].*$/\1/p')"
        [[ -n "$prefix" ]] && break
    done
fi

if [[ -z "$prefix" ]]; then
    echo "[ticket-ids] no tracker prefix found — check SKIPPED (set AI_HATS_TICKET_PREFIX)" >&2
    ai_hats_journal_bypass fail_open "no tracker prefix"
    exit 0
fi

# Staged library prose, in the three layouts the library has lived under.
# Collected without `mapfile` (bash 4+) so the hook runs on macOS bash 3.2.
files=()
while IFS= read -r _f; do
    [[ -n "$_f" ]] && files+=("$_f")
done < <(
    git diff --cached --name-only --diff-filter=ACM |
        grep -E '(^|/)(ai_hats_library|library|libraries)/.*\.(md|ya?ml)$' |
        grep -vE '(^|/)(hooks|git_hooks)/' ||
        true
)
[[ ${#files[@]} -eq 0 ]] && exit 0

# Digits are what separate an id from a placeholder: `<PREFIX>-NNN` teaches the
# shape of an id and must survive; `<PREFIX>-1430` cites history and must not.
pattern="\\b${prefix}-[0-9]+\\b"

violations=()
for _f in "${files[@]:-}"; do
    [[ -f "$_f" ]] || continue
    while IFS= read -r _hit; do
        [[ -n "$_hit" ]] && violations+=("$_f:$_hit")
    done < <(grep -nE "$pattern" "$_f" | grep -vF "$marker" || true)
done

if [[ ${#violations[@]} -gt 0 ]]; then
    {
        echo "[ticket-ids] BLOCKED — a tracker id in prose this library ships:"
        for _v in "${violations[@]}"; do echo "  ! $_v"; done
        echo ""
        echo "The library installs into other projects, where '${prefix}-<n>' is a"
        echo "dead link. Drop the id and keep the WHY in one line; long rationale"
        echo "belongs in an ADR, which ships inside the repository."
        echo ""
        echo "An id a machine PRINTS keeps its place — say so on the same line:"
        echo "  <!-- ${marker} the checkout guard prints this string -->"
        echo ""
        echo "Or skip this single commit after confirming intent:"
        echo "  AI_HATS_TICKET_IDS_ACK=1 git commit ..."
    } >&2
    ai_hats_journal_catch ticket-ids block "tracker id in staged library prose"
    exit 1
fi

exit 0
