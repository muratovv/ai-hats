---
name: claim-evidence-check
description: Claims table for a text — every checkable statement gets a source you saw or an honest mark, and every link is checked by script. Use when a draft states facts, numbers, dates, names, versions, tool behaviour or quotes; before a text is handed back or published; or when asked whether a text is accurate.
license: MIT
---

# Claim–Evidence Check

Decompose a text into atomic claims and give each one a source or an honest
mark — the SAFE / FActScore pattern, applied by hand.

## When to Use

- The text asserts something about the world: a number, a date, a name, a
  version, what a tool does, a quote, a comparison. Opinions and instructions
  the author owns are not claims.
- Alongside `prose-review` at review time; before publishing; whenever the
  author asks "is this right?".
- Reading the source is the check. A search snippet that agrees is not a
  source; the page or file that says it is.

## Procedure

1. **Atomize.** List every checkable statement as one self-contained
   sentence — one fact per row, names spelled out, no pronouns. A sentence
   carrying two facts becomes two rows.
2. **Source each row.** The URL, file path, command output or document that
   shows the fact, plus what you saw there in one line. Where the text already
   cites something, open the citation and confirm it says that.
3. **Mark honestly.** `verified` — you saw it this time; `unverified` — no
   source found or not checked; `contradicted` — the source says otherwise,
   quoted. A contradicted claim is reported, never fixed on the quiet: the
   author decides.
4. **Check the links.** Run the checker that ships with this skill —
   `scripts/check_links.py <file>`, resolved against **this skill's own
   directory**, not the project's `scripts/`. It prints every link with its
   status and exits non-zero on a dead one. `UNREACHABLE` is the network, not
   the link — report it, do not count it as dead.
5. **Report** the table and the link-check output. The text stays untouched.

## Output

| # | Claim                                             | Source                   | Seen                | Status     |
| - | ------------------------------------------------- | ------------------------ | ------------------- | ---------- |
| 1 | The CLI's default timeout is 30 seconds           | `src/cli/main.py:41`     | `timeout: int = 30` | verified   |
| 2 | SAFE agrees with human annotators 72% of the time | arxiv.org/abs/2403.18802 | "72% of the time"   | verified   |
| 3 | The linter ships a Russian style package          | —                        | —                   | unverified |

## Completion

- Every checkable statement is a row; every row has a status; no
  `contradicted` row was rewritten without the author.
- The link check ran; its output is in the report.
- Validation — RED: a draft with three facts, one false, comes back "the
  facts look right". GREEN: the false one is a `contradicted` or
  `unverified` row with the source quoted beside it.

## Anti-Patterns

- A status from memory — `verified` is what you saw in the source this time.
- Softening a contradicted claim in the text instead of reporting it.
- A row bundling two facts — one can hold and the other not.
- Counting `UNREACHABLE` as dead, or a 403 from a bot filter as a fact check.
- Running `scripts/check_links.py` from the project root and reporting "no
  such file" — that path is the project's `scripts/`, not this skill's.
