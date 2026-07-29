# ADR-0020: Hook substrate — one materialization posture, one execution contract

## Status

Proposed (HATS-1240, 2026-07-29). Design of record for epic **HATS-1266**.

**Born by splitting ADR-0019 [1].** Rev 7 of that ADR accumulated two stories:
the declarative extension model (`checks:` — epic HATS-1138) and the substrate
underneath it — where existing hook scripts live and how they are executed
(epic HATS-1266). The supervisor split them during the HATS-1240 review,
before rev 7 ever merged: the channel taxonomy (was D10), the execution
mechanics (was the mechanics half of D4/D5) and the git_hooks orchestrator
contract moved here. ADR-0019 keeps the extension model and binds to this
document for everything below.

Becomes `Accepted` when the epic's exit criteria hold: every channel on the
assigned posture (D4), the primitive live under the worktree channel, and the
orchestrator installed by `init` on a clean project.

## Context

Four hook channels accreted, and each solved "where does the script live"
independently: `lifecycle_hooks` copied a union of every library skill into
`tracker/lifecycle-hooks/<event>.d/`, `runtime_hooks` (claude) flatten-copies
into `library/hooks/<skill>-<basename>`, the worktree channel copies into
`library/wt-hooks/`, and `git_hooks` copies into `.githooks/<event>.d/` behind
a generated dispatcher. Every copy grew police: three manifests, a sweep,
`_assert_manifest_intact`, drift arms, a leak detector, and the "recorded
carry ⇒ backing script exists" invariant. The copies drift, the police wedge,
and flatten-copying drops the data files shipped beside a script.

Execution is equally split: two runners with different timeouts, different env
vocabularies, one that leaves stdin open (a hook can hang a rack transition
while holding the task lock) and one that swallows the child's refusal reason
into a log file.

This ADR owns the substrate: **one taxonomy for where scripts live (D1), one
contract for how they execute (D2), one durable global artifact (D3), and the
migration of the existing channels (D4).** What binds to it from above —
declarative `checks:`, the point catalog, binding policy — is ADR-0019 [1].
What providers materialize per session is ADR-0018 [2].

## Decision

### D1 — Channel taxonomy: `in_process` vs `detached`

The axis is **not** "snapshot versus live". Nothing in the system snapshots
*bytes*: even the worktree carry persists only the hook **set**
(`{skill, script, on}`) into worktree state and re-resolves the content at
teardown. The axis is **who is guaranteed to be running when the script is
spawned**:

| mode         | who spawns it                                | what it requires                                                                                                                                                                      |
| ------------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `in_process` | ai-hats itself                               | an absolute path resolved fresh at spawn; no copy, no manifest, sibling files intact                                                                                                  |
| `detached`   | a third party, with no ai-hats process alive | one static dispatcher per event, installed at init — no role logic, no venv paths; the gate set resolved at spawn time from ai-hats-owned state; **fail-open** when ai-hats is absent |

Naming the axis is what prevents the next channel from picking a shape by
imitation — each existing copy was imitated from the previous channel's, and
only one of the four ever *needed* one.

Two further attributes, previously implicit and silently violated:

- **`selection: composed | union`.** Only `lifecycle_hooks` collected over
  every library skill; every other channel is per-composed-role. ADR-0019 D7
  retires the union special case with the channel itself.
- **`bundle: script | dir`.** Whether a hook needs the sibling files shipped
  beside it. Flatten-copying a single script silently drops them, and two live
  scripts already read siblings via `__file__` or a repo-relative path. Under
  `in_process`, `bundle: dir` is free — the resolved path's siblings are
  simply there; ADR-0019 D9 gets the same property by snapshotting the
  declaring skill's directory whole.

### D2 — The execution primitive

One function spawns every ai-hats-run hook and returns a governed outcome
(implementation: HATS-1151; ANSI sanitisation of the reason channel:
HATS-1161). **The primitive owns mechanics; callers own policy** — what
`refuse` means at a rack edge, at a worktree teardown, or at a future
`checks:` binding (`on_error`, ADR-0019 D4) is the caller's contract.

| exit          | meaning                         | effect                                  |
| ------------- | ------------------------------- | --------------------------------------- |
| `0`           | pass                            | operation proceeds                      |
| `2`           | **refuse** — the hook's verdict | refused; stdout tail becomes the reason |
| `1` and other | the hook itself broke           | governed by the caller's error policy   |

**Refuse is exit 2, deliberately not 1.** Under `set -e`, a false `[[ ... ]]`,
a `grep` with no match and a failing `jq` all abort a bash script with
**exit 1** — exit 1 is the status a shell check produces *by accident*, so it
routes to the caller's error policy, never reads as a considered verdict. The
inverse is live in-repo: `drain-review.sh` does `exit "$rc"` propagating
`hunk-notes.sh`'s status (2, 127, …) to fail **closed** — under a naive
"other = broke" plus a warn policy that data-protection script would fail
**open**, silently reproducing HATS-1130. Precedent for not signalling a
verdict by exit status: the runtime-hook channel returns 0 and emits
`permissionDecision: deny` on stdout (`safety_gate.py`).

Mechanics, uniform across callers:

- `126`/`127` (not found / not executable) and anything `>128` (signal death:
  130 SIGINT, 143 SIGTERM) are **broke**, named as such in the message.
  SIGINT aborts the whole operation regardless of error policy — never
  swallowed.
- A **timeout is broke, not refuse**: a hung hook formed no verdict.
- **The timeout is a caller parameter, not a shared constant.** The channels
  sit behind different locks (rack kernel 30 s, wt lifecycle 60 s); the
  invariant is `hook_timeout < that caller's lock timeout`, asserted at import
  per caller. One global constant would make the assert a coincidence.
- `stdin = DEVNULL`, always — a hook that reads interactively terminates
  instead of hanging a transition while holding the task lock.
- Text decoding with `errors="replace"` — invalid bytes become a governed
  error message, never a `UnicodeDecodeError` stack trace inside a consumer.
- The **reason** is the tail of the child's **stdout**; stderr is captured
  separately so a verbose diagnostic stream cannot truncate the verdict away.
- **One shared env base:** `AI_HATS_PROJECT_DIR`, the fully-qualified
  point/event identifier, `AI_HATS_FORCE`. Point-specific additions stay with
  their caller (the `checks:` vocabulary is ADR-0019 D5; wt teardown keeps
  its own).

### D3 — The git_hooks orchestrator

`git_hooks` is the only `detached` channel, on two independent constraints. A
human `git commit` runs the hook with no process able to evaluate
`builtin_library_root()` — and that resolver is not a stable path source in
any case: it is worktree-aware, cwd-sensitive, and in the zipimport tier
resolves to a temp directory alive only for the calling process. Separately,
git requires a real executable named exactly `<event>` under
`core.hooksPath`, and the dispatcher is generated rather than shipped by a
skill. Note what is *not* a reason: `.githooks/` is git-ignored, so "it is
committed with the repository" does not apply.

**The detached contract** (supervisor decision 2026-07-29, HATS-1266
re-scope; implementation HATS-1337):

- The durable artifact is only the **orchestrator**: the per-event dispatcher
  plus `core.hooksPath`, installed at `ai-hats init`. It carries no role
  logic and no paths into a versioned venv, so it survives `self update`
  unchanged and is independent of which roles are composed.
- Gate *content* stays in the declaring skills. The dispatcher resolves the
  gate set at **spawn time** from ai-hats-owned state — no per-gate copies
  under `<event>.d/`, no `.ai-hats-manifest`, no GIT drift arm. The
  resolution mechanism (the `versions/current` interpreter vs a composition
  snapshot refreshed at compose points) is settled at HATS-1337's plan.
- Resolved paths are containment-checked. The retired flattening's
  `Path(...).name` was doing security work against tampered persisted state;
  that property survives the flattening's removal, here and in the worktree
  channel alike.
- Degradation is **fail-open with a one-line warning**: a wedged human commit
  is never an acceptable failure mode, and the previous fail-closed
  backstop's named remedy — `ai-hats self init` — needs exactly the binary
  that is gone. A gate that cannot be resolved is a gate that does not run,
  said out loud; it is never a commit that cannot happen.
- The orchestrator must gate **every worktree of the repository**, not only
  the main checkout — worktrees share `.git/config`, and a relative
  `core.hooksPath` resolves against the worktree top where no `.githooks/`
  exists. (Mechanism at HATS-1337's plan.)

Symlinking gates into the library — the halfway house ADR-0019 rev 7
considered — stays rejected, on staleness grounds: a link into a versioned
install dies at every `self update`, which is the one lifecycle event the
orchestrator must survive.

### D4 — Assignment and migration of the existing channels

| channel                | posture      | card                                                               |
| ---------------------- | ------------ | ------------------------------------------------------------------ |
| `lifecycle_hooks`      | **deleted**  | HATS-1147 (tombstone; ordering: ADR-0019 D8)                       |
| `runtime_hooks` claude | `in_process` | HATS-1268 — resolve into the session skill tree                    |
| `runtime_hooks` agy    | `in_process` | already there (the proven target shape)                            |
| worktree `wt_in/out`   | `in_process` | HATS-1269 — scripts only; declaration fold is ADR-0019's HATS-1146 |
| `git_hooks`            | `detached`   | HATS-1337 — the D3 orchestrator                                    |
| `checks:` (future)     | `in_process` | ADR-0019 D9 — own snapshot root, never a provider's                |

`in_process` for `runtime_hooks` means the **provider's per-session tree**,
not the installed package: a session must execute the same bytes from start
to finish, and the installed-package path goes stale under a mid-session
`self update` (venv-per-version) while a session's settings live for the
whole session. Two `in_process` roots — the provider session tree for
runtime hooks, ai-hats's own `checks/` snapshot (ADR-0019 D9) — are correct,
not a smell: runtime hooks are surface-specific by nature; checks fire on
rack with no provider involved.

One known obstacle for the `runtime_hooks` half: today the materializer
copies each script and rewrites the mode to `0o755`, which is masking at
least one `100644` hook in the shipped library (ADR-0019 D6). `in_process`
resolves the path fresh instead of copying, so that mask disappears and the
mode has to be correct at rest before the retrofit lands.

The police retire with the copies: three managed directories, three
manifests, the sweep, `_assert_manifest_intact`, the runtime drift arm, the
leak detector, and the carry backstop the worktree sweep forced (HATS-833's
`_drop_unbacked_carry_rows`). A card that removes a copy and leaves its
watchdog behind has done half the job.

## Consequences

**Gained.** One taxonomy instead of four imitated copies; sibling data files
survive by construction (`bundle: dir` is free); the whole
copy-drift-police-heal bug class dissolves rather than gaining another
watchdog; a refusing hook can say why; a hook cannot hang a transition
holding the task lock; `self update` stops being a re-materialization event
for anything but nothing — the orchestrator survives it by content.

**Cost, named.** Fail-open means: ai-hats removed or broken ⇒ git gates
silently off (one warning line). That is the supervisor-chosen posture — the
alternative demonstrably wedges human commits with an unusable remedy. Live
resolution also means the gate set at commit time can differ from the set at
compose time; the copies "fixed" that at the price of the entire drift class
above.

**Risk.** This unifies channels that today are independent — exactly the
condition where per-channel tests stay green while composite behaviour
changes (HATS-1113: five days of every agent role disabled behind a green
suite). Verification is chain-level or it is nothing: the composed-chain
harness (`tests/e2e/_helpers/hook_chain.py`) and real-commit e2e, per
channel AND in composition.

## References

- [1] `docs/adr/0019-declarative-lifecycle-extension-model.md` — the
  declarative extension model this substrate carries: point catalog, binding
  policy (`on_error`), check-point env vocabulary (D5), the `checks/`
  snapshot root (D9), and the migration ordering (D8).
- [2] `docs/adr/0018-unified-artifact-builder.md` — provider-side
  materialization: the artifact builder, the clean-root invariant, and the
  per-session trees `in_process` runtime hooks resolve into.
- [3] `docs/adr/0013-wt-core-extraction-boundary.md` D5 — the `wt_hooks`
  carry persisted into worktree state JSON, the shape HATS-1269's compat
  shim must keep replaying.
- [4] `src/ai_hats/templates/githooks/dispatcher.sh` — the current
  dispatcher template D3 rebuilds (stdin fan-out and previous-hooks chaining
  are behaviours to keep; the manifest backstop is not).
