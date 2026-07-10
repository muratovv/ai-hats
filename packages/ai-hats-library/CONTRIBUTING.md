# Contributing to ai-hats-library

`ai-hats-library` is developed in the [ai-hats](https://github.com/muratovv/ai-hats)
monorepo under `packages/ai-hats-library/`. Follow the repository-root contributing
guide for setup, branch, and commit conventions. Routine skill/role authoring is
owned by the `role-curator` role.

## The rules specific to this package

1. **Data only.** `ai_hats_library` ships content — no framework runtime code. The
   `__init__.py` is a thin shim (it exposes `LIBRARY_SCHEMA_VERSION` and makes the
   content resolvable via `importlib.resources`); it imports **only** the standard
   library. Enforced by `tests/test_library_boundary.py`.
2. **Keep `SKILL.md` pure.** Every skill stays valid drop-in Agent-Skill format —
   the pure keys (`name` / `description` / `allowed-tools` / …) are untouched, and
   all ai-hats composition metadata lives under the single namespaced `ai_hats:`
   frontmatter key. A non-ai-hats agent ignores that key. Enforced by
   `tests/test_skill_md_purity.py` (in the integrator suite).
3. **Bump the schema, not the world.** A breaking change to the frontmatter /
   composition / resolver contract is a `schema_version` bump in `manifest.yaml`
   (and a coordinated ai-hats `SUPPORTED_LIBRARY_SCHEMA` bump). Content-only
   additions are MINOR; fixes are PATCH — no schema bump.

## Tests

```sh
pytest packages/ai-hats-library/tests
```

`test_library_standalone.py` proves `importlib.resources.files("ai_hats_library")`
resolves to a real directory with `core/`+`usage/`+`hooks/` and round-trips a
`SKILL.md` read through `as_file`; `test_library_boundary.py` is the static
import-boundary guard (stdlib-only).
