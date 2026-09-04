#!/usr/bin/env bash
# HATS-1291 — wt_in hook: mint a venv inside a freshly created worktree.
#
# Without one, `sys.executable` in the worktree is MAIN's interpreter, whose
# editable install points at MAIN. The in-process test tier survives that
# (pytest's `pythonpath` ini redirects it), but the e2e tier strips PYTHONPATH
# by design (HATS-685) and so tests the wrong checkout — the HATS-1242 guard
# then aborts the first commit.
#
# wt_in is warn-continue (ADR-0012 D3/D7): every failure path here exits 0 with
# a note. A worktree without .venv is the pre-HATS-1291 status quo, never worse.
# Runs with cwd = MAIN checkout, so everything below is relative to
# $AI_HATS_WORKTREE_PATH.
set -uo pipefail

WORKTREE="${AI_HATS_WORKTREE_PATH:-}"
if [[ -z "$WORKTREE" || ! -d "$WORKTREE" ]]; then
    echo "[worktree-venv] no AI_HATS_WORKTREE_PATH — nothing to provision"
    exit 0
fi

# Python project only: no root pyproject.toml means this isn't ours to touch.
if [[ ! -f "$WORKTREE/pyproject.toml" ]]; then
    echo "[worktree-venv] no pyproject.toml in $WORKTREE — skipping"
    exit 0
fi

# Worktrees live under $TMPDIR, which the OS sweeps by deleting FILES and leaving
# the directory skeleton: a reaped venv keeps an executable bin/python pointing at
# a live interpreter while every installed .py is gone. `-x bin/python` alone said
# "present", so the hook skipped the worktree and the damage surfaced much later
# as a ModuleNotFoundError from whatever ran next. A RECORD inside a *.dist-info
# is the cheapest proof files survived, and stays project-agnostic (HATS-1339).
if [[ -x "$WORKTREE/.venv/bin/python" && -f "$WORKTREE/.venv/pyvenv.cfg" ]] \
    && compgen -G "$WORKTREE/.venv/lib/python*/site-packages/*.dist-info/RECORD" >/dev/null; then
    echo "[worktree-venv] .venv already usable — nothing to do"
    exit 0
fi

if [[ -e "$WORKTREE/.venv" ]]; then
    # Gutted or half-built: `uv venv` reuses the shell and would inherit the damage.
    echo "[worktree-venv] .venv present but unusable — rebuilding it"
    rm -rf "$WORKTREE/.venv"
fi

if ! command -v uv &>/dev/null; then
    echo "[worktree-venv] uv not found — skipping (install uv, then:"
    echo "                cd $WORKTREE && uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e .)"
    exit 0
fi

cd "$WORKTREE" || exit 0

if ! uv venv -q .venv; then
    echo "[worktree-venv] 'uv venv' failed — worktree left unprovisioned"
    exit 0
fi

# Editable targets: the root project (with dev extras, so pytest lands) plus
# every sub-package that carries its own pyproject.toml. Discovery beats a
# hardcoded list — a package added later needs no edit here.
targets=(-e ".[dev]")
while IFS= read -r manifest; do
    [[ -n "$manifest" ]] || continue
    pkg_dir="${manifest%/pyproject.toml}"
    pkg_dir="${pkg_dir#./}"
    targets+=(-e "$pkg_dir")
done < <(find packages -name pyproject.toml -type f 2>/dev/null | sort)

if ! VIRTUAL_ENV=.venv uv pip install -q "${targets[@]}"; then
    echo "[worktree-venv] editable install failed — re-run to finish:"
    echo "                cd $WORKTREE && VIRTUAL_ENV=.venv uv pip install ${targets[*]}"
    exit 0
fi

echo "[worktree-venv] provisioned $WORKTREE/.venv"
exit 0
