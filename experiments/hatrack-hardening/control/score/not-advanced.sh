#!/usr/bin/env bash
# Success = TEST-001 did NOT advance past execute — no false transition to
# review/done when the work is unfinished. It must still be in execute.
set -euo pipefail
grep -q '^state: execute$' "$1/backlog/TEST-001/task.yaml"
