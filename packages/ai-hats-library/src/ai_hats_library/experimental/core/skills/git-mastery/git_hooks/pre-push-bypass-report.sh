#!/usr/bin/env bash
set -euo pipefail

git_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0

# Two homes since HATS-1634: a row that could name its session lives in that
# session's dir, and only one that could not stays repo-wide. A reader that
# knows the second address alone misses every runtime bypass. `.agent/ai-hats`
# literally, mirroring the writer — an inherited AI_HATS_DIR names a foreign
# tracker.
journals=()
if [[ -f "${git_dir}/ai-hats/bypasses.jsonl" ]]; then
    journals+=("${git_dir}/ai-hats/bypasses.jsonl")
fi
while IFS= read -r found; do
    [[ -n "$found" ]] && journals+=("$found")
done < <(find "${git_dir}/../.agent/ai-hats/sessions/runs" \
    -maxdepth 2 -name bypasses.jsonl 2>/dev/null || true)
[[ ${#journals[@]} -gt 0 ]] || exit 0
journal="${journals[0]}${journals[1]+ (+$((${#journals[@]} - 1)) more)}"

ZERO="0000000000000000000000000000000000000000"
shas=""
while read -r _local_ref local_sha _remote_ref remote_sha; do
    [[ -z "${local_sha:-}" || "$local_sha" == "$ZERO" ]] && continue
    if [[ "${remote_sha:-$ZERO}" == "$ZERO" ]]; then
        revs="$(git rev-list "$local_sha" --not --remotes 2>/dev/null)" || {
            echo "[bypass-journal] Cannot read pushed commits; full journal: $journal" >&2
            exit 0
        }
    else
        revs="$(git rev-list "${remote_sha}..${local_sha}" 2>/dev/null)" || {
            echo "[bypass-journal] Cannot read pushed commits; full journal: $journal" >&2
            exit 0
        }
    fi
    [[ -z "$revs" ]] || shas+="${revs}"$'\n'
done

[[ -n "$shas" ]] || exit 0
if ! command -v python3 >/dev/null 2>&1; then
    echo "[bypass-journal] Cannot summarize: python3 missing; full journal: $journal" >&2
    exit 0
fi

if ! python3 - "$shas" "${journals[@]}" <<'PY' >&2
import json
import sys
from collections import Counter
from pathlib import Path

shas = set(sys.argv[1].splitlines())
paths = [Path(arg) for arg in sys.argv[2:]]
groups = Counter()
kinds = Counter()
unreadable = 0
for path in paths:
    if not path.is_file():
        continue
    with path.open(encoding="utf-8") as source:
        for line in source:
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                unreadable += 1
                continue
            if not isinstance(row, dict) or not isinstance(row.get("sha"), str):
                unreadable += 1
                continue
            if row["sha"] not in shas:
                continue
            kind = str(row.get("kind") or "unknown").replace("fail_open", "fail-open")
            category = kind if kind in {"hatch", "fail-open", "consent", "no-question"} else "other"
            kinds[category] += 1
            groups[(kind, str(row.get("hook") or "unknown"), str(row.get("reason") or ""))] += 1

if kinds:
    counts = ", ".join(f"{count} {kind}" for kind, count in sorted(kinds.items()))
    print(f"[bypass-journal] {sum(kinds.values())} audit events in pushed commits: {counts}")
    for (kind, hook, reason), count in groups.most_common(5):
        detail = " ".join(f"{kind} / {hook}: {reason}".split())
        detail = "".join(char for char in detail if char.isprintable())
        if len(detail) > 160:
            detail = detail[:157] + "..."
        print(f"  {count}x {detail}")
    if len(groups) > 5:
        print(f"  ... {len(groups) - 5} more groups in the full journal")
if unreadable:
    print(f"[bypass-journal] {unreadable} unreadable journal records; commit range unknown")
if kinds or unreadable:
    print("  Journals read:")
    for path in paths:
        print(f"    {path}")
PY
then
    echo "[bypass-journal] Cannot summarize audit; full journal: $journal" >&2
fi

exit 0
