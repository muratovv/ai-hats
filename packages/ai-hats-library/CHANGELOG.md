# Changelog — ai-hats-library

All notable changes to this package are documented here. Versioning is semantic,
on the library **format schema** (see README § Versioning).

## 0.4.1

- **`hatrack` stops hand-maintaining the named edges** (HATS-1257). The rendered
  `{{backlog_fsm_edges}}` table now annotates an edge that carries a `name:`,
  so the prose under it no longer re-lists `reclaim` and `reopen` — it states
  the mechanism instead: a parenthesised name is typeable in place of the target
  state (`rack transition <ID> reclaim`). The third mention, a back-reference
  under the per-edge policy table, is gone. Any named edge a backlog declares
  now reaches the prompt on its own, including ones added after this release.

## 0.4.0

- **`safety-guard` matches paths and subcommands, not bare tokens** (HATS-1253).
  `check_git` denied any command whose tokens contained `push`, duplicating a
  concern `pre_bash_shared_state_guard.sh` already owned. A deny is binary, so
  the coarser gate won and the documented ack handshake could never complete:
  supervisor-approved pushes were refused, as were `git stash push`,
  `--dry-run`, and read-only searches whose *arguments* named the word. The
  duplicate is gone; the shared-state guard owns the policy alone and gates a
  regular push behind `AI_HATS_SHARED_STATE_ACK=1`.
- **`rm` is path-based** (HATS-1253). The old check matched the binary name, so
  `rm -rf /tmp/scratch` and `rm -rf /` were indistinguishable — it denied the
  cleanup `global_rule_resource_hygiene` mandates while being no stricter on a
  production database. Now: scratch paths pass, the paths
  `global_rule_destructive_actions` names take `AI_HATS_DESTRUCTIVE_ACK=1`, and
  the filesystem root / `$HOME` are refused outright. `chown` and `truncate`
  leave the blocked list; `mkfs.*` is caught (bare `mkfs` never matched a real
  invocation) and `dd` only when writing to a device.
- **Every denial names the flag that opens it**, or says none does.
- **Command splitting is quote-aware** (HATS-1253). The regex split on
  `;`/`&&`/`||`/`|` ignored quoting, so a quoted argument synthesised a phantom
  command and the guard denied commands that were never issued.

## 0.3.0

- **Default backlog manager flipped to rack (hatrack)** (HATS-1054). `trait-agent`
  now composes the `hatrack` skill instead of `backlog-manager`, so every agent
  role drives the whole backlog through the `rack` CLI. `hatrack-trait` is
  retained as the explicit-selection / rollback anchor until the classic manager
  is retired; the `review → execute` rework consumers flipped now the edge is
  live (HATS-1052).
- **Added `hatrack` skill + `hatrack-trait`** (HATS-1046) — lifecycle / reads /
  documents / links / field-edits / hypotheses / proposals over `rack <verb>`,
  with an FSM token + per-edge policy and two-section restructure (HATS-1051)
  and advance-per-phase lifecycle cadence (HATS-1050).
- **Added `rack-advanced` skill** (HATS-1081) — authoring custom backlogs plus
  cross-project registry / search.
- Parent **"Work Policy"** section delivered to child cards via a declared
  `work_policy` field (HATS-1064 / HATS-1067).
- **Harness-reminder hygiene**: forbid the harness task tools in ai-hats
  projects (HATS-1071); stop narrating ignored harness reminders (HATS-1069).
- Authoring checklists gain negation / negative-space lenses; `task-slicing`
  trimmed to its non-prior core.

## 0.2.1

- **`worktree-isolation`**: the Finish step is now the supervised close
  (HATS-1019) — `wt merge` / `transition done` are refused without
  `AI_HATS_MERGE_ACK=1`; agents stop at `review`, the supervisor merges.
  **`rule_pause_before_shared_state_write`**: `ai-hats wt merge` /
  `task transition done` join the shared-state table; `AI_HATS_MERGE_ACK`
  joins the never-self-set overrides.

## 0.2.0

- The shipped **`backlog-manager`** skill now declares its tool dependency in
  `SKILL.md` frontmatter: `ai_hats.requires.cli: ai-hats-tracker` (with a
  `check`/`hint`), per ADR-0016 / HATS-991. The skill stays portable content in
  the library content layer; it is **not** co-located inside `ai-hats-tracker`.
  Declaration only — the verify-and-warn verifier lands separately (HATS-992).
  Coupled with `ai-hats-tracker>=0.6.0`, which exposes the `ai-hats-tracker`
  console entry the `requires.cli.check` probes. Format `schema_version` is
  unchanged (`1`) — content-level change, not a schema change.

## 0.1.0

- Initial extraction from the `ai-hats` integrator into a standalone, data-only
  workspace package (ADR-0014 §3–6; HATS-876 / T18). The `core/` + `usage/` +
  `hooks/` content is unchanged; the move makes it independently
  `pip install`-able and `git clone`-droppable. The integrator now resolves its
  built-in library layer from `importlib.resources.files("ai_hats_library")`
  through a single `as_file` seam (review P1 #14).
