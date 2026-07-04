# ADR-0005: Composition & pipeline value contract

## Status

Accepted (HATS-452, 2026-05-23).

## Context

HATS-452 was a high-priority bug: under the default `ai-hats` invocation (no `--role`, `active_role: maintainer` in `ai-hats.yaml`), the system prompt written to `--system-prompt-file` contained PRIORITIES + RULES + AVAILABLE SKILLS but was **missing the merged role/trait injection** — hundreds of lines of `ai-hats-maintainer`, `trait-agent`, `dev::python`, `dev::shell`, role-own behavioral guidance silently absent from the agent. The default maintainer session was running on the generic agent baseline, not the maintainer-specific gear.

Mechanical root cause was two bugs interacting:

1. `pipeline/steps/compose.py` returned `{"system_prompt": ""}` when `role` was None — empty string used as an "absent" marker. The accompanying comment ("Empty system_prompt downstream means the runner falls through to the no-override path") described an intent the downstream consumer did not honour.
2. `runtime.py:WrapRunner.run_session` used `if system_prompt_override is not None:` — `""` is not None, so the guard fired. The body then replaced the freshly-composed 16 250-character `injections` list with `[""]`. The provider's `build_system_prompt` then filtered the empty merged injection out of the prompt entirely. Result: PRIORITIES → RULES with no role/trait content between them.

The architectural smell behind these mechanics: **composition was computed twice** — once in `ComposeRole` (a pipeline step, for funnel logging), once inside `WrapRunner.run_session` (for actual prompt construction) — and the two layers communicated through a stringly-typed `Optional[str]` override channel whose "empty" value carried different meaning to producer (compose_role: "I had nothing to compose") and consumer (runtime: "the caller wants me to override with this exact text"). This is a **class** of bug — any future producer who emits `""` to signal "absent" through a similar nullable channel reproduces it.

## Decision

Four principles govern composition and pipeline value semantics across the framework. The fix lands at four layers (mechanism > convention > reminder > test) — partial delivery leaves the class of bug recurrent.

### П1 — Composition is an immutable first-class object

`CompositionResult` and `ResolvedComponent` are `@dataclass(frozen=True)`. Field reassignment is forbidden. Transformations that derive a *modified* result go through explicit `with_*` methods (`with_injection_override(text) -> CompositionResult`) that return new instances. Re-composing the same `(role, overlays)` pair in a second layer to obtain a "modified" variant is forbidden — that's re-derivation of the same logical entity in two places.

### П2 — HITL vs Automate as the primary runtime-API axis

`WrapRunner` (HITL, PTY-proxied — a human types at the keyboard) **has no `system_prompt_override` channel**. Role composition reaches the agent through `composer.compose(...)` + `build_session_prompt` inside `run_session`. Prompt injection is meaningless in HITL; the Optional override was the literal trap that caused HATS-452.

`SubAgentRunner` (Automate, subprocess — the HATS-267 use case) accepts an explicit prompt via the existing parameter. Sub-agents that need a caller-supplied prompt go through this surface.

### П3 — Pipeline funnel value contract

Producer-emits / consumer-may-ignore funnel semantics stay — they enable custom pipelines. But values in the funnel are unambiguous:

- `obj is None` and `key not in ctx` are identical (the framework enforces this at the producer-side merge boundary in `Pipeline._run_steps` by filtering `None` values out of every step's delta).
- `""` (and other falsy values: `0`, `False`, `[]`) are valid non-absent values whose semantics differ from "absent". A consumer that needs "is the value present" checks `if v is not None`, not `if v`. A producer that wants to signal "no value" emits `None` or omits the key.

Custom-pipeline authors inherit this contract for free — no per-step boilerplate.

### П4 — Audit/visibility lives in a separate channel

Composition snapshots for session audit (`_composition_snapshot` → `Session.init_audit`) are emitted by dedicated audit infrastructure, not as a side effect of a data-producing pipeline step. A producer step does not piggyback composition data into its `produces` set so a downstream consumer can route on it.

## Consequences

**Code surface changes**

- `CompositionResult` / `ResolvedComponent`: frozen; new method `CompositionResult.with_injection_override(text)`.
- `WrapRunner.run`: `system_prompt_override` parameter removed; runtime no longer applies overrides for HITL sessions.
- `SubAgentRunner.run` / `_run_attempt`: unchanged contract; still accepts `system_prompt_override` for the HATS-267 path. Now uses the typed `with_injection_override` method instead of `dataclasses.replace`.
- `Pipeline._run_steps`: `state.update(delta)` filtered — `None`-valued keys are dropped at merge.
- `ComposeRole.run(role=None)`: returns `{}` (key omitted) instead of `{"system_prompt": ""}`.
- `LaunchProvider.run`: interactive branch no longer forwards `system_prompt` to `WrapRunner`.

**New artifacts**

- `library/core/rules/rule_composition_value_contract/` — agent-facing reminder; attached to `trait-agent` and added to `ALWAYS_ON_RULES` so the rule body materializes in every agent session prompt (~600-char budget).
- `tests/test_composer_immutable.py` — П1 invariants.
- `tests/test_wraprunner_signature.py` — П2 invariants.
- `tests/pipeline/test_funnel_value_contract.py` — П3 invariant.
- `tests/e2e/test_session_prompt_contains_role_injection.py` — the HATS-452 regression itself; turns red on a revert of any of the above mechanical changes.

**What does NOT change**

- Sub-agent path (HATS-267 prompt injection) — `SubAgentRunner.run` keeps `system_prompt_override`. The bug was on the HITL side; Automate side is the legitimate consumer.
- Public CLI surface — `ai-hats execute --role X`, `ai-hats` (bare) — behavior is unchanged from the user's POV. The fix restores intended behavior; nothing visible breaks.
- Gemini provider — `GeminiProvider.build_system_prompt` has identical structure; correctness follows automatically once composition + funnel are corrected upstream.
- `compose_role` step — still exists, still funnel-producing. We did not delete it.

## Alternatives considered

**V1 (audit-only): turn `compose_role` into an audit-snapshot step, leave the override channel alone.**
Closes this instance of the bug but leaves П2 unsatisfied — the `Optional[str]` override pattern on `WrapRunner.run` is still there for a future call-site to misuse. Rejected: doesn't prevent the class.

**V3 (sentinel-type override): keep dual composition, replace `Optional[str]` with `NoOverride | UseText(text)` algebraic type.**
Self-documenting at the type level; mechanical bug fixed. But П1 stays unsatisfied (composition computed twice) and the architectural duplication that made the Optional necessary remains. Rejected: pastes a label on the smell rather than removing it.

**Chosen — full four-layer fix.** Mechanism prevents the bug class at the type / API / framework-behavior layer; convention (this ADR) documents intent; reminder (new rule) keeps agents aware; test (e2e + three unit guards) catches regressions early.

## Phase 2 — П1-meta closure (HATS-456, 2026-05-23)

П1 above forbids *re-composition* of the same `(role, overlays)` pair in two layers — that's re-derivation of the same logical entity. Phase 1 (the HATS-452 fix) closed the **acute** instance: composition was no longer computed twice across `ComposeRole` step and `WrapRunner.run_session`.

But the same logical operation — `composer.compose(role, overlays=assembler._get_overlays(role))` — was still inlined at multiple sites:

- `WrapRunner.run_session` (HITL)
- `SubAgentRunner._run_attempt` (Automate, with `with_injection_override` after the compose)
- `Assembler.set_role` (on-disk write for providers without a scaffold template)
- `MaterializeSystemPrompt.run` (preview, the `ai-hats config show-prompt` surface)
- Several compose-only sites in `Assembler` (init / set_default_role / status / bump / tier2 lookup / mirror-dir setup)
- One site in `cli/maintenance.py` (composition snapshot for self-update)

The sites were *accidentally* aligned — they all spelled the call the same way — but the alignment was a coincidence of code review, not a structural guarantee. A future change adding an extra overlay (or skipping `_get_overlays`) in any single site would silently produce a different composition for that path, reproducing the П1-meta problem at the catalog level.

**Phase-2 closure.** New module `src/ai_hats/materialize.py` exposes one function — `compose_for_role(assembler, role) -> CompositionResult` — which is the sole place in `src/ai_hats/` where the with-overlays compose call appears. Every consumer above now routes through it. A grep-style guard (`tests/test_no_direct_compose_outside_facade.py`) makes future drift fail at test time.

The build surface stays runtime-specific per П2: `WrapRunner` builds session argv+env+materialized-text via `build_session_prompt` (3-tuple since HATS-523 — the third element is the exact bytes the provider sees as system-prompt override, persisted by the caller via `Session.save_meta_prompt` to `<session_dir>/meta_prompt.txt` for post-hoc audit, symmetric with the Automate path), `SubAgentRunner` builds a sub-agent meta-prompt via `_build_meta_prompt`, `MaterializeSystemPrompt` builds preview text via `build_system_prompt`, `Assembler.set_role` builds the on-disk file via `build_system_prompt` + `expand_path_placeholders`. The facade does not collapse these — only the compose primitive is unified.

One pattern was intentionally **not** migrated to the facade:

- `cli/reflect.py` — `compose(target_role)` *without* overlays (reflect a role in isolation, showing the library's built-in composition for inspection).

This has a different semantic from "compose role X for this project" and would change behavior if force-fitted onto the facade. The Phase-2 drift guard's regex narrowed accordingly: it matches the `overlays=` form only.

> **HATS-501 retrospective (2026-05-25):** `pipeline/steps/compose.py` was *also* on this list as "audit-only, П4" — that classification was wrong. The step's funnel output (`system_prompt`) was a *production* role-delivery value consumed by `LaunchProvider` on the sub-agent path, which fed it into `SubAgentRunner.run` as `system_prompt_override`. The no-overlay form silently dropped global + project overlay content from the SDK system_prompt (HATS-501). HATS-501 routed the step through the facade; HATS-505 then removed the redundant pipeline-side override pass-through entirely (override channel reserved for explicit HATS-267 callers — see Phase-3 below).

### Phase 3 — pipeline-scoped drift guard + override-channel discipline (HATS-505)

The Phase-2 drift guard caught the *with-overlays* drift outside the facade. It missed the *no-overlays* drift inside the pipeline subtree — exactly the shape HATS-501 took. HATS-505 adds a second guard test, `test_no_direct_compose_inside_pipeline_subtree`, that flags any `composer.compose(...)` call (with or without `overlays=`) inside `src/ai_hats/pipeline/`. The whitelist is a `dict[Path, str]` requiring a justification per entry; empty by design today. The `cli/reflect.py` exception lives outside `pipeline/` and is not affected.

HATS-505 also tightened П2's Automate-side reading. The override channel on `SubAgentRunner.run` is reserved for **explicit caller use** — HATS-267 sub-agent callers (future direct API consumers). The pipeline does **not** pre-fill it: the runner's own `compose_for_role(self.assembler, role_name)` call applies overlays. A pipeline-side pre-fill is, at best, a redundant re-composition; at worst (HATS-501 shape) a partial composition that silently replaces the runner's correctly-composed `injections` list via `with_injection_override`. A warning comment at the runtime call site tells future HATS-267 callers to *augment*, not *replace*.

> **Superseded in part (HATS-865, Phase 5 below).** "The runner's own
> `compose_for_role` call applies overlays" is no longer how delivery works —
> runners no longer compose at all. The override-channel discipline itself
> stands unchanged: the pipeline still never pre-fills
> `system_prompt_override`, and `with_injection_override` remains the sole
> transform, applied by `SubAgentRunner` on the injected payload's result.

**Out of scope (deferred):** a `materialize_system_prompt(asm, role, provider) -> str` helper for callers that need only the agent-visible text was proposed in the plan (F1) and removed during execution — every real consumer needs the intermediate `CompositionResult` for hooks install / audit snapshot / stats / override. Re-introduce when a real text-only consumer appears.

### Phase 4 — silent-key sibling (HATS-515)

П3 names *silent-None*: emitting `""` to mean "absent" is a trap because
consumer guards on `is not None`. HATS-515 surfaced the sibling class —
*silent-key*: pydantic's default `extra="ignore"` on `_YamlModel` (the
common base for all YAML round-trippable models) means a typo'd key in
a YAML dict is dropped at parse time, never reaches the Python object,
and never raises. The exact instance: `composition.hooks.sesion_start:`
(typo) in a role's `config.yaml` was silently discarded; the hook never
fired and no diagnostic surfaced. `composer._merge_hooks` reinforced the
silence by iterating a hardcoded 6-string event tuple parallel to
`LifecycleEvent` — even if the key had survived parse, the merge loop
would not have seen it.

**Closure.** `HooksConfig` gets a `@model_validator(mode="before")` that
diffs incoming dict keys against `LifecycleEvent` and raises
`ValueError("unknown hook event(s): <list>; allowed: ...")`. The default
`extra="ignore"` on `_YamlModel` is **not** flipped — many subclasses
(`ComponentConfig`, `RuleMetadata`, `OverlayConfig`, `SkillMetadata`,
`TaskCard`-extras path) legitimately tolerate or round-trip extras.
Validation lives at the boundary where the silent drop hurts.
`_merge_hooks` is refactored to iterate `LifecycleEvent` directly, so a
future event added to the enum auto-propagates to the merge loop and
the two stop drifting.

> **Superseded in part (HATS-707).** The `hooks:` composition channel this
> phase hardened — `HooksConfig`, `LifecycleEvent`, `_merge_hooks`,
> `CompositionResult.hooks` — was later found to have **zero** runtime
> execution consumers and was deleted. The silent-key *invariant* below stands
> as a general value-contract principle; only its `HooksConfig` instantiation
> is gone.

**Invariant added to the contract.** When a model's field set is the
*authoritative* enumeration of allowed keys for a YAML block (not a
schema-evolution surface for round-trip extras), the model must override
the base `extra="ignore"` with explicit validation — either
`model_config = ConfigDict(extra="forbid")` or a `mode="before"`
validator that produces a domain-clear error. The silent-key class
recurs anywhere a YAML dict feeds a model that *intends* to enumerate
all valid keys but inherits the permissive default.

## Phase 5 — composition inversion: compose once at the integrator (HATS-865)

Phases 1–3 stopped the pipeline from *delivering* a competing composition, but
the bricks still *derived* their own: `ComposeRole` composed for the funnel
(observability), then `WrapRunner` / `SubAgentRunner` composed again for
delivery — two derivations of the same `(role, overlays)` per launch, and 23
upward imports of the composition layer from runtime bricks (ADR-0014
Composition rule violation). HATS-865 inverts the direction:

- **One compose seam.** `composition_seam.build_composition_payload` (an
  integrator module) resolves the effective role, validates explicit roles
  (`RoleNotFoundError` moved here from the `compose_role` step), performs the
  HITL first-run `set_role` side effect, composes ONCE via the facade,
  precomputes the audit snapshot (the private-Assembler walk left
  `runtime_common`), and resolves the provider — returning a frozen
  **`CompositionPayload`** (see glossary).
- **Funnel seeding.** Integrator CLI callers seed the payload into
  `PipelineHarness.run(initial=...)` under the `composition` key.
  `ComposeRole` is now a pure projection (`result.merged_injection` →
  `system_prompt`); `MaterializeSystemPrompt` renders the seeded payload; the
  `provider` step hands the SAME object to the runner — the funnel object IS
  the runner object (identity pinned in `tests/test_pipeline_human_yaml.py`).
- **П1 sharpened.** No second composition of the same `(role, overlays)` for
  prompt delivery per execution path. `Assembler.set_role` still composes
  internally for its on-disk write and `HooksManager`'s result-less resync
  edge may compose (carve-out #2) — neither is prompt delivery.
- **Recorded behavior delta.** `SubAgentRunner` retry attempts share the ONE
  payload composition (previously each `_run_attempt` re-composed). This is
  deliberate — attempts are now comparable like-for-like.
- **Recorded delta #2 — funnel surface.** The funnel now carries
  `system_prompt` on default-role runs too (`ComposeRole` projects the
  always-present seeded payload; pre-865 it returned `{}` without an explicit
  `role`). No step consumes it — observability/trace surface only.
- **Recorded delta #3 — `set_role` timing.** The HITL first-run `set_role`
  side effect fires at payload-build time, before `PipelineHarness.run`
  (pre-865: mid-`WrapRunner.run`). A launch dying in a pre-launch step now
  leaves `active_role` persisted; accepted — the write is idempotent and a
  retried launch lands the same value.
- **Carve-outs.** Two data-dependent edges keep late binding: the
  `compute_usage` static-cost analyzer (role known only at run time; threaded
  runner → finalize initial state as a callable built at the seam) and
  `HooksManager.compose` for genuinely result-less resync edges.

Enforcement is deny-by-default: `tests/test_import_hygiene.py::`
`test_composition_layer_is_integrator_only` forbids any module outside the
explicit ALLOWED set from referencing the composition layer at any level.

## Related

- HATS-294 — per-session cache + override mechanism (introduced the HATS-267 channel that was later misused).
- HATS-267 — sub-agent custom prompt (legitimate use of the override channel, on the Automate path).
- HATS-442 — record role composition snapshot per session (existing audit-side surface that П4 preserves).
- HATS-456 — П1-meta closure: the materialization facade described in Phase 2 above.
- HATS-501 — Automate-path regression of the П1-meta class (`pipeline/steps/compose.py` was direct-composing without overlays; routed through the facade).
- HATS-505 — Phase-3 closure: pipeline-scoped no-overlay drift guard + override-channel discipline (pipeline no longer pre-fills `system_prompt_override`).
- HATS-515 — Phase-4 closure: silent-key sibling; `HooksConfig` validates lifecycle event keys at parse, `_merge_hooks` derives event list from `LifecycleEvent`.
- HATS-506 — umbrella epic for role-delivery harness contracts (sister to HATS-499 which owns library / content side).
- HATS-523 — П4 application: HITL audit-persistence symmetry. `WrapRunner` now saves the materialized system prompt to `<session_dir>/meta_prompt.txt` (already done by `SubAgentRunner`). `Provider.build_session_prompt` extended to 3-tuple to surface the bytes through to the runner. Contracts П1–П4 unchanged.
- HATS-865 — Phase-5 closure: composition inverted; the integrator composes once (`composition_seam`), the `CompositionPayload` is funnel-seeded and injected into runners; bricks never import the composition layer (deny-by-default lint).
- ADR-0001 / ADR-0002 — pipeline / step contracts. П3 is a refinement of the existing funnel semantics, not a new mechanism.
