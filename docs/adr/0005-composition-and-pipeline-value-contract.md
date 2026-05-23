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

## Related

- HATS-294 — per-session cache + override mechanism (introduced the HATS-267 channel that was later misused).
- HATS-267 — sub-agent custom prompt (legitimate use of the override channel, on the Automate path).
- HATS-442 — record role composition snapshot per session (existing audit-side surface that П4 preserves).
- ADR-0001 / ADR-0002 — pipeline / step contracts. П3 is a refinement of the existing funnel semantics, not a new mechanism.
