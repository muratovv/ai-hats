#!/usr/bin/env bash
# Success (post-HATS-1052) = the rework loop returned the card to review after
# addressing the comments: review -> execute -> document -> review.
set -euo pipefail
grep -q '^state: review$' "$1/backlog/TEST-001/task.yaml"
