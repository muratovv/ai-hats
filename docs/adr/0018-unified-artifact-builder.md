# ADR-0018: Unified Session Artifact Builder & Clean-Root Invariant

## Status

Accepted (ratified via HATS-1170 execution 2026-07-24).

Linchpin decision record for Epic HATS-1165 (*Provider Context & Harness Filtering*).

> **Указатель (2026-08-03, HATS-1465).** Консолидированная карта материализации
> поверхностей — корни хранения, точки записи, интеграция хуков, кэш, очистка —
> живёт в `docs/adr/0021-surface-materialization.md`; этот ADR остаётся
> контрактом artifact-builder'а. При расхождении описаний builder-слоя истина
> здесь; при расхождении карты — там.

## Context

Provider runtime artifacts (system prompt overrides, plugin skills, event hooks, and provider settings) were historically materialized through fragmented, per-provider ad-hoc code paths:

1. **Working Tree Contamination**: Framework materialization wrote scaffold files (`./CLAUDE.md`), injected managed hooks directly into project-root `.claude/settings.json`, and mutated project files.
2. **Double-Delivery Defects**: Splicing hand-written root files into session prompt overrides resulted in prompt duplication (e.g., HATS-704 double-delivery of `CLAUDE.md` content).
3. **Execution Path Parity Gap**: HITL (`WrapRunner`) and Automate (`SubAgentRunner` / SDK) prepared session inputs via separate logic, causing inconsistent hook firing and environment variables between interactive and headless runs (HATS-975, HATS-1091).

To scale across multiple surfaces (Claude, Agy, Cline) and support declarative filtering (HATS-1167), ai-hats requires a single, unified seam for assembling and delivering session artifacts across both execution paths.

## Decision

### 1. Unified Artifact-Builder Abstraction (`session_artifacts.py`)

All provider surfaces implement a standardized assembly seam:

```python
class ArtifactCategory(str, Enum):
    CONTEXT = "context"
    SKILLS = "skills"
    HOOKS = "hooks"
    SETTINGS = "settings"

class DeliveryMode(str, Enum):
    CACHE_FLAG = "cache_flag"
    SDK_OPTION = "sdk_option"
    NATIVE_ROOT = "native_root"
    INLINE = "inline"

class RunMode(str, Enum):
    HITL = "hitl"
    AUTOMATE = "automate"

@dataclass(frozen=True)
class SessionPolicy:
    context: bool = True
    hooks: bool = True
    settings: bool = True

@dataclass
class BuiltArtifacts:
    cli_args: list[str] = field(default_factory=list)
    extra_env: dict[str, str] = field(default_factory=dict)
    sdk_options: dict = field(default_factory=dict)
    materialized: list[Path] = field(default_factory=list)
    full_content: str | None = None
    port: Materializer = field(default_factory=ApplyMaterializer)
    policy: SessionPolicy = field(default_factory=SessionPolicy)
```

A surface implements one method per **(category, run mode)** pair, named
`_build_<category>_<run_mode>`; the base dispatches to it. A surface therefore
never branches on the run mode — HITL delivery (launch flags) and AUTOMATE
delivery (inline text / SDK options) are different jobs that happen to share a
category name, and mixing them in one method is what let cline's interactive `-i`
end up inside a context handler (HATS-1207). A pair a surface does not deliver is
an **absent method**, which is visible, rather than an `else` that falls through
in silence — `agy` has no `_build_hooks_automate` manifest write, and that gap is
now legible in the class body (HATS-1223).

> **Пример устарел (2026-08-02, HATS-1465, замерено).** Дыра agy выше с тех
> пор закрыта: `AgyProvider` доставляет хуки и в AUTOMATE
> (`_build_hooks_automate` в
> `packages/surfaces/agy/src/ai_hats_agy/provider.py`). Сам принцип
> absent-method в силе; пример дерево больше не описывает.

```python
class Provider(abc.ABC):
    # implemented per surface, e.g.:
    def _build_context_hitl(self, project_dir, result, session_id, artifacts) -> None: ...
    def _build_context_automate(self, project_dir, result, session_id, artifacts) -> None: ...

    def build_category_artifact(
        self,
        category: ArtifactCategory,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        *,
        run_mode: RunMode,
        artifacts: BuiltArtifacts,
    ) -> None:
        # Dispatches to _build_<category>_<run_mode>; absent pair = no delivery.
        ...

    def build_session_artifacts(
        self,
        project_dir: Path,
        result: CompositionResult,
        session_id: str,
        *,
        run_mode: RunMode | str = RunMode.HITL,
        policy: SessionPolicy | None = None,
        artifacts: BuiltArtifacts,
    ) -> BuiltArtifacts:
        # Stamps artifacts.policy, then loops over ArtifactCategory and
        # delegates each enabled one to build_category_artifact
        ...

    def handles_artifact_categories(self) -> bool: ...
```

**Why the policy rides `BuiltArtifacts` rather than a `build_category_artifact`
parameter** (HATS-1207 R1/R4): `ai_hats.providers` is a published entry point, so
adding a parameter to a category handler would raise `TypeError` at launch for any
out-of-tree surface implementing the pre-1207 signature — and a default value does
not help, because the caller passes the argument explicitly. Threading the policy
on the object the handler already receives changes no published signature.

The caller owns `artifacts` and therefore its materialization port: passing one
that carries a `PlanMaterializer` turns the whole build into a dry-run (HATS-1211).

A surface that predates this seam overrides only `build_session_prompt`; since
`build_category_artifact` defaults to a no-op, routing it through the builder
would silently deliver an *empty* session rather than fail. `handles_artifact_categories()`
detects that case so the runner can fall back to the legacy entry point, and warn
when a non-default policy cannot be honoured there.

### 2. Clean-Root Invariant

1. **Zero Framework Pollution in Project Root**: No ai-hats runtime files, scaffolds, role/skills mirrors, or managed settings entries are written into the user's project root directory (specifically none of `.agy/`, `.agents/`, `.cline/`, `.gemini/`). A clean `git status` alone is a necessary but insufficient condition — gitignored residual framework directories left on disk are an explicit violation.
2. **Per-Session Cache Isolation**: All ephemeral artifacts generated by ai-hats are stored strictly within `<ai_hats_dir>/.cache/sessions/<session_id>/` and cleaned automatically upon session termination.
3. **User File Immunity**: User-owned files (e.g., a hand-written `./CLAUDE.md` or user permissions in `.claude/settings.json`) are categorized as `native_root` and remain completely untouched.

Clause 2's location was superseded by **HATS-1398**: the per-session cache is unchanged as a concept but no longer sits in the workspace — it resolves to `<cache_root>/sessions/<session_id>/`, outside the project (default `~/.cache/ai-hats/<project-key>/`), which strengthens clause 1 rather than weakening it.

### 2.1 Wiring vs Installation Boundary (HATS-1207 R3)

A critical distinction governs hook lifecycle:

- **Session Wiring**: Per-session hook activation emitted by the builder (`<cache>/settings.json` + `--settings` for Claude, `<cache>/hooks.json` + global dispatcher for Agy). This is gated by `SessionPolicy(hooks=False)`.
- **Project Installation**: Long-lived project setup performed by `sync_hooks` (copying runtime scripts to `<ai_hats_dir>/library/hooks/`, installing worktree hooks in `library/wt-hooks/`, installing git hooks in `.githooks/`). This installation state is shared across sessions and human runs; a `SessionPolicy` gates session wiring ONLY and must NEVER uninstall shared project installation state.

> **Расхождение, датировано 2026-08-14 (HATS-1655).** Граница «wiring vs
> installation» в силе; изменился её *installation*-берег. Ни одного из трёх
> перечисленных выше артефактов больше нет: `library/hooks/` снесена
> (HATS-1480), `library/wt-hooks/` — (HATS-1269), а сам `sync_hooks` ретайрен
> вместе с ними — у `HooksManager` такого метода уже нет, имя выживает только в
> названиях тестов и в комментариях. От installation-берега осталась одна
> поверхность: по-событийные диспетчеры в `.githooks/` + `core.hooksPath`
> (`install_git_hooks`, HATS-1337). Карта состояния на сегодня —
> ADR-0021 S3/S6.

### 2.2 Core Vocabulary vs Surface Implementation (HATS-1217)

The builder's modules were repeatedly re-litigated as "is this a shared contract
or one surface's private way of putting bytes on disk?" — a question taste cannot
settle. HATS-1211 moved `plugin_dir`'s materialization half to
`surfaces/claude/` on that basis; HATS-1217 asked the same of four more modules
and found the question has a mechanical answer.

**The movability test.** A module belongs to a surface if and only if **no
provider-agnostic core module imports it.** ADR-0014 §1 [1] places surfaces as
*"The first consumer tier above the integrator"* — a surface depends **up** on
`ai_hats`, never the reverse. So a core module importing `ai_hats.surfaces.*`
inverts the tier, and a core importer is therefore proof the module is shared
vocabulary rather than one surface's implementation.

Applying it to the HATS-1211 module set:

| Module                                                              | Provider-agnostic core importers                                                                     | Verdict                            |
| ------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- | ---------------------------------- |
| `materialization.py` (the port, plan, `describe_*`)                 | `session_artifacts`, `session_report`, `dry_run`                                                     | core contract                      |
| `session_artifacts.py` (this section's vocabulary)                  | `providers` (the `Provider` ABC), `wrap_runner`, `dry_run`, `subagent_runner`, `composition_payload` | core contract                      |
| `session_report.py`                                                 | `dry_run`, `subagent_runner`, `wrap_runner` — and no surface importer at all                         | core contract                      |
| `skills_dir.py` — the PATH half (skills' `scripts/`+`bin/` on PATH) | `subagent_runner`, `wrap_runner` (+ all three surfaces)                                              | core contract                      |
| `skills_dir.materialize_skills_dir` — the copier half               | none — only surfaces call it                                                                         | movable, not yet moved (HATS-1271) |

The last row is the honest one. By the test `materialize_skills_dir` belongs to
the surface layer, and it has simply not been moved: HATS-1271 first converges
it with cline's byte-for-byte equivalent, and the converged function is what
gets a home. A verdict table that reported "core contract" here — bending the
rule to match today's tree — would be worth nothing. The test earns its keep
precisely by returning answers the tree has not caught up with.

> **Расхождение, датировано 2026-08-14 (HATS-1655).** Половина этого плана
> исполнена, половина нет, и порознь. Схождение состоялось: HATS-1271 закрыта,
> и `skills_dir.materialize_skills_dir` теперь зовут **обе** поверхности —
> agy (`_materialize_skills`, `_build_skills_automate`) и cline
> (`_deliver_skills`), — то есть «one surface calls it» верно уже не считается,
> а вердикт по тесту от этого не меняется: провайдер-агностичных импортёров у
> копира по-прежнему ноль. Дом сошедшейся функции так и не найден — она
> осталась в `src/ai_hats/skills_dir.py`, и три пути назначения тоже остались
> врозь (ADR-0019 D9). Владельца у переезда сейчас нет.

Two structural consequences — the first applied by HATS-1217, the second by
HATS-1248:

1. **The port owes nothing to a surface.** `materialization.py` took its tree
   hash from a private helper of `plugin_dir.py` — the legacy claude-mirror
   sweep. The hash is now the stdlib leaf `fs_digest.py`, so the chokepoint has
   no edge into surface-specific cleanup code.
2. **A per-session artifact needs no cross-session reconciliation.** The skills
   copier was a ref-counted rebuild behind a filelock, guarding one session's
   skills from another's sweep. Its target is itself keyed by session id, so the
   guard could only ever engage when two processes minted the *same* id — and
   then they shared one ref slot and overwrote each other regardless. HATS-1248
   made session ids unique per process and deleted the machinery. The general
   rule: reconcile inside a session-scoped artifact only if two sessions can
   reach it, and if they can, fix the identity rather than the artifact.

`ai_hats.surfaces.*` is not a published extension point — only the
`ai_hats.providers` entry-point group is (`pyproject.toml`). Moving a module
between `ai_hats` and `ai_hats.surfaces` therefore changes no external contract.

### 3. Claude Reference Implementation

Claude is established as the primary reference surface:

- **Context (`context`)**: Prompt injection is composed into `<cache>/sessions/<sid>/prompt.md`. Delivered via `--system-prompt-file` for HITL and `system_prompt` for Automate SDK. Root `./CLAUDE.md` scaffold generation and double-delivery prompt splicing are permanently eliminated.
- **Skills (`skills`)**: Materialized into `<cache>/sessions/<sid>/plugin/` and delivered via `--plugin-dir` (HITL) and `plugins` (Automate SDK).
- **Hooks (`hooks`)**: Framework-managed hooks (including `pre_bash_shared_state_guard.sh` and skill runtime hooks) are written into `<cache>/sessions/<sid>/settings.json`. Delivered additively via `--settings` for HITL and `settings` with `setting_sources=[]` for Automate SDK. S1 PoC verified that `--settings` merges additively with user-owned settings without overwriting root permissions.
- **Subagent Env Parity (HATS-975)**: `SubAgentRunner` explicitly merges `provider.get_env()` into sub-agent process environments, guaranteeing environment parity with `WrapRunner`.

### 4. Headless Hook Enforcement

Verification in S4 confirmed tool-hook behavior in headless environments. Where underlying CLI execution skips tool-hook invocation in headless `-p` mode, `SurfaceGuard` (HATS-1105) serves as the harness-level enforcement boundary.

### 5. AGY Surface Architecture & Global Hook Dispatcher (HATS-1190)

Spike HATS-1190 established that the third-party binary `agy` (v1.1.6) hardcodes relative `.gemini` directory resolution within the active workspace root (`google3/third_party/jetski/fs/local/local.GeminiDir`), and exposes no `--settings` or `--config` CLI flags for settings path relocation. Overriding `$HOME` invalidates user OAuth credentials (`~/.gemini/antigravity-cli/mcp_oauth_tokens.json`).

To maintain the **Clean-Root Invariant** without mutating `<project_root>/.gemini/settings.json`, AGY adopts the **Global Hook Dispatcher** pattern:

1. **Global Hook Registration**: `ai-hats self init` registers a single, static dispatcher script (`ai-hats-hook-dispatcher`) in global `~/.gemini/antigravity-cli/settings.json`.
2. **Session-Scoped Routing**: `ai-hats-hook-dispatcher` inspects `AI_HATS_SESSION_ID`. If absent (standalone `agy` run by user), it immediately exits 0 (no-op). If present (`ai-hats` runner), it loads session hooks from `<cache>/sessions/<sid>/hooks.json`.
3. **Context Delivery**: Rules and prompt context are delivered cleanly via `--add-dir <cache>/rules` without polluting the project root.

> **Поправка (2026-08-02, HATS-1465, замерено).** «at `self init`» пункта 1
> не описывает код: регистрация выполняется на **каждой сборке сессии** —
> `_deliver_hooks` вызывает `ensure_global_dispatcher_hook`
> (`packages/surfaces/agy/src/ai_hats_agy/provider.py`), незалоченный
> read-modify-write файла настроек в `$HOME` (тело
> `ensure_global_dispatcher_hook` в `global_hook.py`, только идемпотентный
> short-circuit). Call-site из `self init` не существует.
> Защита этой записи от гонок — HATS-1338. Карта ярусов материализации живёт
> в ADR-0021 [2], но утверждение о per-build каденции там не подкреплено ни
> одним маркером, поэтому ссылка намеренно голая: голую цитату проверка
> целостности корпуса не резолвит. Содержание — за HATS-1652.

## Consequences

- **Pristine Project Trees**: Running ai-hats sessions leaves zero framework role or session materialization directories in the project root (specifically none of `.agy/`, `.agents/`, `.cline/`, `.gemini/`). Clean `git status` alone is a necessary but insufficient condition (gitignored residual framework directories are a violation).
- **Architectural Uniformity**: Both HITL interactive sessions and Automate SDK sub-agents rely on identical artifact assembly logic.
- **Foundation for Multi-Surface Expansion**: Unblocks remaining surfaces (Agy in HATS-1166, Cline in HATS-1171) and schema filtering (HATS-1167).
- **A Settled Placement Rule**: §2.2's movability test replaces taste with an import check, so "core or surface?" is answerable without re-opening the debate per module.

## References

- [2] `docs/adr/0021-surface-materialization.md` — карта материализации
  поверхностей (ярусы, чокпойнты, кэш, очистка) и требования M1–M15, куда
  ADR-0020 отсылает за ярусами (врезка-указатель в его шапке: «полная карта
  материализации поверхностей … живут в **ADR-0021 [5]**»). Таблицы с per-build
  каденцией в ней нет — HATS-1652.
- [1] `docs/adr/0014-composable-component-decomposition.md` §1 — the three-tier
  dependency model and the HATS-956 amendment adding the surface tier
  (*"depend UP on the integrator"*, *"The first consumer tier above the
  integrator"*). Enforced by `tests/test_import_hygiene.py` (intra-package) and
  `tests/test_workspace_boundaries.py` (cross-package).
