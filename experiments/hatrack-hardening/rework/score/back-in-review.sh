#!/usr/bin/env bash
# Success = the rework loop returned the card to review after addressing the
# comments: review -> execute -> document -> review (edge landed, HATS-1052).
set -euo pipefail
grep -q '^state: review$' "$1/backlog/TEST-001/task.yaml"
