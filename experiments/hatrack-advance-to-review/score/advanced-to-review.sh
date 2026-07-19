#!/usr/bin/env bash
# Success = TEST-001 ended in review: advanced past execute, waiting, not done.
set -euo pipefail
grep -q '^state: review$' "$1/backlog/TEST-001/task.yaml"
