# Rule: Unroll the Claims You Author

Prose you write into a doc, an injection, or a `SKILL.md` carries claims a
reader will act on and no test will check. A claim whose truth is **settled by
unrolling it** — expanding a pattern, walking the branches, counting the
matches — must be unrolled **before** you commit it. Which kind of claim it is
does not matter; that it is checkable and unchecked does.

## The kinds — common spellings, not the boundary

| Kind           | What you wrote                                         | How to unroll it                                                            |
| -------------- | ------------------------------------------------------ | --------------------------------------------------------------------------- |
| **Name**       | `` `judge-protocol` ``, `library/core/`, `--isolation` | grep the source / run `--help` — does it resolve *now*, in *this* checkout? |
| **Glob**       | `judge-*-protocol`, `**/hooks/**`                      | expand it and read the match list — is the set the one you meant?           |
| **Quantifier** | "verdicts auto-persist", "every role composes X"       | hunt the branch that breaks it; one counterexample kills it                 |
| **Count**      | "3 skills", "~600 chars", "the only caller"            | count it, or drop the number                                                |

## The foil to cut

```text
❌ `judge-*-protocol` — written for 3 skills, matches 2: `judge-protocol`
   has no second hyphen
❌ "verdicts auto-persist into validation_log" — false on the
   HarnessReliabilityError branch
❌ `library/core/` + `library/usage/` in a role injection — the library
   moved under packages/, the paths kept pointing at a directory that
   no longer exists (the `prose-refs` CI stage now refuses this one;
   the three kinds below it still have no machine)
❌ a count you recalled instead of counting
```

One question the table cannot ask for you: **would anything go red if this line
rotted?** If no, it is load-bearing prose — unroll it now, or delete it.

## Scope

Any prose you author that a reader will act on — `docs/`, `README`,
`CONTRIBUTING`, **role and trait injections**, and `SKILL.md`. Code comments
sit under `dev_rule_comment_discipline`: same principle, narrower surface
(it forbids a stale-able count outright rather than asking you to verify it).

Source: HATS-1430. The invariant is the claim's checkability, not the lexical
shape it happened to take. The `prose-refs` CI stage now holds the name and the
path; the glob, the quantifier and the count are still yours.
