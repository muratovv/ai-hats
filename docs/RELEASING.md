# Releasing ai-hats

Maintainer-facing guide to cutting a release. The mechanics rely on
[`setuptools-scm`](https://github.com/pypa/setuptools_scm/) (version
from git tags), [Conventional Commits](https://www.conventionalcommits.org/)
(commit log structure), [Keep a Changelog](https://keepachangelog.com)
(human-readable release notes), and
[PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/)
(tokenless OIDC publish from CI).

## SemVer policy

ai-hats follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with one pre-1.0 caveat documented below.

### What is the public API?

**Stable surface — SemVer-protected:**

- The `ai-hats` CLI: top-level groups, command names, and documented
  flags. `ai-hats --tree` is the canonical inventory of the surface.
- `ai-hats.yaml` schema. The top-level `schema_version` field tracks
  breaking shape changes; migrations live in `docs/migration*.md`. The
  `migration_step` field (HATS-471) is NOT part of the schema contract
  — it is an internal counter for one-shot side-effect replay, freely
  bumped by additive registry entries without a SemVer signal.
- Tracker on-disk format: the `tracker/backlog/tasks/HATS-NNN/` layout,
  task-card YAML schema, `STATE.md` format.
- The skill / rule / trait file format documented in [1].

**Unstable surface — no SemVer guarantee until 1.0:**

- The Python API (`from ai_hats import ...`). Internal refactors land
  freely.
- Exact contents of `<ai_hats_dir>/sessions/*` artefacts beyond the
  documented `metrics.json` keys.
- Default values inside the library landing roles — additive changes
  can shift behaviour subtly.

### Pre-1.0 caveat

Until `v1.0.0`, the version format is `0.MAJOR.MINOR`:

- **MAJOR bump** (`0.X.0` → `0.(X+1).0`) — at least one breaking change
  to the stable surface. A migration doc is mandatory.
- **MINOR bump** (`0.X.Y` → `0.X.(Y+1)`) — additive or fix-only.
- The PATCH level (`0.X.Y.Z`) is not used in 0.x — bug fixes ship as
  MINOR bumps so contributors don't have to guess.

This caveat ends at `v1.0.0`. From then on, MAJOR.MINOR.PATCH applies
in the standard SemVer way.

### Breaking-change protocol

1. **Mark the deprecation.** Emit a runtime warning (`warnings.warn(...,
   DeprecationWarning)`) for at least one MINOR release before removal.
2. **Add a migration doc** under `docs/migration-<topic>.md`. The doc
   names: what changed, why, the user-facing migration step, and the
   timeline (which release deprecated, which one removes).
3. **CHANGELOG entry** under *Changed* or *Removed* must reference the
   migration doc and use the explicit `Migration:` prefix so readers
   can `grep`.

## Release artefacts

A release ships three things:

1. **A git tag** (annotated, e.g. `v0.4.0`) — `setuptools-scm` reads
   this and stamps `src/ai_hats/_version.py` accordingly. Pushing the
   tag is also what triggers the PyPI publish.
2. **A PyPI release** (wheel + sdist) — built and published
   automatically by the `release.yml` workflow [3] on the tag push, via
   OIDC trusted publishing (no stored API token). This is the artefact
   the `stable` channel installs (`ai-hats==<version>`).
3. **A GitHub Release** — body is the matching CHANGELOG.md section,
   verbatim. Created by hand after the tag (step 4) so the notes stay
   human-curated.

## PyPI trusted publishing

The `release.yml` workflow [3] publishes to PyPI without a stored API
token — it authenticates via OIDC ("trusted publishing"). Build and
publish are split into two jobs so the `id-token: write` privilege is
held only by a job that does nothing but download the prebuilt dist and
upload it (least privilege).

### One-time setup

Configured once on the PyPI side, scoped to a GitHub deployment
environment. Repeat only when the repo or workflow filename changes.

1. **Reserve the name + add a pending publisher.** On PyPI →
   *Account settings → Publishing → Add a new pending publisher*. This
   both claims the `ai-hats` name and binds the OIDC trust. Set exactly:

   | Field             | Value         |
   | ----------------- | ------------- |
   | PyPI Project Name | `ai-hats`     |
   | Owner             | `muratovv`    |
   | Repository name   | `ai-hats`     |
   | Workflow name     | `release.yml` |
   | Environment name  | `pypi`        |

   These four binding values must match the workflow verbatim — any
   mismatch fails the publish loud.
2. **Create the GitHub `pypi` environment:**

   ```bash
   gh api -X PUT repos/muratovv/ai-hats/environments/pypi
   ```

   The workflow's `publish` job runs in this environment; the pending
   publisher above is scoped to it.
3. **Optional hardening** — add a required reviewer to the `pypi`
   environment (repo *Settings → Environments → pypi*) to gate each
   publish behind a manual approval. Off by default: the tag push
   publishes unattended.

### Workspace packages (`ai-hats-core`, `ai-hats-wt`)

The workspace packages under `packages/*` carry their own **static** versions
(`packages/<pkg>/pyproject.toml`), decoupled from the `ai-hats` `v*` tag. They
publish through a separate workflow,
[`release-packages.yml`](../.github/workflows/release-packages.yml) — build both
with `uv build`, then a per-package OIDC publish **job** each (core first, then
the wt package that depends on it). It runs on **manual dispatch** and
**auto-triggers** on a push to master that touches `packages/*/pyproject.toml`
(HATS-943 — bump⇒publish is one step; `skip-existing` no-ops an unchanged
version). A final `verify-remote-install` job then does a fresh-venv
`git+https` install and imports `ai_hats_core.migrations`, so a version-skewed
release fails loud instead of shipping a DOA remote channel.

**Distinct environment per package — required, not cosmetic.** PyPI refuses two
*pending* trusted publishers that share one `(owner, repository, workflow,
environment)` tuple ("*a pending trusted publisher matching this configuration
has already been registered for a different project name*"). The environment is
the disambiguator, so each package's publish job runs in its own environment
(`pypi-core` / `pypi-wt`) — separate from the `pypi` environment the main
`ai-hats` release uses.

**One-time setup:**

1. Create the two GitHub environments:

   ```bash
   gh api -X PUT repos/muratovv/ai-hats/environments/pypi-core
   gh api -X PUT repos/muratovv/ai-hats/environments/pypi-wt
   ```

2. Add a pending publisher for **each** package (*PyPI → Account settings →
   Publishing → Add a new pending publisher*):

   | Field             | `ai-hats-core`         | `ai-hats-wt`           |
   | ----------------- | ---------------------- | ---------------------- |
   | PyPI Project Name | `ai-hats-core`         | `ai-hats-wt`           |
   | Owner             | `muratovv`             | `muratovv`             |
   | Repository name   | `ai-hats`              | `ai-hats`              |
   | Workflow name     | `release-packages.yml` | `release-packages.yml` |
   | Environment name  | `pypi-core`            | `pypi-wt`              |

**To cut a package release:** bump the version in the package's `pyproject.toml`
and merge to master — the push auto-triggers the publish (or run it manually via
*Actions → release-packages → Run workflow*). PyPI rejects re-uploading an
existing version, so a re-run without a version bump fails loud (no accidental
clobber). The integrator `ai-hats` then pins the new versions in the root
`dependencies`.

**The `version-skew-guard` CI job enforces the bump:** any change to
`packages/<pkg>/src/**` whose version is not strictly above the published PyPI
version fails CI (`scripts/check_pkg_version_skew.py`). This is the barrier that
would have caught HATS-923 / HATS-937 — a `core` module added without a version
bump, so the remote channel resolved a stale wheel (`ModuleNotFoundError`).

## CHANGELOG flow

The changelog [2] follows Keep a Changelog 1.1.0.

- Every PR with user-visible impact updates the **Unreleased** section
  under one of: *Added*, *Changed*, *Deprecated*, *Removed*, *Fixed*,
  *Security*.
- Pure refactors, internal tests, and CI-only changes do **not** require
  CHANGELOG entries.
- A PR that introduces a deprecation marks the entry as **Changed** and
  references the migration doc.

## Release checklist

End-to-end, this takes about ten minutes once the changelog is current.

### 1. Pre-flight

```bash
git fetch
git status      # working tree must be clean
git log origin/master..HEAD --oneline   # must be empty
```

### 2. Roll up the changelog

1. Open `CHANGELOG.md`.
2. Decide the version bump:
   - **MAJOR** if the Unreleased section has any entry under *Removed*
     or any *Changed* entry tagged `Migration:`.
   - **MINOR** otherwise.
3. Rename `## [Unreleased]` to `## [X.Y.Z] - YYYY-MM-DD` and create a
   fresh empty Unreleased above it:

   ```markdown
   ## [Unreleased]

   ## [0.4.0] - 2026-06-01
   ```

4. Update the link references at the bottom of the file:

   ```markdown
   [Unreleased]: https://github.com/muratovv/ai-hats/compare/v0.4.0...HEAD
   [0.4.0]: https://github.com/muratovv/ai-hats/compare/v0.3.0...v0.4.0
   [0.3.0]: https://github.com/muratovv/ai-hats/releases/tag/v0.3.0
   ```

5. Commit:

   ```bash
   git add CHANGELOG.md
   git commit -m "chore: prep v0.4.0 release"
   ```

### 3. Tag

Tags are **annotated** (not lightweight) so they carry a message
`setuptools-scm` and `gh release` can use:

```bash
git tag -a v0.4.0 -m "Release v0.4.0"
git push origin master
git push origin v0.4.0
```

The `git push origin v0.4.0` triggers `release.yml` [3] → `uv build` →
PyPI publish. Watch it before moving on:

```bash
gh run watch --workflow release.yml
```

### 4. Create the GitHub Release

The release body is the verbatim CHANGELOG section for this version:

```bash
# Extract the [v0.4.0] section out of CHANGELOG.md
awk '/^## \[0\.4\.0\]/{flag=1} /^## \[0\.3\.0\]/{flag=0} flag' CHANGELOG.md \
    > /tmp/release-notes.md

gh release create v0.4.0 \
    --title "v0.4.0" \
    --notes-file /tmp/release-notes.md
```

Alternative quick path (auto-fills contributors but loses the
hand-curated CHANGELOG framing):

```bash
gh release create v0.4.0 --generate-notes
```

### 5. Verify

Confirm the PyPI release resolves and installs — this is what the
`stable` channel consumes:

```bash
# 1. The release is live on the index (the stable resolver's endpoint).
curl -fsS https://pypi.org/pypi/ai-hats/json \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["info"]["version"])'   # → 0.4.0

# 2. Install the published wheel in a throwaway venv.
uv venv /tmp/release-verify
uv pip install --python /tmp/release-verify/bin/python "ai-hats==0.4.0"
# HATS-790: no bin/ai-hats console script — invoke the package as a module.
/tmp/release-verify/bin/python -m ai_hats --version    # → ai-hats 0.4.0
rm -rf /tmp/release-verify
```

A `stable`-channel project then picks it up via `ai-hats self update`
(resolves `ai-hats==<latest>` from PyPI). Finally confirm the tag and
the GitHub Release:

```bash
git fetch --tags
git describe --tags --exact-match    # → v0.4.0
```

Open `https://github.com/muratovv/ai-hats/releases/tag/v0.4.0` and
confirm the body matches the CHANGELOG section.

## Roadmap to 1.0

Pre-1.0 means the public surface above is **subject to change** — even
for users who otherwise read SemVer literally. We exit the 0.x regime
when:

- Three consecutive releases have shipped without an entry under
  *Removed* (no breaking changes in three releases).
- Every documented breaking change since `v0.3.0` has an accompanying
  `docs/migration-*.md` doc.
- There is at least one production user outside the maintainer.

At that point the next release tag is `v1.0.0` and standard SemVer
applies. There is no "release candidate" step — the work that justifies
1.0 is already on `master`.

## Out of scope

- **Automated release-notes generation** — manual `awk` extraction is
  fine for the current cadence.
- **TestPyPI staging / release candidates** — a single-shot publish on
  the tag is enough at the current cadence.
- **Attaching built wheels to the GitHub Release** — PyPI is the
  distribution channel; the GitHub Release carries notes only.

## References

**[1]** — [`docs/ARCHITECTURE.md`](ARCHITECTURE.md) — skill / rule / trait file format and the rest of the engine model.

**[2]** — [`CHANGELOG.md`](../CHANGELOG.md) — versioned release notes following Keep a Changelog 1.1.0.

**[3]** — [`.github/workflows/release.yml`](../.github/workflows/release.yml) — the tag-triggered PyPI publish workflow (OIDC trusted publishing, two-job least-privilege split).
