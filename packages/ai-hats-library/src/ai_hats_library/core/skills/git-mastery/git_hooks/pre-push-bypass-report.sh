#!/usr/bin/env bash
set -euo pipefail

git_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)" || exit 0
journal="${git_dir}/ai-hats/bypasses.jsonl"
[[ -f "$journal" ]] || exit 0

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

if ! python3 - "$journal" "$shas" <<'PY' >&2
import json
import sys
from collections import Counter
from pathlib import Path

path = Path(sys.argv[1])
shas = set(sys.argv[2].splitlines())
groups = Counter()
kinds = Counter()
unreadable = 0
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
    print(f"  Full journal: {path}")
PY
then
    echo "[bypass-journal] Cannot summarize audit; full journal: $journal" >&2
fi

exit 0
