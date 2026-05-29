#!/usr/bin/env bash
# E2E runtime-hook probe (HATS-601). Reads a Claude Code hook payload
# (tool_input JSON) on stdin. Exit 2 (deny) iff the payload carries the
# sentinel token RTHOOK_DENY; exit 0 (allow) otherwise. Mirrors the
# stdin → exit-code contract the real Claude hook channel uses, so the
# e2e can prove the materialized script is reachable and functional.
set -euo pipefail
payload="$(cat || true)"
if printf '%s' "$payload" | grep -q 'RTHOOK_DENY'; then
  echo "e2e-rthook: denied (sentinel matched)" >&2
  exit 2
fi
exit 0
