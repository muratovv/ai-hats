# Rule: Comment Discipline

A comment or docstring carries the one thing the code can't — the **WHY** — in the
fewest lines that hold it. Default to none; when you do write one, match the shape
below. (`TICKET-NNN` = whatever ticket prefix *this* repo uses, never the literal.)

## The shape to write

```python
# ✅ inline — one line, the non-obvious WHY:
lock = FileLock(path)        # flock auto-releases on PID death — no stale cleanup

# ✅ function docstring — one line of intent; add args/returns ONLY when the
#    signature doesn't already say them:
def materialize_runtime_hooks(session):
    """Write the session's runtime hooks; return the paths written."""

# ✅ module docstring — one line stating the contract, not its design history.

# ✅ history — a pointer, never a retelling:
"rule_composition_value_contract",  # TICKET-452: always-on; see docs/adr/0005
```

## The foil to cut (the HATS-837 shape)

```python
# ❌ a 4-line note restating a DI-wiring assignment   → delete; the code says it
# ❌ a multi-paragraph docstring retelling the task    → one line of intent
# ❌ "compute total" / decorative banners / commented-out code / ownerless TODO
# ❌ a stale-able count ("~600 chars", "only caller")  → omit
```

## Before typing, ask

1. Does the code already say this? → delete.
2. Is it *what changed* / task history? → `# TICKET-NNN` pointer, not prose.
3. A count or claim that can rot? → omit.
4. Left with a non-obvious WHY in ≤1 line? → keep it.

Long rationale → ADR / task card, linked by id — never pasted into the source.
