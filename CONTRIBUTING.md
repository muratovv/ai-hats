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
