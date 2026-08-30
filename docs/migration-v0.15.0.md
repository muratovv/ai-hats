# Migration: v0.14.0 → v0.15.0

`v0.15.0` is a MAJOR bump under the pre-1.0 `0.MAJOR.MINOR` scheme. It carries
ten breaking-surface changes across four areas: the **Python pin** (§1), the
**composition layer** — roles, rules and their sidecars (§2–§4), the **surfaces
area** (§5–§6), and the **rack selector grammar** (§7).

**Your task data is not touched.** The on-disk tracker layout
(`<ai_hats_dir>/tracker/backlog/tasks/<ID>/task.yaml`) is unchanged. There is no
data migration step.

This doc is the migration reference required by the breaking-change protocol [1]
and is linked from the `Removed` and `Changed — BREAKING` entries in the
changelog [2].

> **Read §1 first.** It is the only change that makes the upgrade itself fail,
> and the recovery is out-of-band — the new version's code is not what runs it.

> **Deprecation-warning exemption — claimed under the pre-1.0 clause.**
> [`docs/RELEASING.md`][1] allows skipping the one-MINOR deprecation-warning
> release when every consumer of the removed surface is enumerable and already
> migrated. That claim is made here in writing, with its limits stated — see
> §8.

## 1. Python pin and floor raised to 3.13 — the upgrade fails once

**What changed (HATS-1521).** `PINNED_PYTHON` is `3.13` (was `3.11`) and
`requires-python` is `>=3.13` on the integrator and all five workspace members.
The CI matrix is now 3.13 + 3.14.

**Why.** The old pin sat on the *floor* of the support matrix, so every fresh
install got the version where the HATS-1519 argparse defect lives — `--` before
positionals is rejected on 3.11/3.12 and accepted on 3.13+.

> ### ⚠️ `ai-hats self update` cannot cross this pin
>
> `self update` builds the new version's venv with the **old** code's pin
> (3.11), then cannot resolve a distribution requiring `>=3.13`. It exits 1
> with uv's `does not satisfy Python>=3.13`.
>
> **Nothing is damaged and the old install keeps working.** This is a refusal,
> not a partial upgrade.

**Migrate.** Once per install, out of band — the launcher, not the old Python,
builds the replacement venv:

```bash
curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh \
    | bash -s -- --repair
```

A session whose interpreter is off the pin now says so at startup rather than
failing later somewhere unrelated. `scripts/check_python_pin.py` (stage
`python-pin`) refuses a partial bump and a pin the CI matrix does not run.

## 2. The two role-audit roles were renamed

**What changed (HATS-1425).** `auditor-for-role` → `role-auditor`,
`judge-for-role` → `role-judge`. Neither role's composition, protocol, or
contract changed.

**Why.** The system carries two headless-auditor → HITL-judge pairs, and only
this one was named off-pattern (`judge-auditor` → `judge` beside
`auditor-for-role` → `judge-for-role`), which read as if the auditor were a
curator rather than its opposite.

> ### ⚠️ A stale role key fails silently
>
> `ComponentConfig` is declared `extra="ignore"`, so a customization naming an
> old role is **dropped with no error** — the customization simply stops
> applying. Nothing will tell you.

**Migrate.** Grep every customization block, project `ai-hats.yaml`, and script:

```bash
grep -rn 'auditor-for-role\|judge-for-role' \
    ai-hats.yaml ~/.ai-hats/ .agent/ scripts/ 2>/dev/null
```

| Old                | New            |
| ------------------ | -------------- |
| `auditor-for-role` | `role-auditor` |
| `judge-for-role`   | `role-judge`   |

## 3. Every composed rule body is delivered in full

**What changed (HATS-1515).** Every rule in `composition.rules` delivers its
`rule.md` body into the system prompt's `## RULES` section. The `ALWAYS_ON_RULES`
and `SUMMARIZED_IN_INJECTION` sets are removed.

**Migrate.** Nothing to edit — but **budget for it**. A role that composed a
rule previously delivered as a summary now carries its full body every turn.
Check your roles' resident cost before and after:

```bash
ai-hats list tokens <role>
```

If a role's budget grew past what you want to pay, the fix is to drop the rule
from that role's traits, not to shorten its body — `rule-delivery-gate` now
checks only that every rule pointer names a rule that exists in the library.

## 4. Rules no longer carry a `metadata.yaml`

**What changed (HATS-1836).** A rule is one file: `rule.md`. The sidecar held a
second copy of each rule's meaning in `description`, and **5 of 14 had drifted**
from the bodies they described. Nothing machine-checkable holds two copies in
agreement — a semantic divergence is not a gate's to catch — so the copy went.
The `delivery` field went with it, finishing what §3 started.

`ai-hats list rules` now prints names, symmetric with `list skills` and
`list traits`.

**Migrate — for external libraries: nothing.** A leftover
`rules/<name>/metadata.yaml` is simply never read. Rule discovery has always
been marked by `rule.md`, so the sidecar can stay or go without effect.

## 5. The allow-rule lint is gone — check your `permissions.ask`

**What changed (HATS-1861, [ADR-0031](adr/0031-the-hook-holds-the-gate-alone.md)).**
HATS-1642 rested on one measurement: a broad `permissions.allow` rule silenced
the consent gate. Re-measured on Claude Code 2.1.247 it **does not reproduce** —
a PreToolUse hook that returns `ask` blocks the call whatever `allow` says, in
`default`, `auto`, `dontAsk` and `bypassPermissions` alike. The fixture is in
the repo (`experiments/harness-permission-precedence/probe.sh`, four arms, two
of them controls, verdict read from a side-effect file rather than the model's
prose), because the claim is about someone else's release and nothing in CI can
hold it.

Deleted: `consent_permission_lint.py`, `permission_warning()` in
`safety_gate.py`, five tests written against the retired behaviour, and the
glossary's "Allow-rule lint (two verdicts)". `consent_spellings.py` stays — the
HATS-1781 D6 runtime boundary reads the same table.

> ### ⚠️ The lint's advice had a price
>
> Answering each finding with a matching `ask` rule **silently disarmed** the
> two places the framework deliberately says nothing: a **consent grant**
> (`consent wt.merge 30` bought no silence at all on the operations it names)
> and `allow_verdict()`'s routine-`rack` auto-allow. An `ask` rule prompts even
> when the hook returned `allow`.

**Migrate.** If you followed the lint's advice, your `permissions.ask` has
grown — in this project it had reached **66 entries**. Review it and delete the
entries you added only to silence the lint; the consent grant works again once
they are gone.

## 6. Surfaces: symbol renames, and four that stopped being distributions

### 6a. The surfaces area speaks `surface`

**What changed (HATS-1826).** Every in-process symbol renamed:

| Old                         | New                        |
| --------------------------- | -------------------------- |
| `Provider`                  | `Surface`                  |
| `ProviderHint`              | `SurfaceHint`              |
| `ProviderRunResult`         | `SurfaceRunResult`         |
| `ClaudeProvider` (+4)       | `ClaudeSurface` (+4)       |
| `ai_hats.providers`         | `ai_hats.surface_registry` |
| `get_provider`              | `get_surface`              |
| `register_provider`         | `register_surface`         |
| `provider_names`            | `surface_names`            |
| `ai_hats.surfaces_registry` | `ai_hats.surface_catalog`  |

**Unchanged, on purpose:** the `ai_hats.providers` **entry-point group**,
`-p/--provider`, `ai-hats list providers`, the `provider:` key in
`ai-hats.yaml`, and the `provider` marker in session artifacts. Renaming those
would break installed third-party surfaces and existing sessions and buys
nothing; `docs/glossary.md` records the boundary so neither side gets "fixed"
to match the other.

> ### ⚠️ The method rename does not alias
>
> `Provider` / `ProviderHint` / `ProviderRunResult` survive as **deprecated
> aliases** on `ai_hats.surfaces`, so `class MySurface(Provider)` still
> imports. But a surface overriding `provider_hints` is **silently never called
> again**. Rename the override to `surface_hints`.

### 6b. Four surfaces stopped being distributions of their own

**What changed (HATS-1826).** `agy`, `cline`, `codex` and `opencode` shipped as
packages under `packages/surfaces/<name>/` — four PyPI projects, four publish
jobs, four trusted-publisher environments — for a tier ADR-0014 created to state
a dependency rule, not to ship wheels. Each is now a folder in the
`ai_hats.surfaces` area (`src/ai_hats/surfaces/<name>/`), declared next to
`claude` under `[project.entry-points."ai_hats.providers"]` in the root
`pyproject.toml`.

The seam is untouched: surfaces keep their names, discovery is still the
`ai_hats.providers` entry point, and an out-of-tree package can still register
one. Selecting a surface no longer installs anything — ai-hats never runs an
installer for a surface it already ships.

**Migrate — nothing to do; the upgrade prunes what it replaced.**
`ai-hats-agy` (0.2.0) and `ai-hats-cline` (0.5.0) had reached PyPI, and
`self update` installs rather than synchronizes, so both would have stayed in
the venv shadowing the folded code — with agy's `ai-hats-hook-dispatcher`
console script still on `PATH`. They are listed in
`src/ai_hats/retired_dists.py`, which uninstalls a retired distribution during
the upgrade to the release that retires it (HATS-1280).

`ai-hats-codex` and `ai-hats-opencode` need no prune: their publish jobs were
gated behind an `ai-hats>=0.15.0` floor that no tag has ever met, so no venv can
be carrying them.

## 7. A rack lifecycle point is now an arrow, and it denotes a SET

**What changed (HATS-1719, HATS-1720).** `at: [edge:<from>--<to>]` is replaced
by `at: ['<from>-><to>']`. The retired spelling is **removed, not aliased**: a
role still carrying it is refused at composition, naming the row. Grammar and
legality: [ADR-0017](adr/0017-backlog-yaml-single-definition.md) §3.

**Why it is worth the break.** The shipped `->done` gate ("is master green after
this card") was bound to `edge:review--done` — **one** of the **eight** edges
into `done` that the worktree teardown-merge fires on. A forced close
(`rack transition <id> --state done --force`) merged into master with that
question never asked. `at: ['->done']` closes all eight.

**Migrate.** Rewrite every `at:` row in your roles and traits:

| Old spelling        | New spelling     | Means                           |
| ------------------- | ---------------- | ------------------------------- |
| `edge:review--done` | `'review->done'` | that one edge                   |
| *(no equivalent)*   | `'->done'`       | every road **into** `done`      |
| *(no equivalent)*   | `'execute->'`    | every road **out of** `execute` |
| *(no equivalent)*   | `'ANY->ANY'`     | every move of the backlog       |

Also breaking, for anyone reading these surfaces:

- **The event key** in the transition journal, `audit.jsonl` and
  `AI_HATS_HOOK_POINT` is now `<from>-><to>`. Records written earlier keep the
  old spelling; `rack context --attr audit --event` accepts **both** and
  resolves them to the same record.
- **`rack doctor --json`** renamed its per-row key `point` → `selector`, and
  `role_materialization.json` did the same — the latter additionally carrying
  `from` and `to` already parsed, because the PreToolUse guard is stdlib-only
  and must not hold a second copy of the grammar.
- **`ai_hats_rack`**: `Subscription.event_key` → `.selector`,
  `BindingStatus.point` → `.selector`, `checks.parse_edge_point` →
  `selectors.parse_selector`, `checks.EDGE_PREFIX` removed.
  `Dispatcher.subscribers_for` now answers for non-FSM keys only; an edge is
  addressed by its pair via `subscribers_for_edge`. `all_edge_keys` is gone —
  the product is `all_edges`, returning typed pairs.
- **`ai_hats_core`**: `ConsentPoint.point` → `.selector`.
- **`FrozenIntegrityExtension(tasks_dir, topology=…)` no longer takes
  `topology`** — it says `ANY->ANY`. Same for the integrator's ownership /
  worktree / consent adapters.
- **A topology may no longer name a state `ANY` or `NONE`** — refused at load.
  The selector grammar owns both words, and a state actually called `ANY` would
  turn exact subscriptions into wildcards.
- **`ANY` on ONE side is refused** as a second spelling of the empty side:
  write `->done`, `execute->`, or `ANY->ANY`.

**Two rules on wide selectors.** A declared row may not stand on a wide
*output*, and each refusal names the card that lifts it. A `run:` row is in-lock
and can refuse, and one refusal on every way out locks the card in that state.
`consent:` is refused for an unrelated reason: the guard matches on the target
state and never learns which state the card is leaving.

**A behaviour change worth knowing.** A card sitting in a state the topology no
longer has now reaches the subscribers. Previously, after a state was renamed or
dropped in `backlog.yaml`, `rack transition <id> --state done --force` on a card
left behind wrote the state and ran **nothing** — no gate, no consent, no
worktree teardown, no ownership release. Now it runs them.

## 8. `AI_HATS_DIR` + a foreign `AI_HATS_PROJECT_DIR` pin now exits 1

**What changed (HATS-1471).** When `AI_HATS_DIR` is set to a sandbox directory
and `AI_HATS_PROJECT_DIR` is set to a foreign project path, `rack` commands and
`ai-hats wait` refuse with **exit code 1** and typed error `foreign_project_pin`,
naming both paths and `ai_hats_dir`.

**Previously** `rack` ignored `AI_HATS_DIR` on CLI resolution and wrote to the
**live project root** — the reason this is a refusal now.

**Migrate.** If a script sets both variables deliberately, make them agree: point
`AI_HATS_PROJECT_DIR` at the project that owns the `AI_HATS_DIR` sandbox, or
unset one.

## 9. The deprecation-warning exemption, and its limits

The pre-1.0 clause in [`docs/RELEASING.md`][1] lets a removal ship without a
prior warning release when every consumer is enumerable and already migrated.
The claim is made per change, not blanket:

| Change                  | Consumer set                                   | How verified                                                                              |
| ----------------------- | ---------------------------------------------- | ----------------------------------------------------------------------------------------- |
| §4 rule `metadata.yaml` | Every rule the library ships                   | Sidecar is never read; discovery is marked by `rule.md`, so an external leftover is inert |
| §5 allow-rule lint      | This repository's own `permissions.ask`        | The lint had no API; its only output was advice                                           |
| §6a symbol renames      | In-tree surfaces + out-of-tree via entry point | Type names keep deprecated aliases; only `provider_hints` breaks, and it is named here    |
| §6b four surfaces       | `ai-hats-agy`, `ai-hats-cline` on PyPI         | Pruned automatically via `retired_dists.py`; the other two never published                |
| §7 `edge:` spelling     | Roles and traits carrying `at:` rows           | **Refused at composition, naming the row** — this one cannot fail silently                |

**Where the claim does not reach:** §2 (role renames) and §3 (rule delivery) are
the two that can change behaviour with no error — a stale role key is ignored
(`extra="ignore"`), and a fuller prompt is not an error at all. Both are called
out with the grep or the command that surfaces them.

## References

**[1]** — [`docs/RELEASING.md`](RELEASING.md) — SemVer policy and the
breaking-change protocol, including the pre-1.0 enumerable-consumers exemption.

**[2]** — [`CHANGELOG.md`](../CHANGELOG.md) — the `[0.15.0]` section; its
`Removed` and `Changed — BREAKING` entries reference this doc.

**[3]** — [`docs/adr/0017-backlog-yaml-single-definition.md`](adr/0017-backlog-yaml-single-definition.md)
— the rack kernel decision record; §3 carries the selector grammar §7 changes.

**[4]** — [`docs/glossary.md`](glossary.md) — the `surface` / `provider`
boundary §6a deliberately keeps.
