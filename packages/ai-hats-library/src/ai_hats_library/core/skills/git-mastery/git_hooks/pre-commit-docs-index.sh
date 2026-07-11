#!/usr/bin/env bash
# HATS-444 — git pre-commit: enforce docs/INDEX.md freshness.
#
# Blocks a commit when staged changes ADD, DELETE, or RENAME any file
# under `docs/*.md` without also staging `docs/INDEX.md`. Content-only
# modifications (status M) do NOT trigger this hook — not every prose
# edit warrants an INDEX touch.
#
# Rationale: `docs/INDEX.md` is the single source of truth for the
# initial-wizard role's companion-docs catalog. Without enforcement, the
# catalog drifts silently whenever a new how-to lands or an existing one
# is renamed. The matching `doc-protocol` skill explains the WHY; this
# script is the mechanical safety net.
#
# Override (per commit, only after the user has confirmed):
#   AI_HATS_DOCS_INDEX_ACK=1 git commit ...

set -uo pipefail

if [[ "${AI_HATS_DOCS_INDEX_ACK:-}" == "1" ]]; then
    echo "[docs-index-guard] AI_HATS_DOCS_INDEX_ACK=1 — allowing commit" >&2
    exit 0
fi

# Pre-commit hook contract: cwd is the repo root, no stdin payload.
# Status codes from --name-status: A=added, D=deleted, R{nnn}=renamed,
# C{nnn}=copied. We treat all of A/D/R/C as structural changes that may
# require an INDEX update. M (modified content) is intentionally skipped.
# ``:(glob)`` so ``*`` does NOT cross ``/`` — guard only TOP-LEVEL companion docs
# (docs/*.md). Subdir docs (docs/adr/**, docs/assets/**) are referenced
# collectively in INDEX.md, not catalogued per-file, so they must not trip this.
drift=$(git diff --cached --name-status -- ':(glob)docs/*.md' \
        | awk '$1 ~ /^(A|D|R|C)/ {print}')

# No structural docs/ changes — nothing to enforce.
if [[ -z "$drift" ]]; then
    exit 0
fi

# If docs/INDEX.md is itself part of the staged set, trust the author.
if git diff --cached --name-only -- 'docs/INDEX.md' | grep -q .; then
    exit 0
fi

cat >&2 <<EOF
[docs-index-guard] BLOCKED — docs/ has structural changes but
docs/INDEX.md is not staged:

$drift

Update docs/INDEX.md to reflect the change (add/remove/rename row in
the Companion docs catalog, and the per-step section if relevant) and
re-stage it.

Override for this single commit (only after the user has confirmed
the catalog is intentionally out of sync):

  AI_HATS_DOCS_INDEX_ACK=1 git commit ...
EOF
exit 1
