# CLI output, paired

Each pair is the same event, twice. Read the **why** — the good version is not
longer, it is aimed at a reader who has to act.

---

## 1. A gate refuses

**Bad**

```
review-gate: FAILED (3 stages)
```

**Good**

```
review-gate: tree 5d6d1b9e (HEAD) has not earned every stage this gate requires.

Missing:
    lint
    unit

Run the gate on that exact content, then retry:

    scripts/gates.sh run lint unit
```

**Why.** The bad one is true and unusable: which three, and how does the reader
earn them? The good one names the subject (which tree — so the reader learns a
stale-tree failure is even possible), names what is missing, and ends with a line
that can be copied. The command goes LAST because a reader arriving at the tail
sees the last line first.

---

## 2. A non-obvious exit code

**Bad**

```
$ ai-hats list skills
$ echo $?
124
```

**Good**

```
$ ai-hats list skills
list skills: no output after 60s — the library scan is slow on a cold cache,
not hung. Re-run with a longer bound, or narrow it:

    timeout 240 ai-hats list skills

$ echo $?
124
```

**Why.** `124` is `timeout`'s code, and an agent that has not met it reads it as
a crash and starts debugging the wrong thing. The tool knows what its own slow
path is; saying so at the point of failure costs one line and saves a diagnosis.

---

## 3. A guard refuses an operation

**Bad**

```
Refused: direct tracker writes are not permitted. See the backlog discipline
rule for the rationale and the list of sanctioned operations.
```

**Good**

```
Unknown link kind 'related' on the 'hypotheses' backlog: configured kinds are
source_task, supersedes, superseded_by — 'related' is a kind of the 'tasks'
backlog, which this id does not route to.
```

**Why.** The bad one is a citation with a policy attached: the reader must find
a document, read it, and map it back. The good one — a real refusal from this
project — states the invariant (kinds are per-backlog), enumerates what IS
available here, and explains the reader's actual mistake (right verb, wrong
backlog). Nothing is left to fetch.

---

## 4. A check could not run

**Bad**

```
$ pre-commit
[ok] privacy-check
```

*(the hook's dependency was missing; it exited 0 without checking anything)*

**Good**

```
$ pre-commit
[skipped] privacy-check — `rg` not found on PATH, nothing was scanned.
          Install it or set PRIVACY_CHECK_REQUIRED=1 to make this fatal.
          Recorded in .git/ai-hats/fail-open.log
```

**Why.** The bad one is the worst failure in this file: a green that will be
quoted as evidence. Fail-open is a legitimate design choice; fail-open in
silence is not, because it removes the difference between "checked and clean"
and "did not check". Say it where the result is read, and journal it.

---

## 5. A guard that fires when it should not

**Bad**

```
[hygiene] raw `pytest` call — prefer the project runner
```

*(printed for `rack transition --log "...make done-gate rc=0"`, where the runner
name appears inside a quoted argument)*

**Good**

```
(silence)
```

**Why.** The good output for a non-event is nothing. A guard that fires on the
command it recommends, or on a name inside a string, spends the reader's
attention on a non-problem — and the same guard's real refusal is then scrolled
past. Precision is not a nicety here; it is what keeps the channel worth reading.

---

## 6. A test run reports back

**Bad**

```
$ project-test
running 6157 tests
............................................................ [  1%]
............................................................ [  2%]
        … 96 more lines of dots …
F........................................................... [ 99%]
============================== FAILURES ==============================
        … 60 lines of traceback …
1 failed, 6156 passed in 41.02s
```

**Good**

```
$ project-test
FAILED: 1 of 6157 in 41.0s

    tests/test_gate.py::test_refusal_names_missing_stages
    AssertionError: refusal did not name the missing stage 'unit'

Full output: /tmp/project-test-8f2a.log

Re-run just this one:

    project-test tests/test_gate.py::test_refusal_names_missing_stages
```

**Why.** The dots are a progress indicator, and nobody is watching. They exist
for a human at a terminal; delivered to an agent they are a hundred lines of
context spent to say "still working", and they push the one line that matters
away from both ends of the message, which are the parts that get read. The
traceback is not deleted — it is moved to a file the reader can open **if** the
summary is not enough, which it usually is.

Note the two ends doing their jobs: the verdict opens the message so it survives
any truncation, and the runnable command closes it so it is the last thing seen.
Between them sits only what identifies the failure. Ask of every other line what
it changes about the reader's next move; if the answer is nothing, it is being
charged to their context for free.
