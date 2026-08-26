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


# ✅ a TODO — the one id that earns its place, because it points FORWARD at work
#    no commit holds yet, and names the card that retires it:
rows = fetch_all()  # TODO(PROJ-123): stream once the cursor API lands
```

## The foil to cut

```python
# ❌ a 4-line note restating a DI-wiring assignment   → delete; the code says it
# ❌ a multi-paragraph docstring retelling the task    → one line of intent
# ❌ an id as provenance — `# PROJ-123`, `Source: PROJ-123`, `(PROJ-123)` after
#    a sentence that already carries the WHY → the reader has no tracker
# ❌ an ownerless `TODO:` — no card means no one retires it
# ❌ "compute total" / decorative banners / commented-out code
# ❌ a stale-able count ("~600 chars", "only caller")  → omit
```

## Before typing, ask

1. Does the code already say this? → delete.
2. *What changed*, or task history? → omit, ticket id included. `git log -S`
   finds the commit for any reader of the repo; a tracker id does not. The one
   exception is a `TODO(<card>)`: no commit can hold work that has not happened.
3. A count or claim that can rot? → omit.
4. Left with a non-obvious WHY in ≤1 line? → keep it.

Long rationale → an ADR, cited by number: it ships inside the repository, so the
citation resolves for whoever reads the code, and `adr-integrity` refuses it once
it stops resolving. Never paste the rationale into the source.
