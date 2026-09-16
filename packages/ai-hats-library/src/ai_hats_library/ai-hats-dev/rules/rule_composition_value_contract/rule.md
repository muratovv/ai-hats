# Rule: Composition & Pipeline Value Contract

Four invariants govern composition flow; breaching any drops a role or trait
injection from the prompt, silently. Full rationale:
`docs/adr/0005-composition-and-pipeline-value-contract.md`.

1. **Immutable.** `CompositionResult` / `ResolvedComponent` are frozen — copy
   via `with_*` methods, never field reassignment or re-composing the same
   `(role, overlays)` twice.
2. **HITL vs Automate split.** `WrapRunner` (HITL) has **no** prompt-override
   channel (composition reaches it via `plan_session`); `SubAgentRunner`
   (Automate) takes an explicit prompt. Don't re-add `system_prompt_override`
   to `WrapRunner.run`.
3. **Funnel.** `obj is None` ≡ `key not in ctx` (None filtered at merge). `""`,
   `0`, `False`, `[]` are valid non-absent values — for "no value" emit `None`
   or omit the key, never a magic `""`.
4. **Audit separate.** The composition record reaches `Session.init_audit`
   from the session record the plan projects (`composition_record`), not from
   a data-producing pipeline step.
