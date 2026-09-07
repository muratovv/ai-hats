#!/usr/bin/env bash
# Seed: one draft with two planted defects (a claim contradicted by config.example.yaml,
# and one idea stated twice) plus the file the claim can be checked against.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
cd "$1"

cp "$here/fixture/draft.md" draft.md
cp "$here/fixture/config.example.yaml" config.example.yaml
git add draft.md config.example.yaml
git -c user.email=exp@sandbox -c user.name=exp commit -q -m "add draft and example config"
