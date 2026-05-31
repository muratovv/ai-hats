#!/usr/bin/env bash
# HATS-617 — git pre-commit: lint STAGED library SKILL.md files with agnix.
#
# Ships with the `skill-lint-gate` skill, attached to the `skill-engineer`
# trait → installed only for the `maintainer` and `role-curator` roles (the
# two roles that author library skills).
#
# Scope: only STAGED `library/**/SKILL.md` files are checked, EXCLUDING the
# third-party `golang-*` pack (HATS-627 decision — pack drift is handled
# separately, not as a blocking gate). Changed-files scope means the gate only
# fires on commits that touch an authored skill; it never retro-blocks the
# pre-existing backlog (HATS-626).
#
# agnix is invoked through an overridable command so tests can stub it and the
# pinned version lives in one place:
#   AI_HATS_SKILL_LINT_CMD   (default: "npx --yes agnix@0.29.0")
# The rule policy + target client live in the repo-root `.agnix.toml`, which
# agnix auto-discovers from the commit's cwd (repo root).
#
# Fail-open: if the agnix runner binary is absent the hook is a LOUD no-op — a
# missing optional dev tool must never wedge a commit (mirrors pre-commit-smoke).
# Inside an ai-hats agent node IS present, so the gate is live there.
#
# Override (per commit, after the user confirms the skill is intentionally
# non-conforming):
#   AI_HATS_SKILL_LINT_ACK=1 git commit ...
set -uo pipefail

if [[ "${AI_HATS_SKILL_LINT_ACK:-}" == "1" ]]; then
    echo "[skill-lint] AI_HATS_SKILL_LINT_ACK=1 — allowing commit" >&2
    exit 0
fi

# Staged (added/copied/modified) library SKILL.md, excluding the golang-* pack.
# Collected without `mapfile` (bash 4+) so the hook runs on macOS system bash 3.2.
files=()
while IFS= read -r _f; do
    [[ -n "$_f" ]] && files+=("$_f")
done < <(
    git diff --cached --name-only --diff-filter=ACM \
        | grep -E '^library/.*/SKILL\.md$' \
        | grep -vE '(^|/)golang-[^/]+/SKILL\.md$' \
        || true
)
[[ ${#files[@]} -eq 0 ]] && exit 0

# Resolve the agnix command (overridable for tests / version pinning).
read -r -a _cmd <<< "${AI_HATS_SKILL_LINT_CMD:-npx --yes agnix@0.29.0}"

# Fail-open if the runner binary is unavailable.
if ! command -v "${_cmd[0]}" >/dev/null 2>&1; then
    echo "[skill-lint] '${_cmd[0]}' not found — SKILL.md lint SKIPPED (fail-open)" >&2
    exit 0
fi

output="$("${_cmd[@]}" "${files[@]}" 2>&1)"
rc=$?

if [[ $rc -ne 0 ]]; then
    {
        echo "[skill-lint] BLOCKED — agnix flagged staged SKILL.md:"
        echo "$output" | head -40
        echo ""
        echo "Fix the issue(s), or skip this single commit after confirming the"
        echo "skill is intentionally non-conforming:"
        echo "  AI_HATS_SKILL_LINT_ACK=1 git commit ..."
    } >&2
    exit 1
fi

exit 0
