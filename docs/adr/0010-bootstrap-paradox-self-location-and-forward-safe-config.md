# ADR-0010: The bootstrap paradox — self-location guard + forward-safe config

## Status

Accepted (HATS-786 epic, 2026-06). Realized by HATS-790 (remove the
console-script generator), HATS-791 (self-location guard + `bootstrap.sh`
recovery + stray-shadow detector), and HATS-792 (forward-safe `ai-hats.yaml`
reader). Documented by HATS-793.

## Context

`ai-hats` is a **host tool**, not a project dependency. The day-to-day entry
point is a host-global bash launcher at `~/.local/bin/ai-hats` that resolves a
per-project managed venv and exec's `<venv>/bin/python -m ai_hats "$@"`. The
package is meant to be driven by that launcher, on the host, against whichever
project the user `cd`'d into.

Two failure classes broke that model. Both are *bootstrap paradoxes*: the tool
needed to fix the problem is the very thing that is broken.

### C1 — "which code runs?" (the shadow problem)

Before HATS-790, `pyproject.toml` carried
`[project.scripts] ai-hats = "ai_hats.cli:main_entry"`. Every venv that ever did
`pip install ai-hats` — typically a *project's own app-venv* a user once
installed ai-hats into — therefore materialised a `bin/ai-hats` console script.
With `direnv` (or any rc that prepends a venv's `bin/` to `$PATH`), that stale
`bin/ai-hats` is reached **ahead of** the host launcher. It runs an old ai-hats,
mis-resolved against the wrong project. The user sees "ai-hats", believes the
host launcher ran, and silently gets stale code [1][2].

The paradox: `ai-hats self update` runs *from* the venv it is trying to fix. A
shadow that intercepts `ai-hats` also intercepts `ai-hats self update`, so the
in-band self-repair path cannot win the race against the thing shadowing it.

### C2 — "can I still read this file?" (forward-safe config)

`ai-hats.yaml` is `extra="forbid"`: typos must fail loud. But that strictness
cuts the wrong way across versions. A NEWER ai-hats can write a field an OLDER
binary does not know — `migration_step` did exactly this, added without a
`schema_version` bump (it is orthogonal to the yaml format by design). A naive
`extra="forbid"` load hard-crashes the older binary on every command. Worse, an
older binary that *did* tolerate the unknown field by dropping it would then
**silently lose** that field on the next `save()` — a forward round-trip that
quietly deletes data the user's newer ai-hats relies on [3].

C1 is "which code runs"; C2 is "can the code that runs still read (and not
corrupt) state a different version wrote". The epic treats them as one theme —
version skew between the running ai-hats and the on-disk world — and fixes each
with the cheapest layer that is provably safe.

## Decision

A **layered** fix. No single mechanism is the gate; each layer narrows the
attack surface the previous one left, and the policy biases hard toward
fail-open / fail-loud-with-recovery rather than toward a clever-but-brittle
re-exec.

### П1 — Remove the shadow generator at the source (Alt 5, HATS-790)

Delete `[project.scripts]` from `pyproject.toml`. With the generator gone, a
managed venv no longer materialises a shadowable `bin/ai-hats` proxy — the
common `direnv`-prepend vector is closed structurally, not policed at runtime.
`python -m ai_hats` (via `src/ai_hats/__main__.py` → `main_entry`) becomes the
**sole** package entry. The launcher execs `bin/python -m ai_hats`, and every
venv-usability probe switches from "is `bin/ai-hats` executable?" to
"`bin/python -c "import ai_hats"`" — importability is the real signal a package
installed cleanly [1].

The host launcher is *still named* `ai-hats` and still on `$PATH` — only the
**per-venv generated binary** is gone. There is exactly one `bin/ai-hats` in the
world worth invoking: the HOST launcher at `~/.local/bin/ai-hats`
(`AI_HATS_LAUNCHER_DEST`). No `<venv>/bin/ai-hats` console script exists anywhere.

### П2 — Refuse-and-instruct backstop for the residual case (Alt 3, HATS-791)

Removing the generator closes shadows born from the console script. It does not
cover someone running `python -m ai_hats` directly from a foreign venv (e.g. a
project app-venv with an old ai-hats still installed). For that residual case,
a runtime guard: `ai_hats.self_location.classify_invocation` is a **pure**
function returning `"sanctioned"` or `"foreign"`; `_guard_self_location` in
`ai_hats.cli` wires it into `main_entry` (the real-invocation path) and, on a
`"foreign"` verdict, prints `remediation_text` to stderr and exits **3**.

Design constraints that make this safe to ship:

- **Defense-in-depth, not a primary gate.** The shadow generator is already
  gone (П1). A *missed* foreign venv merely reproduces pre-guard behaviour; a
  *false positive* would brick a perfectly good CLI. So `classify_invocation`
  biases HARD toward fail-open: it returns `"foreign"` only when positively
  certain the running interpreter is a real venv that is **none** of the
  sanctioned shapes. Every ambiguity (no project, unresolved venv, any path
  error, editable host clone) resolves to `"sanctioned"`.
- **Scoped to an existing managed venv.** The guard only refuses when the venv
  ai-hats *would* resolve for this project **actually exists** on disk
  (`resolved_path.exists()`). If there is no managed venv to shadow, there is
  nothing to protect — fail open. This clears false positives for standalone /
  by-name installs in projects with no managed venv [2].
- **Info commands are exempt.** `--version`, `--help`/`-h`, `--tree`, and a bare
  `ai-hats` resolve no project state — a shadow printing its version harms
  nothing — so `_is_guard_exempt_invocation` skips them.
- **Wired into `main_entry`, NOT the `main` click group.** In-process
  `CliRunner` tests drive `main` directly and so bypass the guard for free; the
  suite never risks tripping it.
- **Operator escape hatch.** `AI_HATS_SKIP_SELF_LOCATION_GUARD=1`
  (`SKIP_ENV_VAR`) unconditionally returns `"sanctioned"`.

`remediation_text` names the three recovery paths: run the host launcher
(`~/.local/bin/ai-hats`), re-bootstrap out-of-band, or uninstall ai-hats from
the offending venv.

### П3 — Out-of-band recovery that is paradox-immune (HATS-791)

When a managed venv is broken badly enough (deleted site-packages, dangling
interpreter, a foreign shadow), in-band `ai-hats self update` cannot heal
itself — it runs *from* the thing it must fix. `scripts/bootstrap.sh` is the
**out-of-band recovery hatch**, and it is paradox-immune by two properties:

1. **Always fetched fresh** — `curl … bootstrap.sh | bash`. The recovery script
   is never the stale on-disk copy.
2. **Drives the launcher by ABSOLUTE path** — `"$LAUNCHER_DEST"`, never the
   on-`$PATH` `ai-hats`. A stray shadow cannot intercept an absolute-path
   invocation (see П5). This is the **absolute-path-immunity** insight: PATH
   resolution is the entire attack surface, and an absolute path does not
   consult PATH.

The dedicated entrypoint is the **`--repair`** flag: it force-reinstalls the
launcher, removes the framework-managed default venv (`.agent/ai-hats/.venv` +
`versions/`) — and *only* that, never a user-owned `AI_HATS_VENV` override —
then re-runs `"$LAUNCHER_DEST" self update` to rebuild from scratch. Idempotent.

`bootstrap.sh` also runs a **stray-shadow detector** (`detect_stray_launchers`,
the bash twin of `ai_hats.cli.maintenance.find_stray_launchers`) on every run:
it scans `$PATH` for `ai-hats` executables outside the sanctioned launcher and
WARNs. It **never deletes** (destructive-actions rule) — it instructs. The bash
twin exists so detection works even when the managed venv (and thus the python
detector) is unrunnable.

### П4 — Config: preserve unknowns, fail loud on a newer schema (HATS-792)

Two complementary rules in `ai_hats.models.ProjectConfig`, keyed off
`KNOWN_SCHEMA_VERSION` (currently 4 — the highest `schema_version` this binary
understands):

- **Same-version unknown fields are preserved, not dropped.** `from_yaml`
  pre-strips unknown top-level keys before `extra="forbid"` validation (so an
  older binary survives a field a newer one added without a schema bump), but it
  now *returns* the stripped `{key: value}` map and stashes it on an `_extra`
  `PrivateAttr`. `to_dict` merges `_extra` back, so read→write **round-trips**
  the unknown field instead of deleting it. The HATS-581 stderr WARN still fires
  — the vanish-from-the-typed-model stays observable; what changes is the bytes
  survive a `save()`. This mirrors the `TaskCard.extras` capture-on-load /
  merge-on-dump pattern [3].
- **A genuinely newer schema fails loud.** When the on-disk `schema_version`
  exceeds `KNOWN_SCHEMA_VERSION`, `from_yaml` raises `ProjectConfigError`
  pointing at `ai-hats self update`, rather than misreading the file as v4 and
  risking a clobber on the next `save()`. A matching **clobber guard** in
  `save()` refuses to overwrite an on-disk file whose `schema_version` is newer
  than this binary knows — defending the bypass paths that construct a config
  without loading the existing file first (a fresh `ProjectConfig().save()` over
  a future file, a re-init).

Preserve covers *same-version* skew (forward-safe round-trip); fail-loud covers
*newer-schema* skew (refuse rather than corrupt). The two never overlap: a
newer schema fails loud before it can reach the preserve seam.

### П5 — The absolute-path-immunity insight

The common thread under C1 and the recovery design: **a shadow is only ever a
PATH-resolution outcome.** A stray `bin/ai-hats` wins only because bare `ai-hats`
consults `$PATH` and finds the stray first. Every layer leans on this:

- П1 removes the artifact that *gets onto* a venv's `bin/`.
- П3's `bootstrap.sh` invokes `"$LAUNCHER_DEST"` by absolute path, so no PATH
  lookup happens and no shadow can intercept the recovery.
- The guard (П2) and detector (П3) compare *resolved absolute paths*
  (`Path.resolve`) against the sanctioned launcher, so symlinks and `..` cannot
  disguise a shadow as the real thing.

This is why we did **not** try to fix the problem by reordering PATH (see
Rejected).

## Consequences

- **One entry point, one binary worth running.** `python -m ai_hats` is the
  package entry; `~/.local/bin/ai-hats` is the only `bin/ai-hats` to invoke.
  Docs may name `<venv>/bin/python` but never a `<venv>/bin/ai-hats` console
  script — it does not exist.
- **Recovery is always available out-of-band.** Any breakage that defeats
  in-band `self update` has a paradox-immune hatch: `curl … bootstrap.sh | bash
  -s -- --repair`.
- **Config is forward-safe within a schema and fail-loud across schemas.** An
  older binary preserves a newer same-version field's bytes and refuses a newer
  *schema* rather than corrupting it.
- **The guard is deliberately leaky.** It is a backstop, not a wall. It will
  miss foreign venvs it cannot positively identify, by design — the alternative
  (a false positive bricking the CLI) is strictly worse now that the generator
  is gone.

## Alternatives considered

- **Alt 1 — re-exec into the resolved venv (REJECTED).** Have the shadow detect
  it is foreign and `os.execv` into the correct managed `python`. Rejected: it
  re-introduces a runtime "find and jump to the right interpreter" dance (the
  exact `_maybe_reexec_into_local_venv` python wrapper HATS-337 deleted in favour
  of the single bash launcher), doubles process startup, and a *wrong* re-exec
  target is far harder to diagnose than a refuse-and-instruct. The launcher
  already owns venv selection; a second selector in the package fights it.
- **Alt 2 — ship a shell function instead of a launcher (REJECTED).** Define
  `ai-hats()` in the user's rc so a function always shadows any `bin/ai-hats` on
  PATH. Rejected: rc-only state is invisible, per-shell, and breaks
  non-interactive / sub-process invocations (hooks, `ai-hats … | ai-hats …`)
  that never source the rc. It trades one PATH-ordering fragility for a worse,
  shell-specific one.
- **Naive fetch-on-every-invocation (REJECTED).** Make `ai-hats` always
  `curl | bash` the latest bootstrap so it is never stale. Rejected: network on
  every command, offline-hostile, and it makes the tool's behaviour depend on a
  remote at the worst possible moment (mid-task). Fetch-fresh is right for the
  *recovery* hatch (rare, deliberate), wrong for the *hot* path.
- **rc PATH-ordering ("just put `~/.local/bin` first") (REJECTED).** Document
  that users must ensure the host launcher precedes any venv `bin/` on `$PATH`.
  Rejected: it pushes a global invariant onto every user's shell config,
  `direnv` re-prepends per-directory after the fact, and it does nothing for the
  `python -m ai_hats`-from-a-foreign-venv case. П5 shows PATH order is the
  *attack surface*, not a knob to settle the fight on.
- **Config: `extra="ignore"` (silently drop unknowns) (REJECTED for C2).** The
  pre-HATS-792 behaviour. Drops a newer binary's same-version field on the next
  `save()` — silent forward data loss. Replaced by preserve-on-`_extra`.
- **Config: treat a newer `schema_version` as v4 (REJECTED).** Misreads the
  file's actual (unknown) format AND risks clobbering future fields on `save()`.
  Replaced by fail-loud + the `save()` clobber guard.

## References

- **[1]** — [`CHANGELOG.md`](../../CHANGELOG.md) — the `[Unreleased]` HATS-790 /
  HATS-791 / HATS-792 entries.
- **[2]** — [`src/ai_hats/self_location.py`](../../src/ai_hats/self_location.py)
  — `classify_invocation`, `SKIP_ENV_VAR`, `remediation_text`; the pure-function
  truth table. Wiring lives in
  [`src/ai_hats/cli/__init__.py`](../../src/ai_hats/cli/__init__.py)
  (`_guard_self_location` / `main_entry`).
- **[3]** — [`src/ai_hats/models.py`](../../src/ai_hats/models.py) —
  `KNOWN_SCHEMA_VERSION`, `ProjectConfig._extra` round-trip, the `from_yaml`
  fail-loud + `save()` clobber guard.
- [`scripts/bootstrap.sh`](../../scripts/bootstrap.sh) — out-of-band recovery
  hatch, `--repair`, `detect_stray_launchers`.
- [`scripts/ai-hats-launcher`](../../scripts/ai-hats-launcher) — host launcher;
  execs `bin/python -m ai_hats`, no `bin/ai-hats` console script.
- [`docs/glossary.md`](../glossary.md) — **self-location guard**, **managed venv
  / managed-venv invariant**, **stray shadow**, **out-of-band recovery**.
- ADR-0006 [`docs/adr/0006-worktree-concurrency-layered-defense.md`](0006-worktree-concurrency-layered-defense.md)
  — sibling "layered defense" pattern (each layer justifies the surface the
  previous left).
