#!/usr/bin/env bash
# E2E runtime-hook probe (HATS-601 / HATS-607). Reads a Claude Code hook
# payload (JSON) on stdin.
#
#   * Side-effect (HATS-607): append the payload's ``hook_event_name`` to the
#     marker file ``${RTHOOK_MARKER:-$PWD/.rthook-marker}``. An e2e asserts
#     the marker to prove the hook BODY actually executed (stronger than the
#     exit code alone).
#   * Exit contract (HATS-601): exit 2 (deny) iff the payload carries the
#     sentinel token RTHOOK_DENY; exit 0 (allow) otherwise. Mirrors the
#     stdin -> exit-code contract of the real Claude hook channel.
set -euo pipefail
payload="$(cat || true)"

# Best-effort extract hook_event_name (portable; no jq). Empty -> "unknown".
event="$(printf '%s' "$payload" \
  | grep -oE '"hook_event_name"[[:space:]]*:[[:space:]]*"[^"]+"' \
  | sed -E 's/.*:[[:space:]]*"([^"]+)"$/\1/' || true)"
[ -n "$event" ] || event="unknown"
printf '%s\n' "$event" >> "${RTHOOK_MARKER:-$PWD/.rthook-marker}"

if printf '%s' "$payload" | grep -q 'RTHOOK_DENY'; then
  echo "e2e-rthook: denied (sentinel matched)" >&2
  exit 2
fi
exit 0
