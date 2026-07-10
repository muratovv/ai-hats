# ai-hats-library

The **agent library** for [ai-hats](https://github.com/muratovv/ai-hats) — the
portable content the framework composes into role prompts: **skills**, **roles**,
**traits**, and **rules**, plus the runtime **hooks** they ship. It is a
**data-only** package (YAML + Markdown + a few hook scripts); it holds no
framework runtime code and has **zero dependencies**.

## Two ways to use it

**1. Drop-in for any Claude Code user (no ai-hats, no pip).** The skills are in
the standard Claude Code Agent-Skill format (`SKILL.md` frontmatter). Clone this
package and copy the ones you want into `~/.claude/skills/`:

```sh
git clone https://github.com/muratovv/ai-hats
cp -r ai-hats/packages/ai-hats-library/src/ai_hats_library/core/skills/systematic-debugging ~/.claude/skills/
```

The ai-hats composition metadata rides in a **namespaced `ai_hats:` frontmatter
key** that non-ai-hats agents simply ignore — so every `SKILL.md` stays drop-in.

**2. As the content dependency of ai-hats.** `ai-hats` pins this package
(`ai-hats-library>=…`) and resolves its built-in library layer from it via
`importlib.resources`. The precedence stack layers user overrides on top,
last-wins: `built-in (this package) < ~/.ai-hats < project libraries`.

## Layout

```
src/ai_hats_library/
  core/     # engine fundament — universal skills, base traits, global rules, pipelines
  usage/    # opinionated catalog — domain roles, dev::/env:: traits, opt-in skills
  hooks/    # runtime hook sources materialized into consuming projects
```

`core/` and `usage/` are the two library **layers** (core first = lowest
priority). Local development against ai-hats uses the `AI_HATS_LIBRARY_ROOT`
environment override pointed at a checkout of this package.

## Versioning

Semantic versioning on the **format schema** (skill frontmatter + composition
schema + resolver expectations), decoupled from ai-hats's own version. The shipped
`schema_version` (in `manifest.yaml`) is the contract; ai-hats pins a compatible
range and fails loud on a library newer than it understands. MAJOR = breaking
schema change, MINOR = skills/roles added, PATCH = content fix.

## License

MIT — see `LICENSE`. Third-party-derived skills carry their own upstream
attribution in-tree (`metadata.yaml` `upstream:` blocks + per-skill `LICENSE`
files); see the repository-root `THIRD_PARTY_NOTICES`.
