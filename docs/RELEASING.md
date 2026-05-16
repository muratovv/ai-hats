# Releasing ai-hats

Maintainer-facing guide to cutting a release. The mechanics rely on
[`setuptools-scm`](https://github.com/pypa/setuptools_scm/) (version
from git tags), [Conventional Commits](https://www.conventionalcommits.org/)
(commit log structure), and [Keep a Changelog](https://keepachangelog.com)
(human-readable release notes).

## SemVer policy

ai-hats follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
with one pre-1.0 caveat documented below.

### What is the public API?

**Stable surface — SemVer-protected:**

- The `ai-hats` CLI: top-level groups, command names, and documented
  flags. `ai-hats --tree` is the canonical inventory of the surface.
- `ai-hats.yaml` schema. The top-level `schema_version` field tracks
  breaking shape changes; migrations live in `docs/migration*.md`.
- Tracker on-disk format: the `tracker/backlog/tasks/HATS-NNN/` layout,
  task-card YAML schema, `STATE.md` format.
- The skill / rule / trait file format documented in
  [ARCHITECTURE.md](ARCHITECTURE.md).

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

For now, a release ships exactly two things:

1. **A git tag** (annotated, e.g. `v0.4.0`) — `setuptools-scm` reads
   this and stamps `src/ai_hats/_version.py` accordingly.
2. **A GitHub Release** — body is the matching CHANGELOG.md section,
   verbatim.

PyPI publication is intentionally deferred. The install path remains
`pip install 'ai-hats @ git+https://github.com/muratovv/ai-hats'`.
Once the API surface is stable enough for SemVer to mean something to
downstream packagers (post-1.0), we add a PyPI publish step.

## CHANGELOG flow

[`CHANGELOG.md`](../CHANGELOG.md) follows Keep a Changelog 1.1.0.

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

```bash
git fetch --tags
git describe --tags --exact-match    # → v0.4.0

# Install in a throwaway venv and confirm the version
python3 -m venv /tmp/release-verify
/tmp/release-verify/bin/pip install 'ai-hats @ git+https://github.com/muratovv/ai-hats@v0.4.0'
/tmp/release-verify/bin/ai-hats --version    # → ai-hats 0.4.0
rm -rf /tmp/release-verify
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

- **PyPI publication** — deferred to post-1.0.
- **Automated release-notes generation** — manual `awk` extraction is
  fine for the current cadence.
- **CI-driven release** — a GitHub Actions workflow that triggers on
  tag and attaches built wheels lands in HATS-345 (CI infrastructure).
