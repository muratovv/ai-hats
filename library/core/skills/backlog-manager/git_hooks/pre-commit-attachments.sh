#!/usr/bin/env bash
# HATS-402 — task attachments integrity (managed by backlog-manager skill).
#
# Blocks commits that introduce or modify files under
# `tasks/<HATS-NNN>/attachments/` without registering them via
# `ai-hats task attach add`. Catches the failure mode that produced
# PROP-004: agents bypassing the CLI with `mkdir/mv/echo` under the
# backlog tree.
#
# Override (per single commit):  AI_HATS_ATTACH_ACK=1 git commit ...
# Skip on machines without the CLI installed (no false-blocks for
# users who just clone the repo): handled below.
set -uo pipefail

if [[ "${AI_HATS_ATTACH_ACK:-}" == "1" ]]; then
    echo "[attach] override acknowledged via AI_HATS_ATTACH_ACK=1" >&2
    exit 0
fi

# Fallback: no ai-hats binary → silent skip + stderr warning. The hook
# must not break commits for collaborators who haven't installed the
# framework yet.
if ! command -v ai-hats >/dev/null 2>&1; then
    echo "[attach] ai-hats not in PATH — skipping attachment verification" >&2
    exit 0
fi

# Collect staged paths under any tasks/HATS-NNN/attachments/ subtree.
# `git diff --cached --name-only --diff-filter=ACMR` lists Added /
# Copied / Modified / Renamed — i.e. everything we want to gate.
mapfile -t staged < <(
    git diff --cached --name-only --diff-filter=ACMR \
        | grep -E '/tracker/backlog/tasks/HATS-[0-9]+/attachments/' \
        || true
)

if [[ ${#staged[@]} -eq 0 ]]; then
    exit 0
fi

# Extract unique task IDs from staged paths.
declare -A seen=()
task_ids=()
for path in "${staged[@]}"; do
    if [[ "$path" =~ /tasks/(HATS-[0-9]+)/attachments/ ]]; then
        tid="${BASH_REMATCH[1]}"
        if [[ -z "${seen[$tid]:-}" ]]; then
            seen[$tid]=1
            task_ids+=("$tid")
        fi
    fi
done

rc=0
for tid in "${task_ids[@]}"; do
    if ! out=$(ai-hats task attach verify "$tid" 2>&1); then
        echo "[attach] $tid has unregistered or drifted blobs:" >&2
        echo "$out" | sed 's/^/    /' >&2
        rc=1
    fi
done

if [[ $rc -ne 0 ]]; then
    echo "" >&2
    echo "[attach] Register staged blobs via:" >&2
    echo "    ai-hats task attach add <TASK_ID> <PATH>" >&2
    echo "Or override once:  AI_HATS_ATTACH_ACK=1 git commit ..." >&2
fi
exit $rc
