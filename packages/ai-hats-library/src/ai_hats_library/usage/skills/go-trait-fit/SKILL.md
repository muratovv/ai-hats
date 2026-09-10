---
name: go-trait-fit
description: Match a Go role's `dev::go-*` traits to what the module actually requires, read from `go.mod`. Use when picking or customizing a role in a repository whose root carries a `go.mod`.
license: MIT
---

# go-trait-fit

`go-dev` composes every Go domain, so the default is never under-equipped — a
project with `migrations/` and pgx can no longer end up with no database trait.
The price of that default is paid per project: a pure CLI tool still carries the
database, gRPC and samber descriptions in every turn.

`go.mod` is the one machine-readable statement of which domains a repository
actually touches. This skill turns it into a concrete `config customize` line.

## When to Use

- A role has just been chosen or is being customized, and the project root has a `go.mod`.
- Not for deciding *which* role to wear — that decision is made before this skill runs.
- No `go.mod` in the root: there is no evidence either way. Say nothing, keep the full role.

## Procedure

Run the script from the project root, naming the role the line should target:

```
python <this skill's directory>/scripts/suggest.py --role go-dev
```

It reads `./go.mod` (override with `--go-mod`), writes nothing, and prints one
ready-to-paste command. Show the output to the user **verbatim** and let them
decide — the removal is theirs to make, not yours.

The evidence rule the script applies, so you can explain a surprising result:

- Only **direct** requires count. A `// indirect` line means some dependency of
  a dependency pulled it in, which says nothing about this repository.
- A `replace` with no `require` behind it changes nothing in a Go build, so it
  is not evidence either.
- Module paths are prefix-matched, so `github.com/jackc/pgx/v5` satisfies `pgx`.

## What it deliberately does not judge

Four traits are checked: `dev::go-database`, `dev::go-grpc`, `dev::go-cli`,
`dev::go-samber`. The rest are never suggested for removal because `go.mod`
carries no honest signal for them — `log/slog` and benchmarks are stdlib, and
manual constructor injection is the recommended default for a small graph. An
absent dependency there would not mean an absent domain, and a wrong "drop this"
costs more than the tokens it saves.

## Completion

- The script's output has been shown to the user, unedited.
- Any removal was applied by the user's decision, through `ai-hats config customize`.
- No `go.mod`: the full role stands and nothing was asked.

## Anti-Patterns

- Running `config customize --remove-trait` on the user's behalf — the script prints, the human applies.
- Reading `go.mod` yourself and reasoning about the dependencies in prose — that is what the script exists to replace.
- Suggesting a trait outside the four checked ones because the module "looks like" it does not need it.
