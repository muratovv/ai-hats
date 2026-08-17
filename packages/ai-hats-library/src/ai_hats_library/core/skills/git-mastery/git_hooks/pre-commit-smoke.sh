#!/usr/bin/env bash
# HATS-081 — smoke test pre-commit gate (managed by ai-hats / git-mastery skill)
#
# When the active backlog task carries the `integration` tag, this hook
# runs `pytest -m smoke` before allowing the commit. If any smoke test
# fails the commit is blocked. If no task is active, or the task lacks the
# tag, the hook is a silent no-op.
#
# Override (per single commit):  AI_HATS_SMOKE_SKIP=1 git commit ...
set -uo pipefail

# HATS-1407 — a bypass printed only to stderr leaves no trace an hour later.
# shellcheck source=../../../../hooks/bypass_journal.sh
if ! . "${AI_HATS_BYPASS_JOURNAL:-$(dirname "$0")/../../../../hooks/bypass_journal.sh}" 2>/dev/null; then
    ai_hats_journal_bypass() {
        echo "[bypass-journal] NOT RECORDED ($1: $2) — bypass_journal.sh missing" >&2
    }
fi

if [[ "${AI_HATS_SMOKE_SKIP:-}" == "1" ]]; then
    echo "[smoke] skipped via AI_HATS_SMOKE_SKIP=1" >&2
    ai_hats_journal_bypass hatch AI_HATS_SMOKE_SKIP
    exit 0
fi

# Resolve main repo root (works inside worktrees too).
# In a worktree, git-common-dir points at the main .git; dirname gives
# the main project root where .agent/ lives.
_git_common="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
if [[ -n "$_git_common" ]]; then
    main_root="${_git_common%/.git}"
else
    main_root="."
fi

task_dir="$main_root/.agent/ai-hats/tracker/backlog/tasks"
if [[ ! -d "$task_dir" ]]; then
    exit 0
fi

# Find executing tasks with 'integration' tag.
active_files=$(grep -rl 'state: execute' "$task_dir"/*/task.yaml 2>/dev/null || true)
[[ -z "$active_files" ]] && exit 0

has_integration=false
for f in $active_files; do
    if grep -qE '^\s*-\s*integration\s*$' "$f"; then
        has_integration=true
        break
    fi
done
$has_integration || exit 0

# HATS-1291: prefer the interpreter of the checkout being committed. PATH's
# pytest belongs to whatever venv is active, which in a worktree is MAIN — and
# its editable install makes the e2e tier (PYTHONPATH-stripped by design,
# HATS-685) test the wrong source, tripping the HATS-1242 guard.
# Deliberately --show-toplevel, NOT the --git-common-dir root resolved above:
# that one points at MAIN from inside a worktree, which is the bug itself.
_toplevel="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "$_toplevel" && -x "$_toplevel/.venv/bin/pytest" ]]; then
    PYTEST="$_toplevel/.venv/bin/pytest"
elif command -v pytest &>/dev/null; then
    PYTEST="pytest"
else
    echo "[smoke] pytest not found — skipping smoke gate" >&2
    ai_hats_journal_bypass fail_open "pytest not found"
    exit 0
fi

# HATS-1345 scopes the run to tests/e2e/; HATS-1352 only when it exists — pytest
# answers a missing path with rc=4, not the rc=5 handled below, so a consumer
# project without the dir had every commit blocked. Empty => no path => testpaths.
smoke_paths=()
[[ -d tests/e2e ]] && smoke_paths=(tests/e2e/)

# Run smoke tests (from cwd = worktree root where the code lives).
# HATS-887: strip GIT_* plumbing so the merge-smoke `git merge` that spawns this
# hook can't leak GIT_DIR into pytest and retarget a test's git off cwd onto real
# .git (the child `env -u` does not affect the parent commit).
# HATS-1661: PYTEST_ADDOPTS rides into EVERY pytest, so a flag absent from this
# venv (the gate's `-n8`, a stray `--cov`) fails argument parsing and reads here
# as a test failure.
output=$(env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE -u PYTEST_ADDOPTS \
    "$PYTEST" -m smoke -q --tb=line --no-header -p no:cacheprovider \
    ${smoke_paths[@]+"${smoke_paths[@]}"} 2>&1)  # ${a[@]+…}: bash 3.2 + set -u
rc=$?

if [[ $rc -eq 5 ]]; then
    # Exit 5 = "no tests collected". The marker isn't used yet — pass silently.
    exit 0
fi

if [[ $rc -ne 0 ]]; then
    echo "[smoke] pre-commit smoke tests FAILED:" >&2
    # Limit output to keep context manageable.
    echo "$output" | head -30 >&2
    echo "" >&2
    echo "Fix the failing tests or skip with:" >&2
    echo "  AI_HATS_SMOKE_SKIP=1 git commit ..." >&2
    exit 1
fi

exit 0
