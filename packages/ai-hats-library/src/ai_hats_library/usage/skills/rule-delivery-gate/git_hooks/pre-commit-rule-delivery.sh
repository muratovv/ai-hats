#!/usr/bin/env bash
# HATS-700 — git pre-commit: block a commit that introduces a `see rule X`
# pointer to a rule the agent cannot read.
#
# Ships with the `rule-delivery-gate` skill, attached to the `skill-engineer`
# trait → installed only for the `maintainer` and `role-curator` roles (the two
# roles that author library traits/roles).
#
# Scope: fires only when a commit stages a `library/**/config.yaml` (a trait/role
# injection — the place a dangling pointer is introduced). The check itself scans
# the whole working-tree `library/` (a pointer's deliverability depends on the
# global ALWAYS_ON_RULES + SUMMARIZED_IN_INJECTION + every config), so it cannot
# be a per-file diff. Changed-files SCOPE keeps the gate off commits that touch
# no injection; it never retro-blocks pre-existing content.
#
# The checker is the same pure function the G2 unit test uses, invoked through an
# overridable command so tests can stub it / pin the interpreter:
#   AI_HATS_RULE_DELIVERY_CMD   (default: "python3 -m ai_hats.rule_delivery")
#
# Fail-open: if python or the ai_hats package is absent the hook is a LOUD no-op
# — a missing dev tool must never wedge a commit (mirrors skill-lint /
# pre-commit-smoke). Inside an ai-hats dev/agent env the package IS present, so
# the gate is live there. CI runs the same invariant (G2) regardless.
#
# Override (per commit, after confirming the pointer is intentional):
#   AI_HATS_RULE_DELIVERY_ACK=1 git commit ...
set -uo pipefail

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# shellcheck source=../../../../hooks/bypass_journal.sh
if ! . "$(dirname "$0")/../bypass_journal.sh" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

if [[ "${AI_HATS_RULE_DELIVERY_ACK:-}" == "1" ]]; then
    echo "[rule-delivery] AI_HATS_RULE_DELIVERY_ACK=1 — allowing commit" >&2
    ai_hats_journal_bypass hatch AI_HATS_RULE_DELIVERY_ACK
    exit 0
fi

# Staged (added/copied/modified) trait/role injections. Collected without
# `mapfile` so the hook runs on macOS system bash 3.2.
staged=()
while IFS= read -r _f; do
    [[ -n "$_f" ]] && staged+=("$_f")
done < <(
    git diff --cached --name-only --diff-filter=ACM \
        | grep -E '^library/.*/config\.yaml$' \
        || true
)
[[ ${#staged[@]} -eq 0 ]] && exit 0

# Resolve the checker command (overridable for tests / interpreter pinning).
# The default prefers the committed checkout's own venv over PATH: in a worktree
# PATH's python3 is MAIN's, and whether IT can import ai_hats decides whether this
# gate runs at all — a fail-open skip, not a failure (HATS-1314, mirrors
# pre-commit-smoke.sh). --show-toplevel, not --git-common-dir: the latter points
# at MAIN from inside a worktree, which is the bug itself.
if [[ -n "${AI_HATS_RULE_DELIVERY_CMD:-}" ]]; then
    read -r -a _cmd <<< "$AI_HATS_RULE_DELIVERY_CMD"
else
    _toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
    if [[ -n "$_toplevel" && -x "$_toplevel/.venv/bin/python3" ]]; then
        _cmd=("$_toplevel/.venv/bin/python3" -m ai_hats.rule_delivery)
    else
        _cmd=(python3 -m ai_hats.rule_delivery)
    fi
fi

# Fail-open if the runner binary is unavailable.
if ! command -v "${_cmd[0]}" >/dev/null 2>&1; then
    echo "[rule-delivery] '${_cmd[0]}' not found — rule-delivery check SKIPPED (fail-open)" >&2
    ai_hats_journal_bypass fail_open "${_cmd[0]} not found"
    exit 0
fi
# Fail-open if ai_hats is not importable (the default python invocation).
if [[ "${_cmd[0]}" == python* ]] && ! "${_cmd[0]}" -c "import ai_hats" >/dev/null 2>&1; then
    echo "[rule-delivery] ai_hats not importable — rule-delivery check SKIPPED (fail-open)" >&2
    ai_hats_journal_bypass fail_open "ai_hats not importable"
    exit 0
fi

output="$("${_cmd[@]}" library 2>&1)"
rc=$?

if [[ $rc -ne 0 ]]; then
    {
        echo "[rule-delivery] BLOCKED — undelivered \`see rule X\` pointer:"
        echo "$output" | head -40
        echo ""
        echo "Fix the pointer, or skip this single commit after confirming it is"
        echo "intentional:"
        echo "  AI_HATS_RULE_DELIVERY_ACK=1 git commit ..."
    } >&2
    exit 1
fi

exit 0
