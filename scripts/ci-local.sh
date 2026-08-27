#!/usr/bin/env bash
# HATS-922 — single source of truth for the CI test commands.
#
# Each CI job in .github/workflows/ci.yml calls one stage of this script, and a
# local pre-push run (`bash scripts/ci-local.sh`) runs them all. Because CI and
# the local gate share THIS file, their commands cannot silently drift — the
# root cause of "green locally, red in CI" (the coverage-job command was never
# run locally).
#
# Non-unit tools are invoked as `python -m <tool>` so the same line works in CI (deps
# pip-installed into `python`) and locally (venv python on PATH). Override the
# interpreter with PYTHON=/path/to/python.
#
# Usage:
#   scripts/ci-local.sh                   # the local bundle — the `all` branch names it
#   scripts/ci-local.sh tmp-sweep         # housekeeping only: reap dead test cruft from TMPDIR
#   scripts/ci-local.sh lint              # one stage (used by the matching CI job)
#   scripts/ci-local.sh coverage          # the stage that was the sole failing executor
#   scripts/ci-local.sh security          # CI-only stage; env-scoped (see NOTE below)
#   scripts/ci-local.sh --stages done-gate  # what review->done demands (HATS-1137)
#   scripts/ci-local.sh no-such-stage     # exit 2, listing every stage there is
#
# NOTE: the `install-smoke` CI job is deliberately NOT a stage here — it runs
# install-launcher.sh which writes ~/.local/bin/ai-hats, an unwanted side effect
# on a dev box. It stays inline in ci.yml.
#
# NOTE: `security` (pip-audit) is a stage CI calls but is EXCLUDED from the local
# `all` bundle — pip-audit audits the active interpreter's WHOLE environment, so a
# polluted dev venv reports CVEs in packages ai-hats never declares (green in CI's
# clean install, red locally). CI is authoritative for the audit; run the stage
# explicitly against the project venv (`PYTHON=.venv/bin/python`) to reproduce CI.
set -euo pipefail

export PYTHONDONTWRITEBYTECODE=1

repo_root="$(git rev-parse --show-toplevel)"
cd "$repo_root"

if [[ -z "${PYTHON:-}" && -x "$repo_root/.venv/bin/python" ]]; then
    PY="$repo_root/.venv/bin/python"
elif [[ -z "${PYTHON:-}" && -x "$repo_root/.venv/bin/python3" ]]; then
    PY="$repo_root/.venv/bin/python3"
else
    PY="${PYTHON:-python}"
fi

ci_tmp_sweep() {
    # HATS-1624: reap what killed runs leave in TMPDIR. Housekeeping, not a
    # check — it is in the `all` bundle and in NO gate composition, because a
    # gate names what must be green and this can only free space. Runs FIRST so
    # the heavy stages below get the space, and never fails the bundle: the
    # sweeper reports an unremovable dir on stderr and its own exit code says so.
    local sweep="$repo_root/scripts/clean-tmp-cruft.sh"
    [[ -x "$sweep" ]] || return 0
    echo "[ci-local] tmp-sweep (reap provably-dead test cruft)" >&2
    bash "$sweep" || echo "[ci-local] tmp-sweep left dirs behind (see above); continuing" >&2
}

ci_lint() {
    echo "[ci-local] lint (ruff check + format)" >&2
    "$PY" -m ruff check .
    # HATS-1372: the formatter check lived only in `make lint`, behind a failing
    # `ruff check` — so 20 unformatted files sat on master unseen for weeks.
    # HATS-1651: the same `.` as the line above. Two scopes could not stay equal
    # by convention, and ruff's own extend-exclude is the one that should decide.
    "$PY" -m ruff format --check .
}

ci_unit() {
    echo "[ci-local] unit (pytest -m 'not integration')" >&2
    env \
        -u VIRTUAL_ENV \
        -u VIRTUAL_ENV_PROMPT \
        -u PYTHONPATH \
        -u PYTHONHOME \
        -u PYTHONUSERBASE \
        uv run --isolated --no-project --python "$PY" --with-editable ".[dev]" \
        python -B -m pytest -m "not integration" -q ${@+"$@"}
}

# HATS-1137: the integration tier OUTSIDE tests/e2e — the half `unit` excludes
# by marker and `merge-smoke` does not reach by path. Without it the done-gate
# would call itself green while skipping every real-subprocess test in tests/.
ci_integration() {
    echo "[ci-local] integration (pytest --ignore=tests/e2e -m integration)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e -m integration -q ${@+"$@"}
}

ci_coverage() {
    echo "[ci-local] coverage (unit + real-git integration, --cov-fail-under=78)" >&2
    "$PY" -B -m pytest --ignore=tests/e2e/ \
        --cov=ai_hats \
        --cov-report=term-missing \
        --cov-report=xml \
        --cov-fail-under=78 \
        -q ${@+"$@"}
}

ci_security() {
    echo "[ci-local] security (bandit + pip-audit)" >&2
    "$PY" -m bandit -r src/ -ll -q
    "$PY" -m pip_audit
}

ci_merge_smoke() {
    echo "[ci-local] merge-smoke (curated e2e subset)" >&2
    "$PY" -B -m pytest -m "smoke and not quarantine and not live_claude" tests/e2e/ -q ${@+"$@"}
}

# Offline and instant, so unlike version-skew it belongs in the `all` bundle.
ci_dependency_floor() {
    echo "[ci-local] dependency-floor (pins vs workspace versions)" >&2
    "$PY" scripts/check_dependency_floor.py
}

# Offline and instant, like the others. HATS-1599: a ratchet, so it is green
# only while the tree patches its own units no more than the recorded baseline.
ci_test_isolation() {
    echo "[ci-local] test-isolation (patching of code under test vs the baseline)" >&2
    "$PY" scripts/check_test_isolation.py
}

# Offline and instant, like dependency-floor — so it belongs in `all` too.
ci_silent_fallback() {
    echo "[ci-local] silent-fallback (broad handlers nothing can escape from)" >&2
    "$PY" scripts/check_silent_fallback.py
}

# Offline and instant, like the two above. HATS-1498: the flow catalog is
# rendered from the tests' own docstrings, so it goes stale the moment one is
# edited without regenerating.
ci_e2e_catalog() {
    echo "[ci-local] e2e-catalog (tests/e2e/CATALOG.md vs the flow blocks)" >&2
    "$PY" scripts/gen_e2e_catalog.py --check
}

# Offline and instant, like the three above. HATS-1646: prose carries no assert,
# so a citation into an ADR rots green — two such defects lived for months.
ci_adr_integrity() {
    echo "[ci-local] adr-integrity (ADR citations resolve; a number names one file)" >&2
    "$PY" scripts/check_adr_integrity.py
}

# Offline and instant, like the four above. HATS-1825: `adr-integrity` proved a
# gate on prose is buildable and was aimed at one rigid citation form; this is the
# same gate aimed at the library's own references, where 21 had already rotted.
ci_prose_refs() {
    echo "[ci-local] prose-refs (paths, library prefixes, sections and symbols in library prose)" >&2
    "$PY" scripts/check_prose_refs.py
}

# Offline and instant, like the five above. Sibling of `prose-refs` aimed at what
# prose must NOT carry rather than what it must resolve: the library installs
# into other projects, where this repo's tracker ids are dead links. It reports
# the ids it still finds in docs/adr and CHANGELOG, so a clean run proves the
# pattern is alive rather than merely silent.
ci_ticket_ids() {
    echo "[ci-local] ticket-ids (no tracker id in shipped library prose)" >&2
    "$PY" scripts/check_no_ticket_ids.py
}

# The full maintainer tier (the slow one). Excluded from `all`; this is the selection
# the master pre-push gate runs, kept here so `make e2e` cannot mean something
# narrower than the gate that guards the push (HATS-1372).
ci_e2e() {
    echo "[ci-local] e2e (integration + smoke, quarantine and live agy excluded)" >&2
    "$PY" -B -m pytest -m "(integration or smoke) and not quarantine and not live_agy" tests/e2e/ tests/smoke/ -q ${@+"$@"}
}

# HATS-1137/HATS-1604/HATS-1614 — what each gate is made of, and the ONE place
# it is configured. A gate script asks with `--stages <gate>` and runs them
# through the shared primitive, so "green enough to merge" is an edit HERE and
# the library never restates it (ADR-0023 D7).
#
# `merge-gate` MUST stay a subset of `done-gate`. That is what lets one run pay
# for both (absorption, ADR-0023 D5) — `tests/test_gate_entrypoint_parity.py`
# refuses a composition that breaks it.
#
# `tier` is the linting tier: offline, under eight seconds together. It leads
# with `e2e-catalog` — the slowest of the five at 7s against under a second each
# — because the primitive stops at the first red and a stale CATALOG.md is the
# structural failure worth refusing before anything else starts (HATS-1562).
#
# `integration` sits on `->done`, as ADR-0023 D4 assigns it. It lived on
# `->merge` while the edge passed hollow on most cards (HATS-1614's temporary
# ruling); HATS-1664 made the edge judge the merge result, so the 415
# real-subprocess tests outside tests/e2e are on a blocking road again.
#
# No gate joins `all`: `all` is the pre-push bundle and already runs `coverage`,
# which collects the same non-e2e integration tests unfiltered.
gate_composition() {
    local tier="e2e-catalog lint dependency-floor silent-fallback test-isolation prose-refs ticket-ids"
    case "$1" in
        merge-gate) echo "$tier unit" ;;
        done-gate) echo "$tier unit integration merge-smoke" ;;
        push-gate) echo "lint unit e2e-catalog adr-integrity prose-refs ticket-ids e2e" ;;
        *) return 1 ;;
    esac
}

# Make THIS checkout runnable, so `$PY` above resolves to an interpreter that
# imports this tree and not another one. NOT a stage and in no gate composition:
# it asserts nothing and can only be a precondition (HATS-1664).
#
# Who asks: the gate primitive, before running a gate inside a scratch checkout
# of a merge commit — that checkout is minted by `git worktree add` and has no
# `.venv` at all, so without this every real-subprocess test would exercise the
# MAIN checkout's installed code while claiming to judge the commit. The hook it
# delegates to is the same one every task worktree gets (HATS-1291); an already
# usable `.venv` makes it a no-op.
ci_prepare() {
    echo "[ci-local] prepare (a venv for this checkout, if it needs one)" >&2
    # From the TREE, not from an installed library: an unprepared checkout has no
    # interpreter that could import one, and this repository carries the hook's
    # source anyway — so the version that runs is the one belonging to the
    # content under judgement.
    local hook="$repo_root/packages/ai-hats-library/src/ai_hats_library/usage/skills/worktree-venv/hooks/provision-venv.sh"
    if [[ ! -f "$hook" ]]; then
        echo "[ci-local] no provision-venv hook at $hook — nothing to prepare" >&2
        return 1
    fi
    AI_HATS_WORKTREE_PATH="$repo_root" bash "$hook"
}

# NOTE: excluded from the local `all` bundle — it queries PyPI, so an offline
# dev box would fail a legitimate push. CI is authoritative; run explicitly
# (optionally SKEW_BASE=<sha>) to reproduce.
ci_version_skew() {
    echo "[ci-local] version-skew (workspace pkgs ahead of PyPI)" >&2
    "$PY" scripts/check_pkg_version_skew.py "${SKEW_BASE:-origin/master}"
}

# The stage set IS the set of `ci_*` functions defined above: the dispatch and
# the usage line below both read it, so a stage can no longer be reachable and
# unlisted (`tmp-sweep` was, HATS-1716) or listed and unreachable. The other
# side of the convention: a helper that is not a stage does not take the `ci_`
# prefix — `gate_composition` is one.
known_stages() {
    declare -F | sed -n 's/^declare -f ci_//p' | tr '_' '-' | sort
}

stage="${1:-all}"
shift 2>/dev/null || true   # remaining argv is passed through to the pytest stages
case "$stage" in
    # A gate is not a stage: it is a NAME for a set of them, and running it is
    # the primitive's job (it owns the marker). `--stages` comes FIRST so a
    # dispatcher that does not know the flag refuses instantly instead of
    # mistaking it for an argument to a gate it does know (HATS-1604).
    # Also not a stage, and for the same reason as `--stages`: it is asked BEFORE
    # any stage runs, and it must be its own process — `$PY` is resolved once at
    # the top of this script, so an interpreter minted here is only seen by the
    # next invocation (HATS-1664).
    --prepare) ci_prepare ;;
    --stages)
        gate_composition "${1:-}" || {
            echo "[ci-local] no such gate: ${1:-<none>} (gates: merge-gate | done-gate | push-gate)" >&2
            exit 2
        }
        ;;
    merge-gate|done-gate|push-gate)
        echo "[ci-local] '$stage' is a gate, not a stage — it names: $(gate_composition "$stage")" >&2
        echo "  its composition:  scripts/ci-local.sh --stages $stage" >&2
        echo "  run it (marks the tree on green):  make merge-gate | make done-gate | scripts/run-e2e-gate.sh" >&2
        exit 2
        ;;
    all)
        # security is intentionally omitted — pip-audit is env-scoped (see NOTE).
        ci_tmp_sweep
        ci_lint
        ci_dependency_floor
        ci_silent_fallback
        ci_test_isolation
        ci_e2e_catalog
        ci_adr_integrity
    ci_ticket_ids
        ci_unit
        ci_coverage
        ci_merge_smoke
        echo "[ci-local] local stages passed (security is CI-authoritative)" >&2
        ;;
    *)
        fn="ci_$(printf '%s' "$stage" | tr '-' '_')"
        if declare -F "$fn" >/dev/null 2>&1; then
            "$fn" ${@+"$@"}
        else
            echo "[ci-local] unknown stage: $stage" >&2
            echo "  stages: $(known_stages | tr '\n' ' ')" >&2
            echo "  bundle: all (the local pre-push bundle, and the default)" >&2
            echo "  gates (--stages prints their composition): merge-gate | done-gate | push-gate" >&2
            echo "  --prepare: mint a venv for this checkout (a precondition, never a check)" >&2
            exit 2
        fi
        ;;
esac
