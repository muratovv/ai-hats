# Migration: v0.13.2 → v0.14.0

`v0.14.0` is a MAJOR bump under the pre-1.0 `0.MAJOR.MINOR` scheme. It completes
the **rack cutover**: the legacy `ai-hats task` CLI is unmounted, the
`ai-hats-tracker` package that backed it is deleted, and `rack` becomes the only
backlog CLI. A leftover copy of the old CLI is pruned from your venv on upgrade.

**Your task data is not touched.** The on-disk layout
(`<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml`) is byte-for-byte the same
format rack has always read — see §3. There is no data migration step.

This doc is the migration reference required by the breaking-change protocol [1]
and is linked from the `Removed` entries in the changelog [2].

> **Deprecation-warning exemption — claimed under the pre-1.0 clause.**
> [`docs/RELEASING.md`][1] allows skipping the one-MINOR deprecation-warning
> release when every consumer of the removed surface is enumerable and already
> migrated. That claim is made here in writing, with its limits stated — see
> §5. It is not a claim that nobody anywhere calls `ai-hats task`; it is a
> claim about the consumer set this repository ships and can enumerate.

## 1. The `ai-hats task` CLI is unmounted

**What changed (HATS-1260).** Four command groups — `task`, `task hyp`,
`task proposal`, `task attach`, 28 verbs total — are gone from the `ai-hats`
surface. The standalone `ai-hats-tracker` console script carried the identical
grammar and is pruned on upgrade (§4).

**Why.** Two backlog CLIs over one store is the hazard the cutover exists to
remove: they had diverging plan-section catalogs, and only rack fires the
`edge:` handler bindings that gate transitions.

> ### ⚠️ Read this before migrating scripts
>
> `ai-hats task …` **does not fail.** An unrecognized leading word follows the
> bare-positional-prompt rule (HATS-087 for flag-shaped tokens, HATS-1202 for
> bare words), so the tokens are handed to the provider CLI as a **prompt** and
> a session starts:
>
> ```console
> $ ai-hats --dry-run task ls
> launch    claude task ls --system-prompt-file … --settings …
> ```
>
> For an interactive user this is merely surprising. For a **script, cron job
> or CI step** it is worse than an error: instead of exiting non-zero, the call
> starts an agent session and **hangs**. Grep your automation for `ai-hats task`
> before upgrading — a passing exit code is not evidence it still works.

**Migrate.** Every old verb maps onto `rack`. Forms below were verified against
the shipped `rack --help`.

### Tasks

| Before (`ai-hats …`)                      | After (`rack …`)                                                        |
| ----------------------------------------- | ----------------------------------------------------------------------- |
| `task create "T" --id ID`                 | `rack create "T" --id ID`                                               |
| `task create "T" -d "…" -p high --tag x`  | `rack create "T" --description "…" --priority high --tag x`             |
| `task create "T" --parent-task ID`        | `rack create "T" --parent ID`                                           |
| `task create "T" --depends-on ID`         | `rack create "T" --depends ID`                                          |
| `task show ID`                            | `rack context ID`                                                       |
| `task list` / `--state execute` / `--all` | `rack ls` / `rack ls --state execute` / `rack ls --all`                 |
| `task transition ID <state>`              | `rack transition ID <state>`                                            |
| `task close ID --resolution "…"`          | `rack transition ID --state done --force --reason "…" --resolution "…"` |
| `task log ID "message"`                   | `rack transition ID --log "message"`                                    |
| `task update ID --title "T"`              | `rack transition ID --set title="T"`                                    |
| `task update ID -p high --reviewer @lead` | `rack transition ID --set priority=high --set reviewer=@lead`           |
| `task update ID --add-tag x`              | `rack transition ID --append tags=x`                                    |
| `task update ID --parent-task P`          | `rack transition ID --link parent_task:P`                               |
| `task update ID --clear-parent`           | `rack transition ID --unlink parent_task:P`                             |
| `task update ID --add-depends B`          | `rack transition ID --link depends_on:B`                                |
| `task link A B --type related`            | `rack transition A --link related:B`                                    |
| `task link A B --type see-also`           | `rack transition A --link see_also:B` (underscore)                      |
| `task plan-extract ID`                    | `rack plan-extract ID`                                                  |

**Re-parenting** is one transition, not a `--set`: read the old parent first,
then `rack transition <ID> --unlink parent_task:<old> --link parent_task:<new>`.
`--set parent_task=…` is refused — structural link fields are not plain fields.

### Documents (was `task attach`)

The doc store is **fs-as-truth**: a file written into `tasks/<ID>/` is visible on
the next read, with no registration step.

| Before                          | After                                                  |
| ------------------------------- | ------------------------------------------------------ |
| `task attach add ID F --name N` | `rack transition ID --attach F:N`                      |
| `task attach list ID`           | `rack context ID` (documents block)                    |
| `task attach show ID N`         | `rack context ID --with 'N'`, or read the printed path |
| `task attach remove ID N`       | `rack transition ID --rm N`                            |

### Hypotheses and proposals

Status moves are **named FSM edges**, not a `--status` flag.

| Before                                             | After                                       |
| -------------------------------------------------- | ------------------------------------------- |
| `task hyp create "H"`                              | `rack hyp create "H"`                       |
| `task hyp list`                                    | `rack ls --backlog hyp`                     |
| `task hyp show HYP-NNN`                            | `rack context HYP-NNN`                      |
| `task hyp set-status --hyp ID --status confirmed`  | `rack transition ID confirm`                |
| `task hyp set-status --hyp ID --status refuted`    | `rack transition ID refute`                 |
| `task hyp set-status --hyp ID --status stalled`    | `rack transition ID stall`                  |
| `task hyp append-verdict --hyp ID --session S`     | `rack hyp append-verdict ID --session-id S` |
| `task hyp autoclose [--k N] [--dry-run]`           | `rack hyp autoclose [--k N] [--dry-run]`    |
| `task proposal vote --prop ID --session S`         | `rack proposal vote ID --session-id S`      |
| `task proposal status --prop ID --status accepted` | `rack transition ID accept`                 |
| `task proposal status --prop ID --status rejected` | `rack transition ID reject`                 |

### Removed with no equivalent

| Before                              | Status                                                                                                                         |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| `task sync`                         | **Gone by design.** `STATE.md` regenerates post-lock after every write — nothing to run.                                       |
| `task attach verify`                | **Gone by design.** No manifest exists: presence *is* registration, and `context` reports frozen-pin drift live.               |
| `task hyp migrate`                  | **Gone.** One-shot data migration, already applied.                                                                            |
| `task list --search <regex>`        | **Not isomorphic.** `rack ls --grep` is a case-insensitive **substring** match, optionally field-scoped (`--grep id:PROJ-04`). |
| `task list --reclaimable`           | **No equivalent.**                                                                                                             |
| `--description-file`                | **No equivalent.** Use `rack transition ID --set description="$(cat body.md)"`.                                                |
| `-d` / `-p` short flags on `create` | Long forms only: `--description`, `--priority`.                                                                                |

## 2. `task_prefix` is read from `ai-hats.yaml` only

**What changed (HATS-1260).** Auto-detection of the task-id prefix from existing
`tasks/<PREFIX>-NNN/` folders — and its persist-back into `ai-hats.yaml` — was a
feature of the removed CLI path. rack reads `task_prefix` from `ai-hats.yaml` and
nothing else. **The default also changed: `TASK` → `HATS`.**

**Migrate.** If your project relied on auto-detection, set the prefix explicitly
**before** creating the next card, or it will be minted as `HATS-NNN`:

```bash
ai-hats config set --task-prefix ACME
```

Existing cards are unaffected — this governs new ids only.

## 3. `packages/ai-hats-tracker` is deleted

**What changed (HATS-1262).** The package backing the retired CLI is gone from
the uv workspace, the root dependency set, and the publish workflow. Its
surviving internals were re-homed first: the ownership registry, `linked_context`
and `TrackerPaths` into `ai_hats` (HATS-1258); the retro window onto the rack
facade (HATS-1259); `plan_extract` into `ai_hats_rack`.

**The on-disk task format is NOT affected.** rack reads the same
`<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml` card-per-directory layout,
with the same path constants. **No data migration is needed for your tasks.**

One adjacent, already-applied change to be precise about: the data migrator
normalizes `tracker/backlog/hypotheses/` from flat `HYP-*.yaml` files to the
dir-per-card catalog (HATS-1054). It runs automatically and the legacy flat
files are still read as a fallback. **The tasks catalog is untouched.**

**Already-published `ai-hats-tracker` versions stay on PyPI** — they are not
yanked. Publishing simply stops. `pip install ai-hats-tracker` therefore still
resolves; do not treat its installability as a sign the CLI is supported.

> **If you run an editable install, read §6 before upgrading.** This deletion is
> what can strand a pre-0.14.0 editable install in a self-heal loop that also
> swallows `ai-hats self update`.

## 4. `ai-hats self update` prunes retired distributions

**What changed (HATS-1280).** `self update` installs, it does not synchronize —
a dependency the new version dropped used to stay in the venv with its console
scripts. After 0.14.0 that would leave a fully functional legacy backlog CLI over
the same store, which is exactly the hazard §1 removes. The prune now runs
post-install in the **new** interpreter, from an explicit retired-distribution
list.

**Migrate.** Nothing to do — it happens on your next `ai-hats self update`, and
prints one line per removal. Two exceptions:

- **Editable / `local`-channel dev installs stand down** by design (a workspace
  `uv sync` would reinstate what the prune removed). Remove it by hand if you
  want it gone: `uv pip uninstall ai-hats-tracker`.
- **A project with no composed role never bumps**, so it never prunes.

It also fails open — no `uv` on PATH, a timeout, or a non-zero exit warns and
continues rather than breaking the upgrade.

## 5. The deprecation-warning exemption, and its limits

The breaking-change protocol [1] normally requires a `DeprecationWarning` release
before removal. The pre-1.0 exemption applies here. **Claimed consumers, and how
each was verified migrated:**

| Consumer                                                     | Migrated by          | Verified at HEAD                                           |
| ------------------------------------------------------------ | -------------------- | ---------------------------------------------------------- |
| `backlog-manager` skill tree (SKILL + 5 refs)                | HATS-1261            | Deleted; was composed by zero roles before removal         |
| `pre-commit-attachments.sh` (only `attach verify` caller)    | HATS-1261            | Deleted with the skill tree                                |
| `rule_backlog_discipline`, `hatrack-trait`, `hatrack`        | HATS-1261            | Clean; the two surviving mentions are RED/GREEN deterrents |
| 6 agent-facing skills (`judge-*`, `review-*`, `git-mastery`) | HATS-1276            | Clean; each declares `ai_hats.requires.cli: ai-hats-rack`  |
| reflect engine prompt (`session_review_runner`)              | HATS-1276            | Clean                                                      |
| `reflect-hypothesis-interactive` mutation whitelist          | HATS-1358            | Clean; verified via `config show-prompt`                   |
| `ai-hats-maintainer` / `base-auditor` trait injections       | HATS-1358            | Clean; verified via `config show-prompt`                   |
| `plan-discipline` skill                                      | HATS-1358            | Clean                                                      |
| Docs (25 files) + `--help` strings                           | HATS-1265, HATS-1358 | Clean outside the ADR / `migration-v0.8.0.md` carve-outs   |
| Test tiers (~86 files)                                       | HATS-1263/1282/1264  | Re-pointed at `rack`, not deleted                          |
| `ai-hats-tracker` console script in existing venvs           | HATS-1280            | Pruned on upgrade; 5 real-venv e2e                         |

**What this claim does not cover.** Consumer projects outside this repository
are **not enumerable from here**, and no verification artefact for them exists.
The exemption is claimed for the consumer set this repository ships. If you
maintain a project that drove its backlog through `ai-hats task`, you are the
unenumerated consumer this clause could not buy a cycle for — §1 is your
migration path, and the boxed warning above is the failure mode to check first.

## 6. If `ai-hats` hangs repeating "missing runtime deps" — recover out of band

**The one failure this release can inflict on the way in.** Symptom, observed
live in a consuming project:

```console
$ ai-hats config status
ai-hats: missing runtime deps ['ai-hats-tracker']; healing via uv…
ai-hats: missing runtime deps ['ai-hats-tracker']; healing via uv…
ai-hats: missing runtime deps ['ai-hats-tracker']; healing via uv…
^C
```

**Why it happens.** §3 deletes `packages/ai-hats-tracker`. A project whose
editable `ai-hats` install metadata was written *before* that deletion still
declares the dependency, while its `.pth` points at a source directory that no
longer exists. `uv pip install ai-hats-tracker` then audits the stale dist-info
as already-satisfied and exits 0 **without making the module importable**. The
old bootstrap trusted that exit code and re-exec'd, met the identical missing
dep, healed again, re-exec'd again — forever.

**Why you cannot fix it with `ai-hats`.** The bootstrap runs before subcommand
dispatch, so `ai-hats self update` — the in-band repair — hangs in the same
loop. This is why the recovery below is a bare `uv` command: at this point no
`ai-hats` invocation can complete.

**Recover** (point the editable install at your actual ai-hats checkout):

```bash
uv pip install --python <project-venv>/bin/python -e /path/to/ai-hats
```

Then confirm and finish the upgrade normally:

```bash
<project-venv>/bin/python -m ai_hats config status
ai-hats self update
```

**Already fixed going forward (HATS-1359).** From this release, the bootstrap
rechecks whether the dependency actually became importable and, if not, exits 1
printing the rescue command instead of re-execing. Note the ordering, though:
the code that runs during *your* upgrade is the version you are upgrading
**from**, so a pre-0.14.0 install can still hit the loop once — this section is
the escape hatch for exactly that window.

**Healed automatically after 0.14.0 (HATS-1368/HATS-1367).** Past that window
the recovery above is what the bootstrap now runs for you: on an editable
install it reads your checkout's `pyproject.toml` rather than the frozen
METADATA, so it sees the real dependency set and re-points the checkout itself.
The same applies to the mirror-image failure this section does not cover — an
install whose metadata predates the workspace split and declares no first-party
deps at all, which used to die with a bare `ModuleNotFoundError: ai_hats_wt`.
Both now heal on the next `python -m ai_hats …`. Invoked through the `ai-hats`
launcher, a venv missing a workspace member is still refused up front with a
`self update` hint (HATS-895) — run that, and the heal happens there.

## References

**[1]** — [`docs/RELEASING.md`](RELEASING.md) — SemVer policy and the
breaking-change protocol, including the pre-1.0 enumerable-consumers exemption.

**[2]** — [`CHANGELOG.md`](../CHANGELOG.md) — the `[0.14.0]` section; its
`Removed` entries reference this doc.

**[3]** — [`docs/how-to-hatrack.md`](how-to-hatrack.md) — the day-to-day backlog
guide on `rack`, successor to `how-to-backlog.md`.

**[4]** — [`docs/adr/0017-backlog-yaml-single-definition.md`](adr/0017-backlog-yaml-single-definition.md)
— the decision record behind the rack kernel: one `backlog.yaml` per backlog,
schema-driven fields, declared link kinds and handler bindings.
