#!/usr/bin/env bash
# HATS-470 — block raw destructive ops outside ai_hats.safe_delete.
#
# Enforces that no new `path.unlink(...)`, `shutil.rmtree(...)`, or
# `path.rmdir(...)` call sneaks into `src/ai_hats/` without either:
#   (a) being inside `src/ai_hats/safe_delete.py` (the single authorised
#       primitive site), OR
#   (b) carrying an explicit `# safe-delete: ok <reason>` inline marker
#       on the same line (reviewer-visible bypass with reason in the diff).
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

# Pick a grep impl: prefer ripgrep (faster + line-numbered out of the box),
# fall back to grep -rn (works everywhere).
if command -v rg >/dev/null 2>&1; then
    scan() {
        rg -n --type py \
            -e '\.unlink\(' \
            -e 'shutil\.rmtree\(' \
            -e '\.rmdir\(' \
            "$src_dir"
    }
else
    scan() {
        grep -rn --include='*.py' \
            -E '\.unlink\(|shutil\.rmtree\(|\.rmdir\(' \
            "$src_dir"
    }
fi

# Whitelist:
#  - safe_delete.py is the single legitimate raw-ops site
#  - lines carrying `# safe-delete: ok ` are reviewer-acknowledged bypasses
violators=$(scan \
    | grep -v 'src/ai_hats/safe_delete\.py' \
    | grep -v '# safe-delete: ok ' \
    || true)

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
