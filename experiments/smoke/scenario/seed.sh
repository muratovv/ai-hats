#!/usr/bin/env bash
# Seed the smoke scenario: one card in brainstorm.
set -euo pipefail
cd "$1"
rack create "Smoke: trivial lifecycle card" --id SMOKE-001 >&2
