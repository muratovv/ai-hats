#!/usr/bin/env bash
# Success = SMOKE-001 ended in state plan.
set -euo pipefail
grep -q '^state: plan$' "$1/backlog/SMOKE-001/task.yaml"
