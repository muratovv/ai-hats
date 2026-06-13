# Rule: Comment Discipline

A comment is code you can't compile-check — it drifts silently and the reader
trusts it anyway. So a comment must earn its place, and a wrong comment is worse
than none.

## What a comment is for

1. **WHY-now, not what.** A comment earns its place only by explaining what the
   code cannot say itself: a non-obvious invariant, a trap, a contract, a reason
   the obvious approach was rejected. Keep it to 1–3 lines. Never narrate what
   the next line plainly does.
2. **History is a pointer, not a retelling.** Provenance is a bare task-ID
   reference (`# HATS-596: short-circuit on empty overlay`), not a paragraph
   recounting what the task changed. The retelling lives in the task card and in
   `git blame` / `git log` — link, don't paste.
3. **Long rationale lives outside the code.** Multi-paragraph design narrative
   belongs in an ADR (`docs/adr/`) or the task card, referenced by id/path. A
   module/class docstring states the contract and usage, not the design essay.
4. **No stale-able claims.** Do not write a comment you cannot keep true —
   especially a quantified one (byte counts, "always", "the only caller",
   benchmark numbers). The code changes; the number does not; the comment now
   lies. If you can't guarantee it stays true, omit it.

When in doubt, delete the comment and rename the thing it was explaining.

## Contrastive example

The pattern this rule exists to stop, drawn from a real drift
(`providers.py` `ALWAYS_ON_RULES`): a comment that narrates the task *and* asserts
a measured quantity. The quantity went stale — the claimed `~600 chars` is now
1690 (2.8×), so the comment actively misleads.

**Incorrect** — task retelling + a stale-able number (the rationalization:
"documenting the budget trade-off helps the next maintainer"):

```python
# HATS-452: framework-invariant reminder for any agent that may
# touch composition / pipeline / runtime internals. Short body
# (~600 chars); acceptable budget for an always-on architectural
# guard. Full rationale: docs/adr/0005-*.md.
"rule_composition_value_contract",
```

**Correct** — smallest fix to the same line: keep the pointer + the link, drop
the retelling and the number that can rot:

```python
"rule_composition_value_contract",  # HATS-452: always-on; see docs/adr/0005
```

The difference is visible in under five seconds, which is the bar: WHY-now +
pointer survives; narration and unverifiable quantities go.

## Scope

Binds code-writing roles (composed via `trait-se-mindset`). Applies to inline
comments and to module/class/function docstrings alike. It is **WHY-now-positive**
— it does not cap comment count or strip a genuine invariant-explaining comment;
it removes narration, archaeology, and stale-able claims.

## Source

HATS-752 (supervisor request, follow-up to the HATS-698 validation audit:
findings 2a-F9 worktree.py 185-line docstring essay, 1-F3 providers.py stale
rule-budget paragraph). Contrastive shape per the HATS-638 authoring canon.
