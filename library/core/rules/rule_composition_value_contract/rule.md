# Rule: Composition & Pipeline Value Contract

Four invariants that govern how composition flows through the framework.
A breach of any of these reproduces the class of bug that caused HATS-452
(role/trait injection silently absent from the agent's system prompt).

1. **Composition is immutable.** `CompositionResult` (and
   `ResolvedComponent`) are frozen dataclasses. Derive a modified copy
   via the explicit `with_*` methods (`with_injection_override(text)`),
   never via field reassignment and never by re-composing the same
   `(role, overlays)` pair in a second layer.

2. **HITL vs Automate, hard split.** `WrapRunner` is the HITL runner and
   has **no** prompt-override channel — role composition reaches the
   agent through `build_session_prompt`. `SubAgentRunner` is the
   Automate runner and accepts an explicit prompt (HATS-267). Do not
   re-introduce `system_prompt_override` on `WrapRunner.run`.

3. **Pipeline funnel value contract.** Producer-emits / consumer-may-
   ignore is by design. But values in the funnel are unambiguous:
   `obj is None` and `key not in ctx` are identical (the framework
   filters None at the merge boundary). `""`, `0`, `False`, `[]` are
   valid non-absent values whose semantics differ from "absent". If you
   need "no value", emit `None` or omit the key — never magic `""`.

4. **Audit lives in a separate channel.** Composition snapshots for
   logging / session audit are emitted by dedicated audit infrastructure
   (`_composition_snapshot` → `Session.init_audit`), not as a side
   effect of a data-producing pipeline step.

Full rationale + reproduction trace: `docs/adr/0005-composition-and-pipeline-value-contract.md`.
