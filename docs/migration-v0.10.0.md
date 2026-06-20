# Migration: v0.9.0 → v0.10.0

`v0.10.0` is a MAJOR bump under the pre-1.0 `0.MAJOR.MINOR` scheme. It closes the
**bootstrap paradox** (HATS-786): ai-hats could not repair bugs in its own
invocation-resolution / config-read path by shipping a newer version, because the
broken old code is exactly what ran (a stale `ai-hats` shadowing the host
launcher) or what had already read the config. The fix is structural: the
shadow-generating console script is removed, a runtime guard refuses foreign-venv
invocations, `scripts/bootstrap.sh` becomes a paradox-immune recovery path, and
the `ai-hats.yaml` reader is made forward-safe.

Managed (launcher-driven) projects keep working — the host launcher is still
named `ai-hats` on `$PATH` and the CLI surface is unchanged. But the *removal* of
the per-venv console script is a breaking distribution change, and existing
machines need a **one-time crossover** (§1, §3) because no forward release can
reach the old code already installed.

This doc is the migration reference required by the breaking-change protocol [1]
and is linked from the `Removed` entry in the changelog [2].

> **Deprecation note.** The breaking-change protocol's "deprecate for one MINOR
> first" step was intentionally skipped for the console-script removal: the
> console script *generating* a shadowable `bin/ai-hats` IS the bug, so a
> deprecation period that kept emitting the binary would keep reproducing the
> failure. The removal is immediate; this doc is the mitigation.

## 1. The `ai-hats` console-script entry point was removed

**What changed (HATS-790).** The `[project.scripts] ai-hats` generator is gone,
so **no venv ever materialises `<venv>/bin/ai-hats`** again. Previously, any
project that listed `ai-hats` as a dependency got pip to create that binary, and
a direnv-activated app venv could prepend it ahead of the host launcher
(`~/.local/bin/ai-hats`) — silently running stale code that bypassed
channel/managed-venv resolution. The package is now invoked as `python -m
ai_hats`; the host launcher execs `<venv>/bin/python -m ai_hats "$@"`.

**Why.** Removing the *generator* is the only fix that stops the shadow from
being **born**: from this version on, depending on ai-hats anywhere cannot mint a
competing `ai-hats` command.

**Migrate.**

- **Reinstall the host launcher once** so `~/.local/bin/ai-hats` becomes the new
  `python -m`-based launcher (the old one still looks for the removed
  `bin/ai-hats` and fails loud):

  ```bash
  bash scripts/install-launcher.sh
  # or, from anywhere:  curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/install-launcher.sh | bash
  ```

- **Never list `ai-hats` as a project/app dependency.** It is a host tool, not a
  library. If a project's app venv has it, remove it (see §3).
- For direct, launcher-free invocation use `python -m ai_hats` (e.g.
  `<venv>/bin/python -m ai_hats --version`). `pip install ai-hats; ai-hats …` no
  longer yields an `ai-hats` command inside that venv — by design.

## 2. ai-hats may refuse to run from a foreign (non-managed) venv

**What changed (HATS-791).** A runtime **self-location guard** refuses, with
remediation, when ai-hats is invoked from a venv that is **not** the one it
resolves for the project *and* a real managed venv exists to be shadowed. It
exits non-zero (3) and prints how to recover. It biases hard toward fail-open —
editable dev checkouts, `--version`/`--help`/`--tree`, and any project with no
managed venv are all allowed.

**Why.** A backstop for the residual case after §1: someone running `python -m
ai_hats` directly from a stale foreign venv.

**Migrate.** If you hit `refusing to run from a foreign (non-managed)
virtualenv`, do one of: run the host launcher (`~/.local/bin/ai-hats <cmd>`),
re-bootstrap (§3), or uninstall ai-hats from that venv. To bypass the guard
deliberately (advanced / CI):

```bash
AI_HATS_SKIP_SELF_LOCATION_GUARD=1 <venv>/bin/python -m ai_hats <cmd>
```

## 3. Out-of-band recovery + clearing stray shadows

**What changed (HATS-791).** `scripts/bootstrap.sh` is now the canonical
**out-of-band recovery** path — it is fetched fresh and drives the launcher by
absolute path, so a shadow cannot intercept it. A new `--repair` flag
force-reinstalls the launcher + the framework-managed venv. Both `bootstrap.sh`
and `ai-hats config status` scan `$PATH` for stray `ai-hats` binaries outside the
sanctioned launcher and **warn** (never delete).

**Migrate (the one-time crossover for an existing project).**

```bash
# 1. Repair the host launcher + managed venv (paradox-immune, out-of-band):
curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh | bash -s -- --repair

# 2. If a stray ai-hats lives in a project app venv, remove it (the detector
#    points at it; it is never auto-deleted):
uv pip uninstall --python <app-venv>/bin/python ai-hats

# 3. Update the project to the new version:
ai-hats self update
```

## 4. Forward-safe `ai-hats.yaml` reader

**What changed (HATS-792).** An unknown *top-level* field written by a newer
ai-hats is now **preserved** on round-trip instead of being silently dropped on
the next `save()` (the stderr WARN still fires). A genuinely newer
`schema_version` (greater than this binary's `KNOWN_SCHEMA_VERSION`) now **fails
loud** — `ai-hats` refuses to operate or overwrite and points at `ai-hats self
update` — rather than silently misreading the file.

**Why.** Where `ai-hats.yaml` is committed (e.g. tracked in dotfiles), an older
binary touching a config a newer binary wrote must not silently lose fields or
clobber a future schema.

**Migrate.** Nothing to do. If you see `schema_version N is newer than this
ai-hats`, run `ai-hats self update` to get a binary that understands the file.

## References

**[1]** — [`docs/RELEASING.md`](RELEASING.md) — SemVer policy and the
breaking-change protocol (deprecation, migration doc, changelog reference).

**[2]** — [`CHANGELOG.md`](../CHANGELOG.md) — the `[0.10.0]` section; its
`Removed` entry references this doc.

**[3]** — [`docs/adr/0010-bootstrap-paradox-self-location-and-forward-safe-config.md`](adr/0010-bootstrap-paradox-self-location-and-forward-safe-config.md)
— the decision record: C1/C2 framing, the layered fix, and rejected alternatives.

**[4]** — [`docs/glossary.md`](glossary.md) — *self-location guard*, *stray
shadow*, *out-of-band recovery*, *managed-venv invariant*.
