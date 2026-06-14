#!/usr/bin/env bash
# HATS-470 — block raw destructive ops outside ai_hats.safe_delete.
#
# Enforces that no new `path.unlink(...)`, `shutil.rmtree(...)`, or
# `path.rmdir(...)` call sneaks into `src/ai_hats/` without either:
#   (a) being inside `src/ai_hats/safe_delete.py` (the single authorised
#       primitive site), OR
#   (b) carrying an explicit `# safe-delete: ok <reason>` marker anywhere in
#       the call's parenthesised span (reviewer-visible bypass with reason in
#       the diff). The span-aware match (HATS-757) survives `ruff format`
#       wrapping a long call across lines — which relocates the trailing marker
#       onto the closing-paren line, where a line-local whitelist could not see
#       it and false-positived on every commit.
#
# The aim is to prevent silent regressions where new code reintroduces
# the data-loss patterns HATS-470 just neutralised (see
# tracker/backlog/tasks/HATS-470/plan.md, Step 4).
#
# Scope: this hook only fires when `src/ai_hats/` exists in the
# repository — non-ai-hats projects that happen to include the
# git-mastery skill get a silent no-op.
#
# Override (single commit):  AI_HATS_NO_RAW_DESTRUCTIVE_SKIP=1 git commit ...
set -uo pipefail

if [[ "${AI_HATS_NO_RAW_DESTRUCTIVE_SKIP:-}" == "1" ]]; then
    echo "[no-raw-destructive] skipped via AI_HATS_NO_RAW_DESTRUCTIVE_SKIP=1" >&2
    exit 0
fi

# Resolve the CURRENT worktree root — we scan the source about to be
# committed, which lives in *this* worktree, not the main repo.
# (--show-toplevel correctly returns the worktree root; we deliberately
# do NOT use --git-common-dir here, which would jump to main.)
worktree_root="$(git rev-parse --show-toplevel 2>/dev/null || echo .)"
src_dir="$worktree_root/src/ai_hats"
if [[ ! -d "$src_dir" ]]; then
    # Non-ai-hats project — nothing to guard.
    exit 0
fi

# List the .py files that contain a destructive token. Prefer ripgrep,
# fall back to grep -rl (works everywhere). We only awk-scan files that have
# at least one token — same candidate set under both impls (rg/grep parity).
if command -v rg >/dev/null 2>&1; then
    list_files() {
        rg -l --type py \
            -e '\.unlink\(' \
            -e 'shutil\.rmtree\(' \
            -e '\.rmdir\(' \
            "$src_dir"
    }
else
    list_files() {
        grep -rl --include='*.py' \
            -E '\.unlink\(|shutil\.rmtree\(|\.rmdir\(' \
            "$src_dir"
    }
fi

# Span-aware whitelist (HATS-757):
#  - safe_delete.py is the single legitimate raw-ops site → skipped wholesale.
#  - A destructive call is acknowledged iff a `# safe-delete: ok ` marker
#    appears on ANY line of its parenthesised span (token line … closing-paren
#    line). awk tracks paren balance from the token line until the call's
#    parens rebalance, so the marker survives `ruff format` wrapping the call
#    across lines.
#
# Limitation: paren balance is counted over raw text, so parens or `#` inside
# string literals can confuse span boundaries. In that (contrived) case the
# scan OVER-reports (treats the call as unmarked and demands a marker) — it
# never silently passes an *unmarked* destructive call, so the guard stays safe.
scan_file() {
    awk '
    BEGIN { watching = 0; depth = 0; start = 0; marker = 0 }
    {
        if (!watching && $0 ~ /\.unlink\(|shutil\.rmtree\(|\.rmdir\(/) {
            watching = 1; start = NR; marker = 0; depth = 0
        }
        if (watching) {
            if ($0 ~ /# safe-delete: ok /) marker = 1
            opens = $0; o = gsub(/\(/, "", opens)
            closes = $0; c = gsub(/\)/, "", closes)
            depth += o - c
            if (depth <= 0) {
                if (!marker) print FILENAME ":" start
                watching = 0; depth = 0
            }
        }
    }
    END { if (watching && !marker) print FILENAME ":" start }
    ' "$1"
}

violators=""
while IFS= read -r f; do
    [[ -z "$f" ]] && continue
    [[ "$f" == *"src/ai_hats/safe_delete.py" ]] && continue
    out="$(scan_file "$f")"
    [[ -n "$out" ]] && violators+="${violators:+$'\n'}$out"
done < <(list_files)

if [[ -n "$violators" ]]; then
    echo "" >&2
    echo "ERROR (HATS-470): raw destructive call outside ai_hats.safe_delete:" >&2
    echo "$violators" >&2
    echo "" >&2
    echo "Use one of:" >&2
    echo "  from ai_hats.safe_delete import discard, replace" >&2
    echo "  discard(path, reason=\"...\", project_dir=...)" >&2
    echo "  replace(path, new_bytes, reason=\"...\", project_dir=...)" >&2
    echo "" >&2
    echo "For internal / ephemeral / empty-dir cases, add an inline marker:" >&2
    echo "  path.rmdir()  # safe-delete: ok empty-dir" >&2
    echo "  shutil.rmtree(cache)  # safe-delete: ok session-cache" >&2
    echo "" >&2
    echo "Override (one commit): AI_HATS_NO_RAW_DESTRUCTIVE_SKIP=1 git commit ..." >&2
    exit 1
fi

exit 0
