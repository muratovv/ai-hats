# Rule: Comment Discipline

A comment or docstring carries the one thing the code can't — the **WHY** — in the
fewest lines that hold it. Default to none; when you do write one, match the shape
below.

## The shape to write

```python
# ✅ inline — one line, the non-obvious WHY:
lock = FileLock(path)  # flock auto-releases on PID death — no stale cleanup


# ✅ function docstring — one line of intent; add args/returns ONLY when the
#    signature doesn't already say them:
def materialize_runtime_hooks(session):
    """Write the session's runtime hooks; return the paths written."""


# ✅ module docstring — one line stating the contract, not its design history.

# ✅ a decision whose reason outlived the diff — cite the record, not the ticket:
("rule_composition_value_contract",)  # always-on; see docs/adr/0005
```

## The foil to cut

```python
# ❌ a 4-line note restating a DI-wiring assignment   → delete; the code says it
# ❌ a multi-paragraph docstring retelling the task    → one line of intent
# ❌ a ticket id — `# PROJ-123`, `TODO(PROJ-123)`     → the reader has no tracker
# ❌ a TODO — with an id it leaks that tracker, without one it has no owner
# ❌ "compute total" / decorative banners / commented-out code
# ❌ a stale-able count ("~600 chars", "only caller")  → omit
```

## Before typing, ask

1. Does the code already say this? → delete.
2. *What changed*, or task history? → omit, ticket id included. `git log -S`
   finds the commit for any reader of the repo; a tracker id does not.
3. A count or claim that can rot? → omit.
4. Left with a non-obvious WHY in ≤1 line? → keep it.

Long rationale → an ADR, cited by number: it ships inside the repository, so the
citation resolves for whoever reads the code, and `adr-integrity` refuses it once
it stops resolving. Never paste the rationale into the source.
