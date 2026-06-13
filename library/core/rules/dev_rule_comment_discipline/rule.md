# Rule: Comment Discipline

Default to **no comment**. Good names make the next line obvious; a comment is
only for the WHY the code can't show — and then it's **one line**. Learn the
boundary from the examples; match them.

> In the examples, `TASK-NNN` is a placeholder for a ticket id. Use whatever
> prefix *this* repository uses (`PROX-`, `ACME-`, `JIRA-…`); never write the
> literal string `TASK-NNN`.

## When NOT to write a comment — the common case (delete / skip)

```python
i += 1                       # ❌ "increment i"  — restates the code
total = price * qty          # ❌ "compute total" — restates the code

# ----- helpers -----        # ❌ decorative banner — delete
# load(cfg) -> dict          # ❌ docstring restating the signature — delete

# legacy path, kept for ref:
# rows = old_load(x)         # ❌ commented-out code — git remembers it; delete

# TODO: clean this up         # ❌ ownerless TODO — delete, or pin it:
# TODO(TASK-712): drop after the v0.8 migration lands
```

## When a comment earns its place — ONE line, the WHY

Each ✅ is paired with the weak version it replaces.

```python
# ❌ acquire the lock
# ✅ fcntl flock auto-releases on process death — no stale-lock cleanup
lock = FileLock(path)

# ❌ lock around the worktree add
# ✅ git does NOT serialize `worktree add` across processes (TASK-479)
with _create_lock():
    ...

# ❌ re-check the branch
# ✅ caller must already hold _create_lock — TOCTOU re-check, not a public entry
def _load_under_lock(branch): ...
```

## History is a pointer, never a retelling

```python
# ❌ four lines retelling the task + a number that rots:
# TASK-452: framework-invariant reminder for any agent that may
# touch composition / pipeline / runtime internals. Short body
# (~600 chars); acceptable budget for an always-on architectural
# guard. Full rationale: docs/adr/0005-*.md.
"rule_composition_value_contract",

# ✅ pointer + link; no retelling, no stale-able count:
"rule_composition_value_contract",  # TASK-452: always-on; see docs/adr/0005
```

That `~600 chars` is now **1690 (2.8×)** — the count rotted, the pointer wouldn't
have. Long rationale → ADR / task card, linked by id — not pasted. A module
docstring states the contract in a few lines, not the design history.

## The 4-question test — run it before typing any comment

1. Does the code already say this?            → **delete.**
2. Is it *what changed* / task history?       → **`# TASK-NNN` pointer**, not prose.
3. Is it a count/claim that can go stale?     → **omit** ("~600 chars", "only caller", "always").
4. Left with a non-obvious WHY in ≤1 line?    → **keep it.**

## Source

HATS-752 / HATS-754 (supervisor request, follow-up to the HATS-698 validation
audit: findings 2a-F9 worktree.py module-docstring essay, 1-F3 providers.py
stale rule-budget paragraph). Few-shot / contrastive shape per the HATS-638 canon.
