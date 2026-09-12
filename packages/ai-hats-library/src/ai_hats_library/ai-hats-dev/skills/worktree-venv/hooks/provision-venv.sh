#!/usr/bin/env bash
# HATS-1291 — mint a venv inside a freshly created worktree.
#
# Without one, `sys.executable` in the worktree is MAIN's interpreter, whose
# editable install points at MAIN. The in-process test tier survives that
# (pytest's `pythonpath` ini redirects it), but the e2e tier strips PYTHONPATH
# by design (HATS-685) and so tests the wrong checkout — the HATS-1242 guard
# then aborts the first commit.
#
# Two callers, two contracts:
#   bare     — the wt_in hook. warn-continue (ADR-0012 D3/D7): every failure
#              path exits 0 with a note, because a worktree without .venv is
#              the pre-HATS-1291 status quo and never worse than a failed
#              create. A usable .venv short-circuits: the question there is
#              whether the venv SURVIVED, not what is in it.
#   --sync   — `gates.sh --prepare`. A usable .venv is not enough: the tree's
#              pins move under a long-lived worktree (a rebase) and under MAIN
#              (a pull), and the stages would then judge one dependency set
#              while the marker names another (HATS-1939). Always installs, and
#              owes its caller an honest rc when the install fails.
#
# Runs with cwd = MAIN checkout, so everything below is relative to
# $AI_HATS_WORKTREE_PATH.
set -uo pipefail

SYNC=''
if [[ "${1:-}" == "--sync" ]]; then
    SYNC=1
    shift
fi

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
usable=''
if [[ -x "$WORKTREE/.venv/bin/python" && -f "$WORKTREE/.venv/pyvenv.cfg" ]] \
    && compgen -G "$WORKTREE/.venv/lib/python*/site-packages/*.dist-info/RECORD" >/dev/null; then
    if [[ -z "$SYNC" ]]; then
        echo "[worktree-venv] .venv already usable — nothing to do"
        exit 0
    fi
    usable=1
fi

# Asked before the rebuild below: without uv there is nothing to rebuild WITH,
# and deleting a broken venv we cannot replace only widens the hole.
if ! command -v uv &>/dev/null; then
    echo "[worktree-venv] uv not found — skipping (install uv, then:"
    echo "                cd $WORKTREE && uv venv .venv && VIRTUAL_ENV=.venv uv pip install -e .)"
    exit 0
fi

cd "$WORKTREE" || exit 0

if [[ -z "$usable" ]]; then
    if [[ -e ".venv" ]]; then
        # Gutted or half-built: `uv venv` reuses the shell and would inherit the damage.
        echo "[worktree-venv] .venv present but unusable — rebuilding it"
        rm -rf ".venv"
    fi
    if ! uv venv -q .venv; then
        echo "[worktree-venv] 'uv venv' failed — worktree left unprovisioned"
        exit 0
    fi
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

# Quiet for wt_in, where a person is waiting and uv's package roll is not a
# finding. Loud for --sync, where that roll IS the diagnostic: HATS-1862 cost
# an investigation because nothing anywhere named the package that had moved.
install=(uv pip install)
[[ -n "$SYNC" ]] || install+=(-q)

if ! VIRTUAL_ENV=.venv "${install[@]}" "${targets[@]}"; then
    echo "[worktree-venv] editable install failed — re-run to finish:"
    echo "                cd $WORKTREE && VIRTUAL_ENV=.venv uv pip install ${targets[*]}"
    [[ -n "$SYNC" ]] && exit 1
    exit 0
fi

if [[ -n "$SYNC" ]]; then
    echo "[worktree-venv] .venv installed from $WORKTREE/pyproject.toml pins"
else
    echo "[worktree-venv] provisioned $WORKTREE/.venv"
fi
exit 0
