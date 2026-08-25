---
name: interface-design
description: Shape a public contract — its fields, its types, its dependencies — for a reader who has none of your context. Use on any CRUD to a public contract (adding, changing or removing a field, a type, a method, an enum member), when a field is typed `object`, a bare `str` or a primitive standing in for a domain value, when a contract carries an identifier belonging to another subsystem, or when reviewing a diff that touches one.
license: MIT
---

# Interface Design

You write an interface from the inside — what you put there, how it works — and
it is read from the outside, by someone who knows neither. Every shape below is
one place that gap produced a defect in review.

## When to Use

Any CRUD on a public contract — a field or method added, retyped, renamed or
removed. Two neighbours:

- **api-evolution-checklist** — whether a change breaks existing callers. That is
  compatibility over time; this is the shape itself.
- **design-minimalism** — whether the primitive should exist at all. Settle that
  first; this skill assumes you decided it should.

## The shapes

### Start from the minimal need, and do not let callers grow it

Take the smallest surface the caller genuinely needs. If something can be
expressed with what is already there, that is the path: a new method duplicating
an existing capability is a second way to do one thing (below). Complex machinery
stays hidden behind the small surface, not published beside it.

The same rule from the other end: **a mechanism does not name its callers.** The
set of things it can run is its own; the set of *uses* is the application's.

```
❌  class StepId(Enum):          ✅  class StepId(Enum):
        LOAD, TRANSFORM,             LOAD, TRANSFORM, WRITE
        REPORT_NIGHTLY,          # the application keeps its own catalog:
        REPORT_WEEKLY            NIGHTLY = (LOAD, TRANSFORM, WRITE)
```

    application ──knows──▶ mechanism        mechanism ──must not know──▶ application

### Public data is expressed in types

A primitive on a public surface throws away what the value *is* and leaves the
reader to guess. The same move applies at three scales:

```
❌  def get_id() -> str          ✅  def get_id() -> UserId
❌  isolation: str = ""          ✅  isolation: Isolation      # closed set → enum
❌  Request(role, project_dir, composition, manager, tracer, interactive,
            prompt_path, model, isolation, ticket, tags, extra_args, target)
✅  Request(what_to_run: Materialized, where: Placement, how: HarnessParams)
```

Thirteen fields in a row are thirteen because that is what the one call site had
in scope. Ask, per field, **which reader consumes it**: the domain types fall out
of the answer, and a field whose reader you cannot name stands revealed as a
field nobody reads — the three such fields above belonged to one caller only, and
moved off the shared contract entirely.

Before borrowing a word from surrounding code, follow it to its use: a field
named for the ticket *system* while carrying a card id, typed `str` where it
needs two values, is two defects wearing one plausible name.

### A field's type comes from its writer

Read the code that *produces* the value, not the consumer you imagine. A field
typed `tuple[str, ...]` whose writer produces `dict[step, Exception]` throws the
exceptions away and keeps the names — and nothing goes red, because its only
reader is not written yet.

### An undefined type is declared once, in a shared home, with a card

```
❌  def run(tracer_factory: object): ...      # here
    TracerFactory = object                    # and again, three files away
    Tracer = object                           # and now: same thing or not?

✅  # types.py — the one home
    TracerFactory = object   # TODO(TICKET-NNN): real type once the tracer lands
```

Whoever does not read your file declares their own alias, and a third person
cannot tell whether the two names mean the same thing.

### An identifier of another subsystem is that subsystem's import

```
❌  run(ticket_id: str)           # the contract now depends on the backlog,
                                  # invisibly — nothing in the signature says so
✅  run(prompt_context: Context)  # a caller resolves the id; the contract takes
                                  # the result and stays unaware of its origin
```

    ❌  contract ──id──▶ [must reach into the other subsystem to mean anything]
    ✅  caller ──resolves──▶ contract ──takes the resolved value──▶ done

### Exactly one way to perform one action

Two spellings of the same read are a defect even when both work: the second is
what the next migration standardises on by accident — nobody chooses it, it is
simply the one in front of the agent doing the conversion. The fix is not
documenting which to prefer, it is deleting the choice: **lift**
the keys the contract answers for out of the raw bag instead of copying them
beside it, so a typed field has no raw twin. That form is testable: pin the typed
field list, and a field added without lifting its key is red.

### Say why the field exists

"Resolved once by the entry point and passed down" describes the process. "Every
path that touches a step hangs off it, and it is resolved once so nothing below
rediscovers it" says why the reader should care.

Never illustrate with something the contract does not know: "None for `init` and
`nightly-report`" re-imports the vocabulary the type was just cleared of.
Describe the **condition** — "the run launched no session" — not the instances.

## Before you commit the contract, ask

1. Smallest surface the caller needs — or did I publish machinery?
2. Does any name here belong to a caller rather than to this mechanism?
3. Can I name the reader of every field, and does that reader exist?
4. Every public value a type — or did a primitive stand in for a domain value?
5. Did I read each field's **writer** before choosing its type?
6. Exactly one way per action, and every undefined type declared once, in a
   shared home, with a card?
7. Checked against **all** call sites? "It will be typed when its consumer
   arrives" is not an answer — the next author builds against this now.

## Completion

The contract is judged on the **diff at real call sites**, not on a draft: wire
it into its callers and read what it costs them. A draft shows the shape; only
the diff shows the price. A full contract with every shape applied →
`references/worked-example.md`.

**Validation scenario (RED).** Twelve interface defects from four review rounds
on one contract: an enum naming its callers, thirteen fields in a row, `object`
re-aliased locally, a `str` mode, a foreign id, a typed field with a raw twin, a
field typed for a reader who did not exist yet. An agent without this skill wrote
all twelve and defended several. GREEN: each comes out in its ✅ form unprompted.

## Anti-Patterns

- Naming the principle instead of showing the shape — reciting SOLID does not
  stop the thirteen-field bag.
- Publishing a new method for a case an existing one already expresses.
- Answering a review note by documenting the second way instead of deleting it.
