#!/usr/bin/env bash
# HATS-700 — git pre-commit: block a commit that introduces a `see rule X`
# pointer to a rule the agent cannot read.
#
# Ships with the `rule-delivery-gate` skill, attached to the `skill-engineer`
# trait → installed only for the `maintainer` and `role-curator` roles (the two
# roles that author library traits/roles).
#
# Scope: fires only when a commit stages a library config.yaml (any of the three layouts named at the filter).
# injection — the place a dangling pointer is introduced). The check itself scans
# the whole working-tree `library/` (a pointer's existence depends on the
# library rules directory), so it cannot
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
if ! . "${AI_HATS_BYPASS_JOURNAL:-$(dirname "$0")/../../../../hooks/bypass_journal.sh}" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
    ai_hats_journal_catch() {
        echo "[catch-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

if [[ "${AI_HATS_RULE_DELIVERY_ACK:-}" == "1" ]]; then
    echo "[rule-delivery] AI_HATS_RULE_DELIVERY_ACK=1 — allowing commit" >&2
    ai_hats_journal_bypass hatch AI_HATS_RULE_DELIVERY_ACK
    exit 0
fi

# Staged (added/copied/modified) trait/role injections. Collected without
# `mapfile` so the hook runs on macOS system bash 3.2.
# Three spellings, because the library has moved once already: the monorepo
# package (ai_hats_library), the pre-monorepo root (library/, still used by the
# e2e fixtures), and a consumer project's local layer (libraries/). HATS-1437.
staged=()
while IFS= read -r _f; do
    [[ -n "$_f" ]] && staged+=("$_f")
done < <(
    git diff --cached --name-only --diff-filter=ACM |
        grep -E '(^|/)(ai_hats_library|library|libraries)/.*/config\.yaml$' \
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
# Fail-open if ai_hats is not importable (any python invocation).
# HATS-1337: match on the BASENAME — the venv branch above resolves an absolute
# `/…/.venv/bin/python3`, which `python*` never matched, so the probe was skipped
# and a venv without ai_hats BLOCKED the commit instead of skipping it.
if [[ "$(basename "${_cmd[0]}")" == python* ]] && ! "${_cmd[0]}" -c "import ai_hats" >/dev/null 2>&1; then
    echo "[rule-delivery] ai_hats not importable — rule-delivery check SKIPPED (fail-open)" >&2
    ai_hats_journal_bypass fail_open "ai_hats not importable"
    exit 0
fi

# Resolve the library root: prefer relative paths in cwd if present,
# otherwise resolve from the package via python (HATS-1437).
_libroot=""
if [[ -d "packages/ai-hats-library/src/ai_hats_library" ]]; then
    _libroot="packages/ai-hats-library/src/ai_hats_library"
elif [[ -d "library" ]]; then
    _libroot="library"
else
    _py="${_cmd[0]}"
    if [[ "$_py" != *python* ]]; then
        _toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
        if [[ -n "$_toplevel" && -x "$_toplevel/.venv/bin/python3" ]]; then
            _py="$_toplevel/.venv/bin/python3"
        else
            _py="python3"
        fi
    fi
    _libroot="$("$_py" -c 'import ai_hats_library,pathlib;print(pathlib.Path(ai_hats_library.__file__).parent)' 2>/dev/null || true)"
fi

if [[ -z "$_libroot" || ! -d "$_libroot" ]]; then
    echo "[rule-delivery] library root unresolved — check SKIPPED (fail-open)" >&2
    ai_hats_journal_bypass fail_open "library root unresolved"
    exit 0
fi
output="$("${_cmd[@]}" "$_libroot" 2>&1)"
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
    ai_hats_journal_catch rule_composition_value_contract block "undelivered rule pointer in staged injection"
    exit 1
fi

exit 0
