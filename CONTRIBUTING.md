# Contributing to ai-hats

Thanks for taking the time to look at ai-hats. This guide covers the
practical bits — dev setup, branch and commit conventions, what to test,
and what **not** to commit.

For the architectural overview see [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
For how the framework's reflection loop works see
[docs/how-to-feedback-loop.md](docs/how-to-feedback-loop.md).

## Development setup

```bash
git clone git@github.com:muratovv/ai-hats.git && cd ai-hats
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest tests/ -v
```

Requirements:

- Python 3.11+
- A POSIX shell (the launcher and pre-commit hooks are bash scripts).
- `ruff` for linting (installed via `[dev]` extra).

## Branches and commits

- Branch names: `task/hats-NNN` for tracked work, `fix/<slug>` and
  `feat/<slug>` for ad-hoc work, `docs/<slug>` for docs-only changes.
- Commits follow **Conventional Commits**:
  `<type>(<scope>): <short subject>`. Common types: `feat`, `fix`,
  `docs`, `refactor`, `test`, `chore`, `security`.
- Reference a backlog ID in the subject when applicable, e.g.
  `feat(launcher): venv-first wrapper (HATS-336)`.
- Keep commits focused — one logical change per commit. Squash WIP
  commits before opening a PR.

## Testing

- `pytest tests/` — the full suite (unit + integration).
- `pytest tests/ -m smoke` — quick smoke gate used by the pre-commit hook.
- `pytest tests/ -m integration` — slower tests that spawn real
  subprocesses or use a real PTY.

A change is **not done** until:

1. Tests pass locally.
2. `ruff check .` is clean.
3. The privacy pre-commit hook (`.githooks/pre-commit`) lets the commit
   through (it ships configured automatically via `core.hooksPath`).

## Library structure: core vs usage

Built-in content (roles, traits, rules, skills, pipelines) lives at the repo
root in `library/`, split into two layers shipped together inside the
`ai_hats.library` Python sub-package:

- **`library/core/`** — engine fundament. System roles (`session-reviewer`,
  `auditor-for-role`, …), base traits (`trait-base`, `trait-agent`,
  `trait-analyst-base`, `base-judge`, `base-auditor`,
  `trait-reflect-mode`), global rules, foundational skills
  (`backlog-manager`, `git-mastery`, `review-*`, `judge-*`, …), all
  pipelines + initial injections, and the provider scaffold template.
  Without these the engine cannot bootstrap or run reflect pipelines.
- **`library/usage/`** — curated content catalog. Opinionated roles
  (`assistant`, `architect`, `sre`, `go-dev`, …), domain traits
  (`trait-se-mindset`, `dev::*`, `env::*`), opt-in skills (golang stack,
  terraform, observability, system-design, …).

**Where does a new component go?** Decide by:

1. Is it referenced by name in `src/ai_hats/` code, or pulled in transitively
   by a core trait (`trait-base`, `trait-agent`, `trait-analyst-base`,
   `base-judge`, `base-auditor`, `trait-reflect-mode`)? → **core**.
2. Does removing it break `ai-hats init` / `ai-hats self bump` / a reflect
   pipeline? → **core**.
3. Otherwise — **usage**.

For end-user docs on extending the library (worked examples for roles /
traits / rules / skills, override precedence, replacing a system role) see
[docs/how-to-extend.md](docs/how-to-extend.md).

## What **not** to commit

The privacy pre-commit hook blocks most of these automatically, but it is
your responsibility to keep the repo clean. Particularly:

- **Real Claude / Gemini session recordings** — JSONL dumps from
  `claude-code` or `gemini-cli` typically carry your `sessionId`,
  `requestId`, `cwd` with your home directory, subscription tier markers,
  and unredacted user prompts. These are personal data. If you need a
  fixture, generate a synthetic one (a few rows of representative shape
  is enough). See `tests/fixtures/real_session/` for the synthetic
  pattern.
- **`/cost` or quota output** captured during a session.
- **Absolute paths from your machine** (`/Users/<name>/...`,
  `/home/<name>/...`). Use relative paths or `~`.
- **Personal config dumps** — `ai-hats.yaml` exports that include your
  customizations or personal task-prefix tweaks.
- **API keys, bearer tokens, `.env` files** — the hook will block these
  outright. If a false positive blocks a legitimate commit, use
  `AI_HATS_PRIVACY_ACK=1 git commit ...` for that single invocation and
  explain in the commit body why the override is safe.
- **Binary fixtures larger than ~5 KB under `tests/fixtures/`** — the
  hook flags these as soft warnings. Synthetic fixtures should fit in
  under a kilobyte.

If you have committed something sensitive by accident, contact the
maintainer (see [SECURITY.md](SECURITY.md)) — there is a documented
filter-repo procedure for purging the history.

## Docs and naming

[`docs/glossary.md`](docs/glossary.md) is the naming source-of-truth for core concepts — **role**, **session**, **reflect**, **backlog** (task / HYP / PROP), **worktree**, **artifacts** and friends. When you write or edit a doc:

- Link to the glossary entry instead of redefining a core term.
- If a term is genuinely new (not yet in the glossary), update the glossary first, then reference it.
- For cross-doc / cross-file / fixture links use the numbered-refs convention — see [Documentation references](#documentation-references) below for the full rules.

## Diagrams

Architecture diagrams live in `docs/assets/diagrams/` and are written
in [d2](https://d2lang.com/) (`.d2` source) and rendered to `.svg`.
The renderer is `docs/assets/diagrams/render.sh`.

### Why d2 and not mermaid

Mermaid renders inline on GitHub but produces wide, low-contrast
output with no real control over typography. We switched to d2 + sketch
mode for: hand-drawn aesthetic, brand-tinted palette, predictable
width, and Source Code Pro everywhere.

### Toolchain

- **d2** — `brew install d2` (or any package manager / GitHub release).
- **Source Code Pro variable TTFs** in `~/Library/Fonts/`. Grab the
  two `[wght].ttf` files (regular + italic) from
  [adobe-fonts/source-code-pro releases](https://github.com/adobe-fonts/source-code-pro/releases).
- **Python 3** (system). `render.sh` provisions `fonttools` in a tmp
  venv on first run to extract Medium/SemiBold/Medium-Italic static
  weights from the variable font.

### Render workflow

```bash
# Render everything in docs/assets/diagrams/
bash docs/assets/diagrams/render.sh

# Render one diagram by stem
bash docs/assets/diagrams/render.sh session-lifecycle
```

`render.sh` calls `d2 --sketch --pad=20` with the three font slots
wired to the extracted Source Code Pro weights. The `_palette.d2`
partial is skipped automatically (underscore-prefixed files are shared
imports, not standalone diagrams).

### Palette

The brand palette (`docs/assets/diagrams/_palette.d2`) is imported by
every diagram:

```d2
vars: @./_palette
```

Slot mapping (16 colors, derived from the project logo's navy/orange/cream):

| Slot | Hex | Used for |
|---|---|---|
| N7 | `#faf2e6` | Paper/canvas background |
| N1 → N3 | navy shades | Bold text → italic edge labels |
| B1 → B6 | navy spectrum | Box fills, arrow strokes |
| AA4 | `#e8632b` | Brand orange — storage shapes (cylinders, ovals) |
| AA2 / AA5 | orange shades | Subdued / dark variants |
| AB4 / AB5 | cream/beige | Soft accent shapes (parallelograms) |

See `docs/assets/diagrams/PALETTES.md` for the full preview of
alternative palettes (dracula, tokyo-night, nord, gruvbox) on the same
diagram, plus instructions for building your own.

### Adding a new diagram

1. Create `docs/assets/diagrams/<name>.d2`. First line is:
   ```d2
   vars: @./_palette
   ```
2. Pick `direction: down` for vertical flows (sessions, pipelines) or
   `direction: right` for state machines with a left-to-right happy
   path.
3. Use the conventional shapes:
   - `shape: cylinder` — storage / persistent data
   - `shape: diamond` — decision / branch
   - `shape: oval` — terminal states (FSM accepts), `end`
   - `shape: document` — file artifact (with corner fold)
   - `shape: parallelogram` — handoff / continuation to another flow
   - `shape: person` — user actor
   - `shape: callout` — sticky-note style annotation
4. Run `bash docs/assets/diagrams/render.sh <name>`.
5. Embed in the relevant markdown with an HTML `<img>` for width control:
   ```html
   <p align="center">
     <img src="assets/diagrams/<name>.svg" alt="..." width="520">
   </p>
   ```
   Width `420-640px` reads well on both desktop and mobile.
6. Commit both `<name>.d2` (source of truth) and `<name>.svg`
   (rendered artifact).

### Multiline labels

d2 quoted strings interpret `\n` and `\` for line wrap. Easiest path
for callouts and multi-line node labels is the markdown literal:

```d2
note: |md
  any state ->\
  cancelled
| {shape: callout}
```

### Don't

- Don't commit `.png` exports — `.svg` is the GitHub-friendly source.
  PNGs are large and don't scale.
- Don't inline mermaid in a doc that already has d2 diagrams nearby —
  mixed styles look inconsistent.
- Don't rebuild `render.sh`'s font cache by hand. If it ever gets
  stale (e.g. you upgrade Source Code Pro), delete
  `$TMPDIR/ai-hats-fonts/` and rerun the script.

## Documentation references

User-facing docs (`README.md`, `docs/*.md`, `CONTRIBUTING.md`) use one of three link styles depending on the kind of target. Pick by **what** is being linked, not by **where** the link appears.

| Link kind | Style |
|---|---|
| Cross-doc, cross-file, or fixture path | **numbered** — `[N]` in body + a visible `## References` section at the end of the file |
| In-doc section anchor (`#some-section`) | **inline** `[text](#anchor)` |
| CLI command, env var, file path-as-string | **inline code** — `` `ai-hats task ...` ``, `` `<ai_hats_dir>/...` `` |

**Rationale.** Cross-doc links earn a citation slot at the bottom because they survive doc reshuffles, declutter the body, and form a quick "what does this doc depend on" index. Anchors and CLI strings stay inline because they're navigation / identifiers, not citations.

**Numbered-refs format** (locked by HATS-358):

- One **visible** `## References` section at the end of the file. Entries look like:
  ```markdown
  **[1]** — [`docs/glossary.md`](glossary.md) — naming source-of-truth for core terms.
  ```
- **No** `[N]: url` link-reference definitions at the bottom — they duplicate the URL. `[N]` in body is a plain pointer to the numbered list, not a clickable shortcut.
- Numbering is **per-file** (resets to `[1]` each doc), ordered by first appearance in body.

**Worked example.**

```markdown
The cycle has two parts: auto reflect-session (per session) feeds the HYP log
and PROP inbox; manual reflect-all (user-initiated) triages the accumulated
backlog. Architecture reference — [1]. Sample artifacts — [2], [3]. See also
the [Hypothesis section](#hypotheses) below.

Run `ai-hats reflect all` to start the triage.

## References

**[1]** — [`docs/reflect.md`](reflect.md) — pipeline architecture, schema dispatch.

**[2]** — [`tests/fixtures/real_backlog/HYP-001-sample.yaml`](../tests/fixtures/real_backlog/HYP-001-sample.yaml) — synthetic hypothesis fixture.

**[3]** — [`tests/fixtures/real_backlog/PROP-001-sample.yaml`](../tests/fixtures/real_backlog/PROP-001-sample.yaml) — synthetic proposal fixture.
```

Reference docs already on this style: [`docs/how-to-backlog.md`](docs/how-to-backlog.md), [`docs/how-to-advanced.md`](docs/how-to-advanced.md), [`docs/how-to-feedback-loop.md`](docs/how-to-feedback-loop.md).

## Pull requests

- Open the PR against `master`.
- Use the PR template — describe the change, link the backlog ID,
  include a test plan.
- The reviewer is typically the maintainer. Smaller docs / typo PRs are
  usually merged within a day; substantive changes go through the
  standard task state machine (`brainstorm → plan → execute → document
  → review → done`).
- After merge, the source branch is deleted. The maintainer handles
  release tagging.

## Reporting bugs and proposing features

- Bugs: use the **Bug report** issue template.
- Features and ideas: use the **Feature request** issue template, or
  open a GitHub Discussion if it is still half-baked.
- Security issues: do **not** open a public issue. See
  [SECURITY.md](SECURITY.md).

## Releases

See [docs/RELEASING.md](docs/RELEASING.md) for the SemVer policy, the
breaking-change protocol, and the manual release checklist. Short
version: bump per SemVer, roll up `CHANGELOG.md`, push an annotated
tag, create a GitHub Release with the matching CHANGELOG section as
the body.

## License

By contributing, you agree that your contributions will be licensed
under the [MIT License](LICENSE).
