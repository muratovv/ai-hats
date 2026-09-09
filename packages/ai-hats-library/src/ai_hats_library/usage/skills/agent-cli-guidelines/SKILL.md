---
name: agent-cli-guidelines
description: Design a CLI, gate or hook whose reader is another agent. Use when building or changing one, and when a tool of yours was ignored, misread, or worked around.
license: MIT
---

# Agent CLI Guidelines

Your reader is an agent mid-task. Give it the next step where it is standing.

## When to Use

Use this when you are about to write or change what a tool SAYS: a gate's
refusal, a hook's warning, an error path, an exit code. Use it again when a tool
of yours was ignored or worked around — that is a defect in the tool's
interface, not in the reader.

Skip it for output a human reads at leisure. Colour, alignment and progress bars
are not the subject here.

## Procedure

### 1. Stand where the other agent is standing

You are not writing for yourself, and not for a person reading a manual. You are
writing for an agent that is **mid-task**, has just been interrupted by your
tool, and wants to get back to the task. Before writing a line, answer three
questions as that agent:

- **What did I just try to do, and what does this tell me about it?** Not the
  tool's internal state — mine.
- **What do I do next?** One concrete move, not a class of moves.
- **If I do nothing, what happens to my task?** Blocked, degraded, or fine.

An agent that cannot answer these from your output will guess, and its guess is
whatever it was already doing.

### 2. The principle: context at the point of decision

Everything below is one rule applied in different places. **Put what the reader
needs where the decision is made** — not in a doc, not in a wiki, not in the
tool's own `--help` two commands away. The reader has your output and the
terminal; treat anything further away as unavailable.

Applied:

- a gate broke → the exact command that runs the part that broke;
- a non-obvious exit code → what it means, in the message, not in a table
  elsewhere;
- a refusal → the sanctioned route, spelled out, next to the refusal;
- a check that could not run → say so where the result is read, not in a log
  nobody opens.

Worked pairs, good and bad, with the reason each way: `examples/`.

### 3. Print nothing the reader cannot use

Your output lands in another agent's context window and stays there. A progress
bar, per-test dots, a banner, a re-print of the command just run — each costs the
reader capacity and returns nothing, and the more of it there is, the further the
part that matters drifts from where it will be seen.

- **Ornament is for a tty.** Progress indicators, spinners and colour exist for a
  human watching in real time. Emit them when the output is a terminal; a
  non-interactive caller gets the result.
- **Both ends are read; the middle is not.** Put the verdict first and the one
  command last — the two positions that survive a long message. Nothing that
  matters belongs in between, which is also the reason there should not be much
  in between.

Before shipping, read your own output whole and ask what each line buys the
reader. A line that does not change what the reader does next is a line you are
charging them for.

### 4. Fire only when you should

The failure this project records is not a terse refusal — blocking guards are
honoured routinely. It is a guard that fires on what it should not: one that
flags the very command it recommends, or warns on every call of a tool that is
fine. That teaches the reader to stop reading it, and the guard that gets
scrolled past is unavailable for the real error it was built for.

A false positive is not cosmetic. It spends the reader's attention, which is the
resource every other rule here depends on.

### 5. Every remedy must run AS PRINTED

The recurring defect is not a missing remedy — it is one that does not work.
Shapes that cost real sessions here: a hint naming a verb the CLI does not have;
a refusal advising a flag the command never accepted; a recipe so incomplete
that following it exactly still failed; a guard instructing a form it then
refused; a message claiming state it had not left behind.

Run your own remedy, from the state that triggers it, before shipping the
message. A remedy is executable content and rots like code, not like prose.

### 6. The exit code is the contract

- Return the **runner's own** status, never a filter's or a wrapper's.
- A run that produced no artifact must not exit 0 because the failure was
  "expected" — a green code above a "nothing was written" line is a
  contradiction the reader resolves in favour of the code.
- A gate that cannot see the thing it judges must not report passing. A vacuous
  green is worse than red: it is consumed as evidence.
- Fail-open is a decision: when a check cannot run, say so where the result is
  read and journal it — and keep the journal free of your own test traffic, or
  it stops being readable as evidence.

### 7. Say the invariant, not the catalogue

A refusal that enumerates forbidden spellings teaches the reader to find an
unlisted one, and the unlisted ones outnumber the listed. Name the property that
makes the action wrong; the list is illustration.

### 8. Keep one home for the policy

The message the tool prints IS the documentation of its rule. Restating it in
prose elsewhere gives it two homes and the copy drifts — and when they conflict,
skill prose has been observed to win over the tool's own correct deny text.
Point at the tool; let it speak.

## Completion

- The three questions in step 1 are answerable from the output alone.
- Every line was justified: the verdict is first, the command is last, and
  nothing between them is ornament.
- The guard fires only on what it means to catch; negative cases were checked.
- Every printed command was executed from the failing state, and worked.
- The exit code matches the body, and a check that could not run says so.
- The rule has one home, and it is this output.

**Validation scenario (RED).** A guard ships with a matcher that also catches a
legitimate spelling. It fires on nearly every session, correctly most times and
wrongly often enough to be noise; the agent learns to scroll past it. Weeks
later the same guard reports a real defect, in a correctly worded and actionable
message, and that message is scrolled past too. Nothing about the wording was
wrong — the reading had already been spent.

## Anti-Patterns

- Writing for the tool's author instead of for an agent mid-task.
- Spending the reader's context on progress output nobody is watching.
- Tuning the wording of a guard that fires when it should not.
- A refusal that explains why and not what to do.
- A remedy nobody ran: a verb, flag, or path that does not exist.
- Exit 0 on a run whose body says nothing was produced, or on a check that could
  not see its subject.
- The tool's rule restated in prose that will drift from it.
