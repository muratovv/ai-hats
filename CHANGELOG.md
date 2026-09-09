# Changelog

All notable changes to ai-hats are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versions are produced from git tags via `setuptools-scm`; everything
since the latest tag lives under **Unreleased** until the next release.

## [Unreleased]

### Fixed

- **A runtime hook whose script is not where it should be is reported, never dropped** (HATS-1862). Four of the five session manifest writers — claude, codex, cline, opencode — skipped a declared hook in silence when `resolve_skill_script` found no file, and agy wrote the command without looking; a skill that renamed or forgot to ship its script left the session running with that gate off and nothing but the harness's `No such file or directory` to say so, the end state HATS-1439 measured. All five now go through one `composed_rows` pass in `hook_collection`, which tells the two faults apart: a script missing from the skill itself, or shipped without the exec bit, is the author's to fix, so the row is left out and a warning naming the skill, the event and the file reaches `artifacts.notices` — the pre-launch banner in HITL, the sub-agent's sys trace in automate — and the session starts; a script present in the skill but absent from the session mirror is ai-hats's own write gone wrong, so the build stops with `RuntimeHookMirrorError` naming the mirror path instead of wiring a gate that cannot run. Whether the mirror holds the file is the port's to answer (`Materializer.executable_at`), so `--dry-run` reports exactly what the real build would. The claude wiring in `settings.json` is now built from the same rows as the manifest rather than a second pass. Test fixtures that wrote hook scripts without `chmod +x` had to gain it: a declared gate ships executable, which is what the library guard already demanded.

- **The read-only commands report the library of the checkout you stand in** (HATS-1911). Worktree isolation is the enforced default in this repo, so nearly every library edit happens inside one — and `config show-prompt` and `--dry-run` were the only two commands that composed the tree being edited. The whole `list` group and `config status`'s role tree keyed off the project, which for a linked worktree is the MAIN checkout: a role authored in a worktree came back `Error: Role '...' not found`, and an existing one reported a budget from a tree the author never touched. Both failures are silent and plausible — a missing role reads as broken YAML, and a foreign budget looks exactly like your own — and the `role-curator` injection asserted the opposite ("`config show-prompt` and friends key off cwd"), so an agent had no basis to doubt the number it recorded. Measured before the fix with a worktree-only sentinel behind two controls: pinning `AI_HATS_LIBRARY_ROOT` at the worktree made all eight probes report it, running from the main checkout made all eight report main, and the real run reported 2 worktree against 6 main. Two of the first probe patterns were themselves unsound — the `not found` text contains the role name, so it matched on both outcomes — which is the false green the controls exist to catch. `prefer_cwd` now rides the `Assembler`, so the read-only call sites ask for it instead of each rebuilding a path list. That also retires the `extra`-list workaround in `_project_context`, which appended a duplicate of the builtin and user-global layers after the project's; since resolution is last-wins, user-global outranked project-configured under `show-prompt` and `--dry-run`, and the preview layered unlike the session it previews. `cwd` is passed down the chain rather than resolved three frames below it, which is the exit `check_test_isolation` names for a `chdir` in a test. The prose in `docs/how-to-extend.md` and the `role-curator` injection also claimed resolution was "first-wins when searching for a component" — it is last-wins throughout, in one search, and `find_component_dir` returns the last match.

- **The worktree-library e2e watches its seam again** (HATS-1911). `test_worktree_library_edit_visible` had been red on master since the curator trait was folded into its role: it edited a trait that no longer carries `## LIBRARY CURATOR` and stopped at its own shape guard, so the HATS-1501 contract it exists to hold had been unwatched. It now edits the role injection where that prose lives.

- **The docs a human reads are judged by the same gate as library prose** (HATS-1907). `prose-refs` refuses a path that does not resolve, a `Class.member` that is not declared, and the pre-`packages/` library prefix — over the 144 prose files under the library, and nothing else. So when the library root moved under `packages/ai-hats-library/src/ai_hats_library/`, 68 references in `docs/`, `README.md` and `CONTRIBUTING.md` kept pointing at a directory that no longer existed and no gate ever looked. The corpus is now 170 files, and the first run over it reported 76 findings. Two escapes keep that honest. A **dated record** — `docs/adr/**`, `docs/migration-v*.md` — leaves the corpus entirely: it describes the tree of its own day, and a sweep of it turned a strikethrough row in ADR-0021 about a *removed* flat copy into a pointer at a live directory. A **living doc** keeps retired entries, which no file-level rule can express, so a line may carry `<!-- prose-refs: was -->` to say its references name what a thing used to be; a marker quoted as an example does not count, which is how the section documenting it was exempting itself. Membership is now read from the parsed class body rather than matched over the file: the old `def`-only pattern reported five live dataclass fields as defects, and the regex that fixed those accepted a signature parameter, a local annotation and an unquoted dict key as members. Parsing found one defect both had hidden — the retro banner is a module-level helper, not a method on `RunSessionEnd`. A layer is written bare (`core/`), a file carries the full path, and the three spellings that are a different thing — `<ai_hats_dir>/library/`, `<project>/libraries/`, `~/.ai-hats/` — are untouched.

- **Quorum autoclose runs at the end of a HITL session, for the first time** (HATS-1892). `quorum_autoclose` declared `requires={"layout"}` and defined `run(*, project_dir)`. The runner builds a step's kwargs strictly from its declaration, so `project_dir` was never passed and `layout` fell into `**_`: the step raised `TypeError` on every finalize, and `failure_policy = "continue"` swallowed it, so the sweep ADR-0009 describes had never closed a hypothesis while every session stayed green. The step now takes `layout` and derives `layout.root`, as its five siblings already do — `project_dir` was never a key `finalize-hitl` carried, so the other direction would have meant widening the finalize contract for one step. Nothing could see the defect: the step's own tests called `run(project_dir=...)`, the one call the projection cannot make, and so passed throughout. `build()` now refuses a step whose `run` cannot be called from its declaration — strictly against `requires`, since `optional` permits the key to be absent and a required parameter riding an absent key is the same `TypeError` one path over. It is a guard rather than only a test because `pipeline_steps/` and the `ai_hats.steps` entry-point group are open seams no test in this repository can reach; a registry sweep is the second contour, covering the four advertised ids no shipped pipeline wires. Zero false positives across all 23 registered step ids and all 12 shipped pipelines, and the builtin-pipeline load test is now parametrized by glob rather than four hardcoded names — `finalize-hitl` was not among them.

- **`ai-hats execute --interactive --model` launches instead of refusing** (HATS-1891). The inert-flag guard (HATS-1218) listed `--model` beside `--isolation`, `--ticket` and `--json` and refused it with *"batch-only — the interactive runner cannot act on it"*. That sentence was false, and the workaround disproved it daily: `WrapRunner.run` takes `extra_args`, and a hand-written `ai-hats -p claude --model fable` had always reached the provider and worked. The guard was accurate about the plumbing — nothing carried the model into the HITL branch — and wrong about the capability, which is HATS-1218's own accept-and-ignore class seen from the other side: refuse the knob you could have honoured. `--model` now rides `extra_args` ahead of the user's own arguments; the other three stay refused, and the test that proves the refusal is gone keeps `--isolation` as its positive control.

- **A fresh `ai-hats self init` reports "Initialized", and a yaml it cannot read refuses instead of crashing** (HATS-1883). The summary step decided "already initialized?" by looking for `ai-hats.yaml` after `bootstrap_project` had just written it, so every first init said "Re-initialized" and ran the re-init diagnostics; the answer is now decided once, before anything is written. And since the composition root reads the config to resolve the project, a re-init on a config from a newer ai-hats died with a traceback before the step that used to turn it into the exit-2 refusal — the root now raises that refusal itself.

- **The resident claude dispatcher runs its gates inside the session again** (HATS-1882). Since HATS-1874 the session's wrapper process holds one dispatcher open and answers every PreToolUse call over a socket. It was handed the session's environment and used it to find the manifest — but the gates it spawned inherited the wrapper's own `os.environ`, which carries no session envelope. Every gate reading `AI_HATS_SESSION_IDENTITY` was blind: `safety_gate.py` read "outside a session, nothing declared" and asked no consent question on `plan->execute`, `->done`, `wt merge` or `wt discard`; the runtime boundary that refuses a spelling around the consent wrapper (ADR-0030 D6) was open for the same reason, so `/abs/path/rack transition X done` would have moved a card into master with no question at all; and the bypass journal wrote an empty `session_id` on every line. What the human saw instead was the wrapper's refusal and an agent asking for `consent rack.transition 120` in chat, four cards in a row — the native dialog with its one-use ticket never fired, and Claude Code was not the reason: since 2.1.211 a hook's `ask` forces the prompt even in auto mode. `run_hook` now takes the environment its child inherits, `run_chain` and `dispatch` carry it as `hook_environ`, and the server hands its own in; every other surface spawns its dispatcher inside the session and hands nothing, so nothing changes there. The resident e2e now runs the shipped `safety_gate.py` through the socket: `ask` with the ticket on the declared move, `deny` on the spelling that skips the wrapper — a pair that was `''` before this.

- **A merge that waited for the base lock is judged against the base it finds there** (HATS-1881). The drift guard ran under the per-branch lifecycle lock and before the base-branch lock, so two `transition done` on cards sharing `main` could both pass it and both land — the second on a base its verification had never seen, which is the stale baseline the guard exists to refuse. On a fast box that is what happened every time; on the CI runner the second racer reached the check after the first had merged and was refused, so `test_wt_parallel_transition_done` had been red on master for two runs while passing locally seven of seven. `merge()` now asks the local half of the drift question again inside the base lock, next to the mid-merge guard that HATS-602 moved there for the same reason; the fetch is not repeated, since only a peer's merge can have moved the base in between. The loser of a parallel merge now refuses every time, with the rebase recipe it already printed on slow boxes, and the e2e expects exactly that: one lands, the other refuses and lands after `git rebase main`. `cleanup()`'s squash keeps its contract. `ai-hats-wt` goes to 0.6.2.

### Changed

- **One Go role, and a machine that says which traits this module does not need** (HATS-1931). `go-dev` and `go-dev-full` were a false choice offered at the moment a user knows least about the framework, and the framework itself pushed the wrong half of it: the wizard's Step 3 mapping read `go.mod` and recommended `go-dev`, which composed 3 of the 11 `dev::go-*` traits. A project with `migrations/` and pgx therefore ran with no `dev::go-database` at all, and the remedy printed in the role's own injection — "enable per project via role override" — was addressed to the agent, who cannot perform it. `go-dev` now composes every Go domain and `go-dev-full` is gone. Measured always-on cost (`config show-prompt` + skill descriptions, `len//4`): 12 439 -> 14 915 tokens, of which the Go part is 2 125 -> 4 572 across 33 skills; the skill bodies those descriptions point at (80 554 -> 119 710) load on demand and never enter a turn. Instead of a second, thinner role, the new `go-trait-fit` skill carries the trim: its `scripts/suggest.py` reads `go.mod` and prints one ready-to-paste `config customize <role> --remove-trait ...` line for each of the four domains the module requires nothing from — a `// indirect` line is not evidence, and neither is a `replace` with no `require` behind it. On a module carrying none of the four that returns 1 259 tokens, half the increase. It is a skill rather than a CLI command so that the engine never learns about Go, and so that the wizard's prompt does not either: Step 4 says to follow a stack-fit skill when Step 3 detected a stack, and names no stack itself — the Go instruction lives in the skill body, which loads only when it is wanted. With no `go.mod` nothing is suggested and the full role stands. A project still pinned to `go-dev-full` was the arm this exposed: role existence is checked ahead of composing only for an explicit `--role`, so a role read from `ai-hats.yaml` reached the composer, and its refusal was not in the friendly-error table — `ai-hats --dry-run` printed a traceback and exited 1. `CompositionIncompleteError` is registered there now, so any role that has left the library is named on stderr with `list roles` and `config set -r` as the way out, exit 2.

- **The behaviorist role can reach the top of the ladder it is told to prefer** (HATS-1909). The role for managing agent behavior composed the authoring craft and nothing else, so the mechanism ladder ranked automation first while the role carried no way to write a script — switching to it gave you the craft and took away the tools, which made `--add-trait skill-engineer` the better move and the role advice that named it in the same breath a competitor to itself. It now carries `dev::shell` and `dev::python`, and the sentence in `ai-hats-framework` names the role first. The craft's two ends moved into the trait that any project can compose: the entry (an observation is the trigger, and prose that was already tried and did not hold is not a candidate for rewording) and the exit — which did not exist anywhere portable. `show-prompt` appeared in exactly one component of the whole library, the ai-hats-only curator role, in the in-process form; a consumer arriving by the advertised one-command path got neither that nor any command at all. The procedure is now skill `composition-verification`, so it loads on trigger instead of sitting in every turn, and the curator's thirty-line section shrank to the one value that is checkout-specific. Writing this caught two of its own kind before they shipped: the exit first named `config show-prompt <role>`, a form that does not exist because `--role` is an option, found when the positive control returned zero and proved the search broken rather than the edit; and the generalized hypothesis protocol claimed `rack hyp` answers `unknown backlog` on a project without one, when the group is registered from mounted backlogs and the answer is `No such command 'hyp'`. `skill-template` now carries the hook contract rather than pointing at `docs/how-to-extend.md`, which ships with the repository and not the package. `library-change-hypothesis-protocol` moved to `usage/` and lost its repo paths, so filing the prediction with the check that would refute it is part of the craft everywhere, not only here. Review then asked for the two steps before verification, so the craft is now a chain of four: `prompt-authoring` asks whether the defect is curable by prose at all — an instruction absent from the prompt is a writing job, one present but never reached is a placement job, and one read and selectively applied has already refuted rewording — then `library-editing` puts the component in a root this project reads and wires it, persistently or for one session through the `-r "role + trait"` spec, which existed since the runtime-overlay work and was taught in three repository docs and in no component at all. A second review round added the half that choosing automation implies: `agent-cli-guidelines`, on how the machine you just built talks back — a refusal names what is missing and ends with one command that earns it, and that command is run from the failing state before shipping, because the recurring defect in this repository's own gates was never a missing remedy but a remedy that did not work: a verb the CLI lacked, a flag the command never took, an environment assignment prefixed onto the very command the guard inspects. The same round turned the prose skill the right way round: the first question is whether a machine can hold the invariant, prose is earned only by no automatable invariant or a named cost that loses, and a prompt that already failed is evidence for automating rather than for saying it louder. A third round put that claim against the project's own record — the last hundred sessions, every observation read — and the record refused it in that form: prose interventions in the window are confirmed seven times with no refutation, and the sharpest counterexample is a guardrail that had already failed live, was rewritten from an enumeration into the invariant behind it, and has held since. So the fork is no longer prose versus machine but FORBIDDING versus GUIDING: a prohibition works the automation first, because prose asks every turn and one turn answers no; guidance is prose's own job, and the three rewrites with a record — re-place it, replace a citation with content, turn an enumeration into its invariant — are the only moves that have worked on text that already failed once. The same pass cut three rules from `agent-cli-guidelines` that the record does not support, including the one it opened with, and replaced them with the failure the record does show: a guard that fires when it should not spends the reading that every other rule depends on.
- **The quality-gate skill is no longer named after one of its two carriers** (HATS-1902). `maintainer-quality-gate` was named when the maintainer role was the only road into it. It now comes from the `ai-hats-dev` trait, which `maintainer` and `role-curator` both compose, so the prefix named one of the two — and the layer is already said by the directory, which is why none of its siblings there carries a prefix at all. The directory, the frontmatter name and every live reference are now `quality-gate`, the name ADR-0023 and the glossary already used for the machinery. Nothing installed moves with it: `.githooks/<event>` holds a dispatcher that knows no skill name and resolves gate content live at push time (ADR-0020 D3), so the four gates keep firing with no re-materialization, and the layer ships to this checkout only, so there is no consumer to break. What does move is three hardcoded literals outside the library, and two of them fail loudly — `scripts/gen_gate_table.py` reddens the `gate-table` stage, `Makefile`'s `GATE_HOOKS` breaks `make <gate>`. The third, `scripts/run-e2e-gate.sh`, names the push-gate hook by absolute path, and a stale one is a runtime exit 70 that no marker and no test had ever observed — the test that reads that script checked the hook's basename, which survives a move of the skill. It now matches the whole path against the address the push-gate suite already resolves the hook at, so the two literals cannot drift apart in silence. Historical prose keeps the old name on purpose: the entries below and ADR-0012/0019/0027 describe what was true when they were written, and ADR-0019 names the skill inside an argument, where a retroactive edit would misstate it.

- **Observe's session CLI runs under a `Host` it is handed, not one written into it** (HATS-1883). The integrator used to mount `ai-hats session` by assigning four attributes into `ai_hats_observe.cli._seam` — a private module of another distribution — and the tests did the same with `monkeypatch`; what the CLI depended on was visible nowhere but in those assignments. The package now declares the dependency: `Host` is the four collaborators as one value (layout, tag-filter parser, provider adapter, console), `STANDALONE` is the worktree-free default, `attach(host)` installs one and returns the one it replaced, and every command reads `host()` at call time. A store into the module from anywhere else is red under `test_import_hygiene`. `ai-hats-observe` goes to 0.8.0 and the floor follows.

- **`ProjectLayout` carries every tree the paths leaf used to derive from a directory** (HATS-1883). `library` (rules, skills, hooks), `versions` (the blue-green install tree as geometry — whether a version is complete is still a filesystem question), `cache` (the machine-local tree outside the checkout, its home settled from the environment when the layout is built, never read at access) and the singles `pipeline_steps`, `user_hooks`, `user_rules`, `last_backup`. `cache_home` and `project_key` move to core with it. `ai-hats-core` goes to 0.12.0; the integrator, observe and wt floors follow. The functions of `ai_hats.paths` that re-derived these from `project_dir` — `ai_hats_dir` through `read_current_sha`, and the raw yaml readers under them — are gone: every consumer holds the layout it is handed, `test_import_hygiene` refuses a module that acquires one on its own or a name that grows back in the leaf, and the leaf keeps what is not geometry (`user_home`, `ProjectConfigError`, the legacy migration map). **Breaking** for a surface plugin: every `Surface` method that took `project_dir: Path` takes `layout: ProjectLayout`, and `NotAnAiHatsProjectError` now lives in `ai_hats.rack_workspace`. Found on the way: a foreign-pinned `AI_HATS_VENV` was dropped in silence by the root where the retired resolver had warned; it warns again.

- **The init steps receive their collaborators and report back instead of reaching into the CLI** (HATS-1883). `select_provider`, `bootstrap_project` and `prepare_execute_session` imported six private names from `ai_hats.cli.assembly` and built an `Assembler` up to four times per `ai-hats self init`. The run's `InitRunParams` now carries an `InitWizard` (who answers when a step must ask — the terminal, or a fake) and a `ProjectBootstrapper` (the one `Assembler`, built at the root before the pipeline starts); the steps declare both in `requires`, return what they have to say as `notices` for the CLI to print, and refuse through exceptions rather than `SystemExit`. The wizard hands back a `Surface`, the channel travels as the enum, the directories as paths. `pipeline.steps.init_steps` leaves the composition-layer allowlist, and a module outside `cli/` importing `ai_hats.cli` is now pinned per module and only shrinks.

- **A gate is a few-line declaration over one primitive; a marker is per stage, not per gate** (HATS-1878). The third gate on the checks channel had cost a fourth sixty-line script differing from its siblings in two strings, edits to three hand-kept name lists inside `scripts/ci-local.sh`, a Makefile target, a role row and an ADR row — and the ADR row rotted: seven of the twenty-three stages the dispatcher knew were named nowhere in ADR-0023. `scripts/ci-local.sh` is now `scripts/gates.sh`, and it is two halves of one thing: the **stages** — every kind of check, as a `ci_*` function with a row in `gates.sh list` saying what it checks — and the **marker primitive** over a list of them: `check <stage>…` says which lack a marker for the subject tree and has no code path to the runner, `run <stage>…` runs only those, stops at the first red and stamps each green one. No gate name appears in it. A **gate** is `hooks/<gate>.sh` in the quality-gate skill: a `STAGES=` line and a call into `lib/gate.sh`, which resolves the tree the card puts into master, asks that tree's `scripts/gates.sh check`, and speaks the checks channel's exit codes; `--stages` prints the list, `--run` earns it. The role binds one per edge, as before. A stage runs bare — no argv reaches pytest, since a marker for `unit -k foo` would be a lie, and CI's parallelism rides `PYTEST_ADDOPTS`. Markers live at `<git-common-dir>/ai-hats/stages/<tree>/<stage>`, so `make done-gate` after a green `make review-gate` pays for `master-ci`, `integration` and `merge-smoke` and nothing else, a red stage keeps the stamps earned before it, and a refusal names exactly the stages missing. The subject is always a commit: a run happens in the checkout only when it is clean and at that commit, otherwise in a one-shot scratch checkout — a dirty desk taints no marker and blocks none, and neighbours merging into master cannot move what is being judged. The old marker format is not read; each live card re-earns once. ADR-0023 is rewritten under the split and its two inventory tables render from `gates.sh list`, the gates' `--stages` and the role's bindings, held current by the new `gate-table` stage. Found on the way: `tests/e2e/test_done_gate.py` had been red on master since the review gate landed — its fixture walked the hand-off with no marker, and no card gate runs that file.

### Added

- **A component may no longer name the components that compose it** (HATS-1908). A trait naming the role that takes it inverts the dependency: the trait needs an edit every time a role picks it up, and rots silently when one drops it. Eight such comments were written by one agent in one session with `dev_rule_comment_discipline` resident in its prompt — the rule's examples are all Python, and the violations were all YAML, so the surface went unrecognised. The new `consumer-refs` stage settles the direction from the composition graph rather than from a word list, over two surfaces that differ in strictness because a violation does not look the same in both: a YAML comment addresses no reader but the next author, so the bare name of a carrier is the finding, while shipped prose legitimately addresses its own role (*You were launched as `role-judge`* is a contract, not provenance) and needs a relationship claim — *carried by*, *used by* — before it counts. Neither rule works alone: over prose the bare-name form yields 52 hits, and over YAML the phrase form misses the violation that started this, which carried no phrase at all. It found eight references across six files, all fixed here rather than marked; a reference that must stay says why on the line, as `ticket-ids` does. The stage joins the review, merge and done gates. The half no machine holds — history and stale-able counts in a comment — stays with `dev_rule_comment_discipline`, which gains the YAML foil it lacked and now also hangs on `skill-engineer`: the generic library-authoring role composes that trait and not `trait-se-mindset`, so it had never seen the rule at all.

- **`-m/--model` on the interactive launch, and a contract that keeps the spelling honest** (HATS-1891). `ai-hats -p claude -m fable` used to die on `error: unknown option '-m'` — Claude Code's error, not ai-hats': the root group declared no model option, so the token was forwarded verbatim to a provider that has no short `-m`. It is now the group's own option, prepended to `ctx.args` as `--model <value>`; since that one list feeds both `_launch_session` and `_dry_run_session`, the launch and the `--dry-run` report cannot disagree about the model. Making it a *known* option also disarms the ordering trap it was hiding behind: the group sets `allow_interspersed_args=False`, so `-m fable --dry-run` used to hand `--dry-run` to the provider and spawn the very session the flag asked not to spawn — an option that takes a value does not stop the parser, and the general case is filed as HATS-1893. Hardcoding the `--model` spelling instead of routing through `Surface.model_flags()` is safe only while every surface agrees, so that stopped being an observation and became a test: for each registered surface, `model_flags()` must emit `--model` and `surface_hints()` must advertise it. Enforcing it found one gap — cline advertised only `--yolo`, so `ai-hats --help -p cline` never mentioned the model it accepts.

- **The gate can now tell a check that refused from one that never ran** (HATS-1877). Python exits 1 on an uncaught `ModuleNotFoundError` exactly as a check exits 1 on a finding, so the exit code alone cannot separate them — and the `version-skew-guard` job hosted `e2e-catalog` and `python-pin` without their imports and reported each as its own subject for as long as neither had ever run. The fourteen checker and tool invocations in `scripts/ci-local.sh` now go through one wrapper that reads the failure, and a stage that could not import exits 3 with `BROKEN: this stage never ran — no module named X` and a line saying nothing above it is a finding. The pytest tiers are deliberately outside it: their output streams for minutes, and a missing pytest is not a silent failure mode.

- **A close is refused while master's own CI is red** (HATS-1877). CI had been failing since before 2026-07-28 for a reason unrelated to any of it, so the one arm that could have caught seven of v0.15.0's nine defects went unread for a month; the local gate cannot catch host-dependence because it *is* the host. The `master-ci` stage asks GitHub what master's last `ci.yml` run concluded and refuses `->done` on a non-success. It sits on that edge alone, so `merge-gate` stays offline and stays a subset. Every case where the check has no answer — no `gh`, `gh` exiting non-zero, output it cannot read, a run still in progress — is announced as a skip rather than passed in silence, because a skip nobody is told about is the same defect wearing the gate's colours. `AI_HATS_RED_MASTER_ACK` lets through the one card that is the fix for the redness; the `_ACK` spelling withholds it from a sub-agent on its own, and the use is journalled where it is honoured.

- **The suite now builds the artefact the release publishes** (HATS-1877). `uv build <pkg>` builds the sdist and then the wheel *from* it, which is what all six release-workflow build calls do — and what made the v0.15.0 blocker reachable, since the sdist recorded the symlinked `consent_gate` once under the link and dropped the real directory. All eleven `uv build` call sites in the test suite pass `--wheel`, which builds from the tree instead, so nothing had ever built what users get; measured here, a `--wheel` build omits `ai_hats_library/hooks/consent_gate` on a healthy tree as much as on a broken one. The `wheel-contents` stage builds each `packages/*` through the chain and refuses a wheel that lost a VCS-tracked file, following a tracked symlink to the files carried beneath it. It joins `merge-gate` — the last edge before a version can be published — and takes about three seconds for all five packages. Reverting the `force-include` that fixed the blocker turns it red on exactly the six files that were dropped.

- **The hand-off to a reviewer now has a gate** (HATS-1877). `->merge` asks whether a branch is fit to enter master and `->done` asks whether master is green after the card; the edge between the agent and the human asked nothing, so a card reached a reviewer on the agent's word that the suite was green. That word is the unreliable part, and this card measured it five times over in one session: a pipeline ate a test runner's status so the number read came from `tail`, a stray `cd` persisted and moved three golden tests into another checkout where they passed for the wrong tree, a planted fixture was invisible because the checker reads only VCS-tracked files. None of those was a defect in the subject; each was a defect in the reading. `review-gate` removes the reading — the composition stops at the first red and no marker is written, so there is no number for an agent to misread and no way to hand the card over anyway. It names the same set as `merge-gate` on purpose: the marker is read across gates by stage subset, so one run clears both edges and a `done-gate` run clears all three, and a lighter composition that dropped `unit` would have answered a different question than the one asked. Bound with `at: ['->review']` — every road into the state, not the one expected edge, for the reason already measured on `->done`, where a gate bound to the single edge covered one of its eight roads. The gate roster the dispatcher prints also stops being three hand-kept copies; a name on it that `--stages` cannot resolve is now refused by a test.

- **shellcheck over the shell this repo ships** (HATS-1877). `git_hooks/**` and the skills' `hooks/**` run in a consuming project, where a portability bug reads as a step that quietly did nothing — `mktemp -t e2e-gate` was silently refused on every Linux host for as long as it existed. The stage reads every tracked `*.sh` at severity `warning` and starts clean: the debt was five findings, all fixed here rather than carried as a ratchet. Severity is capped on purpose — `info` and below is 41 findings, twenty of them `SC1091` on dynamically sourced libraries. It does not close the `mktemp` class, which was measured and found invisible to shellcheck at every severity; only a second OS catches that one, and both new stages now also run on Linux in CI.

## [0.15.0] - 2026-08-31

### Fixed

- **The published library was missing the module the consent wrapper imports** (HATS-1876). `core/skills/safety-guard/hooks/consent_gate` is a symlink to the real `hooks/consent_gate`, and the sdist build recorded those six files once — under the symlink — and dropped the directory they actually live in. The wheel is built from that sdist, so `ai_hats_library.hooks.consent_gate` was absent from every published wheel, `0.5.7` included. Nothing noticed while no installed code imported it; `ai_hats.consent_wrapper` is new in this release and imports it at module level, so a fresh `pip install` would have raised `ModuleNotFoundError` on the first HITL session that composed a role declaring `apps.consent_gate`. Measured against the real artefact rather than the build: the wheel downloaded from PyPI carries six `consent_gate` files under the symlinked path and none under the real one, and installing it into a clean venv reproduces the import error. The sdist now names the real directory; the wheel picks it up on its own, which is why only the sdist declares it — adding it to both makes hatchling refuse the duplicate.

### Added

- **A knob you could only learn about by reading the function that reads it** (HATS-1872). `docs/how-to-configure.md`, the page a person configures a project from, named exactly one of the thirty configurable environment variables, and fourteen of them appeared nowhere in `docs/` at all. `docs/reference-env.md` now carries all thirty — budgets, our own path overrides, and the seven homes we honour but do not define — each with its type, its default and a line saying what it does. The page is rendered from the declarations rather than written beside them, and a CI stage refuses it once it falls behind, the way `tests/e2e/CATALOG.md` already worked. What that precedent cannot do, and this one must: for a number, a page that merely *agrees* with the code is a second home that drifts, so the declaration **is** the default and the reader takes it from there. The four budgets belonging to hooks the library delivers into a consuming project keep no declaration — those hooks run where `ai_hats` is not importable — so the generator reads their defaults out of the call site itself. A companion guard refuses any name shipped code reads that nothing declares, across both languages and every distribution.

### Fixed

- **One of six readers of the same knob crashed instead of falling back** (HATS-1872). Six modules each carried their own copy of "read a number from the environment, fall back when it is unusable", and five of them documented that contract in a docstring. The sixth, the pipeline-run rotation, parsed with a bare `int()`: `AI_HATS_PIPELINE_KEEP_N=abc` raised `ValueError` and took the harness down, where every sibling would have returned its default. The contract was real and written nowhere, so nothing could notice the one that broke it. All six now read through one declaration that also holds the number, and the contract is a test over every budget rather than a sentence repeated five times.

- **A bypass flag a sub-agent could set for itself** (HATS-1872). The launcher withholds approval flags from sub-agents by recognising the shape of the name — an `_ACK`, `_OFF` or `_SKIP` suffix. `AI_HATS_SKIP_SELF_LOCATION_GUARD` carries its verb at the front instead, so it went through, and it disables the self-location guard outright. Widening the shape rule was measured and rejected rather than assumed: a front verb has no polarity, and the same widening would also blank `AI_HATS_SKIP_RETIRED_PRUNE`, handing a child the `uv pip uninstall` its parent had suppressed. Only the guard hatch is an approval, so it is named explicitly beside `AI_HATS_YOLO`, which the predicate already named that way; a scan now refuses any future front-verb flag until someone rules on it.

- **Every tool call paid 105 ms to import a dispatcher** (HATS-1869). The hook dispatcher is a fresh process on each tool call, on all five surfaces — including opencode's hookless roles, where it spawns and finds no rows, and claude, whose dispatcher is spelled `channel` and arrived with HATS-1868 while this card sat in review. Three package `__init__` files on its path each pulled the same heavy subgraph: `ai_hats.surfaces` and `ai_hats.surfaces.<surface>` reached the schema layer through the surface contract, and `ai_hats_core` cost pydantic, filelock and asyncio to reach `Deadline`, which imports nothing but stdlib. Measured, not inferred, and the reason the obvious fix was not enough: the three edges are independent, so cutting any two of them still left over 80% of the cost — and the two fixes that suggest themselves, moving `Deadline` out of the core package and giving the dispatchers an entry point outside the surfaces package, are two of those three cuts, so together they would not have fixed it. Only all three moved the number, from ~105 ms of import to ~20 ms (wall ~118 ms → ~34 ms per dispatcher, same interpreter, back to back). All seven facades now bind their exports lazily (PEP 562), the way `ai_hats_observe` already did; every exported name resolves as before, the deprecated `Provider` aliases included. One shape does change: a bare `import ai_hats_core` no longer exposes `ai_hats_core.locks` and its six sibling submodules as attributes, because nothing imports them for it any more. `from ai_hats_core import locks` still works, and the package's documented API — `__all__` plus `safe_delete` — is untouched. Because one eager import anywhere on that path silently restores the whole cost, `tests/test_dispatcher_import_closure.py` refuses a dispatcher whose import closure contains any of them. One side effect is worth naming, because it narrows HATS-1858's "an undeliverable gate refuses": a dispatcher that could not import its own dependencies used to exit non-zero, which agy reads as BROKE, so a half-installed venv refused every tool call — with no reason text and without naming `AI_HATS_GATE_BROKEN_ACK`. The dispatcher no longer imports enough to notice, so on an event whose manifest has no rows a broken install now passes. Where a gate does run, the refusal still lands, one level down and better worded — it carries the traceback and the hatch.

- **The delivery hatch was named by every refusal and read by nothing** (HATS-1858). `AI_HATS_GATE_BROKEN_ACK` appeared in the text of every refusal this channel imposes — "set it to proceed past this gate" — and no code anywhere consulted it. The git channel states the invariant at `githooks_run._skip_reason`: a deny that names a flag nobody reads is worth nothing (HATS-1253 P4). Half of it had been ported. This landed on the same change that converted nine cline branches from fail-open to fail-closed, so a session whose manifest went missing refused every guarded tool call with no way out short of disabling ai-hats. The flag is now read where the refusal is formed, at both levels a gate can fail to be delivered, and taking it is recorded on stderr and in the bypass journal rather than passed in silence.

- **agy ran the whole gate chain on events nothing can bind to** (HATS-1858). Its global hook registers five events and only two are bindable; the other three fell through to `PreToolUse`, and a payload carrying no tool name makes the matcher run every row — so each `Notification`, several per turn, put `safety_gate.py`, `pre_bash_shared_state_guard.sh` and `backlog_write_gate.py` through a call that does not exist. The same collapse keyed the user's own channel wrong, so their `Stop`, `Notification` and `PostInvocation` hooks stopped running entirely while their `PreToolUse` hooks fired on every notification. Codex has carried the guard this mirrors since its own dispatcher was written.

- **A manifest that never resolved met four different answers** (HATS-1858). agy passed the call with a line on stderr, cline refused and named the hatch, codex wrote a bare status naming nothing, and the OpenCode plugin disabled every gate for the whole session by returning no hooks at load time. All four now refuse through one constructor, name the hatch and honour it. This reverses HATS-1339's deliberate fail-open for the vanished-manifest case on agy, which HATS-1439 is the cost of; the reversal is only defensible because the hatch above now works, and its e2e asserts both halves.

- **OpenCode ran whatever command the manifest named** (HATS-1858). codex and cline have both refused a hook command outside the session skills mirror, or without the executable bit, since their dispatchers were written; the OpenCode dispatcher checked neither. `SurfaceProfile` had carried the mirror root the whole time and had no callers at all.

- **Every hook's stderr but the decider's was dropped** (HATS-1858). An allowing hook is the one whose stderr is its only trace — `bypass_journal` writes its own "NOT RECORDED" warning there — so a hatch could be used with the record lost and the warning swallowed. The codex dispatcher had forwarded this unconditionally under a comment saying that swallowing it would erase the audit trail; the line went missing in the move onto the channel.

- **OpenCode's file gates matched the tool and inspected nothing** (HATS-1858). OpenCode names the argument `filePath`; every shipped file gate reads `file_path` or `path` and ALLOWS the call when neither is present (`wt_gate.py`). The surface's profile declared no argument renames at all, so `read`, `edit` and `write` reached the gates with a key none of them look at. The name is observed rather than guessed — opencode's own session database records `filePath` on all 154 reads, edits and writes it holds, while `grep` and `glob` send `path`, which the gates already read — the same kind of evidence that produced codex's `exec`.

- **Codex ran no terminal gate at all** (HATS-1858). Codex names its shell `exec`; the tool-name table in `src/ai_hats/surfaces/codex/claude_hook_adapter.py` had no terminal row, so `Bash|run_command|execute` matched nothing and `safety_gate.py`, `pre_bash_shared_state_guard.sh` and `tool_call_hygiene_guard.sh` never fired on that surface. Measured, not inferred: the real matcher against the real name returned `False` for `exec`, `shell` and `local_shell` alike, with `Bash` → `True` as the control. Nothing was red because every dispatcher test fed its surface `tool_name: "Bash"`, a name no surface sends — `tests/test_surface_matcher_parity.py` now drives each surface's live matcher with names taken from its running tool.

- **OpenCode honoured one shipped gate out of eight** (HATS-1858). The plugin read a verdict off an exit code and understood two shapes: `exit 2`, and a top-level `{"decision":"block"}` that no shipped hook emits. All eight answer in the `permissionDecision` dialect, so seven could refuse a call and be waved through, and `additionalContext` was discarded outright. The plugin is JavaScript and cannot hold a verdict, so it now shells out to `ai_hats.surfaces.opencode.hook_dispatcher` and marshals back the document it is handed. Its nine-entry tool table also lacked `patch`, and an unmapped tool returned before the matcher loop ran, so a file edit by patch met no gate. This surface had no test of any kind before; it has fourteen unit tests now, plus the first e2e driving the real plugin on a real JS runtime.

- **A hook's reply is read whole, not as a 4 KB tail** (HATS-1858). `py_security_lint` puts ruff's entire output in `additionalContext` — over 11 KB on this repo's own test files — and a tail cuts a JSON document's head off. A reply that still overruns is reported rather than read as silence.

- **Nine fail-open branches in the Cline dispatcher now refuse** (HATS-1858). An unreadable payload, an unreadable manifest, a command outside the skills mirror, a timeout, an unstartable hook, a non-zero exit and an unparsable answer all replied `{"cancel": false}` — the tool call went through and the only trace was a line on stderr nobody reads mid-session. Each now refuses and names the variable that opens it. This reverses HATS-1339's deliberate choice for the vanished-manifest case; HATS-1439 is what that choice cost.

### Changed

- **Live claude sessions run their gates through the dispatcher, and a gate that cannot be delivered now refuses** (HATS-1874). HATS-1868 built claude's channel and proved it end to end; the cutover it waited on lands here. A session's `settings.json` used to list every composed gate as its own entry, which left delivery with the harness — whose contract is fail-open: a hook that cannot run passes the call with `rc=0`, zero bytes on stderr and a `hook_non_blocking_error` line inside the transcript, and the class name says it is a contract rather than a bug. That is unreachable from `settings.json`, and HATS-1439 is what it cost. Each bound event now carries ONE entry that reaches the dispatcher, the composed rows move to a manifest beside the settings file, and which gates a call matched is decided by `matches()` from that manifest. Two prices were accepted with the design (supervisor, 2026-08-28): `/hooks` shows one line instead of the gates, and the harness's hook semantics fork — its future improvements stop arriving for free. Latency was the go/no-go and was measured rather than estimated, in paired interleaved runs so machine load could not land on one arm: the gates alone cost 72 ms on a composed maintainer session, spawning a dispatcher per call made it 125 ms (+53 ms, 1.74x) — over the ~100 ms bar — and holding ONE dispatcher open for the session brings it to 94 ms (+22 ms, 1.31x). So the entry asks the session's resident dispatcher over a unix socket and spawns one only when that fails: the wrapper process that already lives for the whole session hosts it, every call still reads the manifest and runs the same `dispatch()`, and an e2e compares the two answers byte for byte. Absence of the resident path is not a failure mode — no socket, no `nc`, or a socket left by a dead session all fall back to spawning, which is the same gate reaching the same verdict. Two things the measurement changed in the design: the entry's matcher is the union of its rows' matchers rather than `*`, because under `*` every `Read` or `Grep` spawns a dispatcher that takes 45 ms to find that nothing matched, where the harness spawns nothing at all; and an event no gate binds to gets no entry. The socket lives in a per-uid 0700 directory under `/tmp` rather than the session cache, because a worktree's cache path is 106 bytes against AF_UNIX's 104 and `bind` fails exactly where the pin points. The guard inside the entry — the one that refuses when the session pins are missing — now honours `AI_HATS_GATE_BROKEN_ACK` too: it runs in `sh`, so it is the one refusal that never reaches the python that honours the hatch everywhere else, and a venv rebuilt mid-session is enough to reach it.

- **Env-var names have a home, and it says what it actually holds** (HATS-1868, folded HATS-1870). `src/ai_hats/env.py` called itself the single source of truth for reading **all** `os.environ` variables while 54 names sat in sixteen other modules of the package, and four sibling distributions (`ai-hats-rack`, `-core`, `-observe`, `-library`) cannot import it at all — so no edit could ever have made the claim true. An unachievable claim reads as no claim: a HATS-1858 plan item asking where to register `AI_HATS_HOOK_TIMEOUT_S` was closed as having no answer, with the home sitting right there. The docstring now states the boundary it can hold, the two gate channels' seven names moved in, and `tests/test_env_homes.py` refuses a name spelled in any module its list does not already carry — today's sprawl is frozen and can no longer grow unreviewed.

- **The hook-channel flow exists once, and claude implements it** (HATS-1868). Four dispatchers wrote the same steps by hand — parse the arrival, resolve the manifest, run the chain, reduce to what the surface can utter, answer — and the copies drifted: HATS-1858's review found four different behaviours for a vanished manifest, one per copy, and after that fix the reduction still lived in three places wrapped around different paths. `SurfaceChannel` and `dispatch()` (`src/ai_hats/surfaces/hook_dispatch.py`) hold the flow; a surface is now a `SurfaceProfile` of names plus five answers. claude is the first implementation and the proof that the interface fits a surface it was not shaped around — its channel refuses a gate whose script vanished, which is the case the harness runs past with `rc=0` and zero bytes on stderr (measured on 2.1.247). **Live claude sessions are not yet routed through it**: the cutover waits on HATS-1869, whose import work the latency budget depends on. A test refuses a channel that reaches for the chain's primitives instead of being handed their result; HATS-1871 moves agy, cline, codex and opencode onto the same flow.

- **Matched gates now run together, and every one of them runs** (HATS-1868). The chain used to run its rows one after another and stop at the first objector. It starts every row a call matches at once now, up to `HOOK_PARALLELISM`, and concludes afterwards by walking the rows in order — so the verdict is unchanged, down to which hook it names, while the gates behind the objector go from skipped to run. That is visible where a gate has a side effect: the bypass journal and `py_security_lint` fire on a call another gate refused. In exchange the chain answers in the time of its slowest gate instead of the sum of all of them, which is what the claude harness does with the same hooks and what a per-tool-call dispatcher has to match to be affordable at all. Affects all four in-process surfaces.

- **One hook budget and one hatch across every surface** (HATS-1858). `AI_HATS_HOOK_TIMEOUT_S` (default 60 s) bounds the whole chain of one tool call rather than each hook alone. Where a surface bounds the dispatcher from outside, that number is derived from this one with a margin rather than written by hand — codex writes it into its TOML, and the OpenCode plugin is handed it through `AI_HATS_HOOK_SURFACE_TIMEOUT_MS` because the asset is copied verbatim and a literal there could not track the budget. Codex had bounded the dispatcher and the hook at the same 60, which made every timeout branch in the dispatcher unreachable and left a killed chain with no verdict at all. `AI_HATS_AGY_HOOK_TIMEOUT_S` is still honoured and says so once per process.

- **What a surface IS is data now** (HATS-1858). Tool names, argument names, manifest and skills-mirror locations, and which verdicts the surface can utter live in a `SurfaceProfile` beside each surface (`src/ai_hats/surfaces/<name>/profile.py`); execution, the reply dialect and the policy live once in `src/ai_hats/surfaces/hook_channel.py`. Fixing a surface is editing a row rather than a dispatcher — the codex fix above is one line of data. Which events a surface delivers is a row too: the three per-surface event lists that looked authoritative were not (one was referenced nowhere at all), and a test now refuses a surface whose row does not cover every bindable event. What a surface cannot utter is a table applied to a fixed point rather than a chain of `if`s, because one reduction can produce what another must then handle. See `docs/glossary.md` "Tool-call hook channel".

- **A refused link kind now names the backlog it was refused by** (HATS-1866).
  `Unknown link kind 'related_tasks': configured kinds are …` listed the tasks
  catalog's kinds without saying they were the tasks catalog's, so the set read
  as absolute. It now reads `… 'related_tasks' on the 'tasks' backlog: …`, and
  when a mounted sibling declares the kind, says so: `— 'related_tasks' is a kind
  of the 'proposals' backlog, which this id does not route to`.

  The cost was paid before the fix, which is why the wording counts as a defect
  rather than a nicety. A session ran `rack transition <TASK> --link
  related_tasks:…` — a form three shipped skills teach with a `PROP`/`HYP`
  subject — against a task card, read the refusal as proof the shipped prose was
  wrong, and filed a card to edit three correct CLI templates. That card is
  cancelled; the instruction was right and the message was misleading.

  Split by layer rather than by convenience: the owner is config identity, so
  `LinksRegistry` carries it, while the sibling lookup is multi-backlog
  semantics and lives in the verb layer — the registry's own docstring makes
  kind-blindness a contract, and this keeps it. The hint is conditional and
  tested in both directions: a kind belonging to no mounted backlog gets none,
  because a hint that always fires is a false statement rather than a help.

  The message had no test at all before this — `test_error_surface.py` pinned
  the error code and payload, never the sentence a human reads.

### Changed — BREAKING

- **Python pin and floor raised to 3.13; one `--repair` needed to cross it** (HATS-1521).
  `PINNED_PYTHON` is 3.13 (was 3.11) and `requires-python` is `>=3.13` on the
  integrator and all five workspace members; the CI matrix is now 3.13 + 3.14.
  The old pin sat on the floor of the support matrix, so every fresh install got
  the version where the HATS-1519 argparse defect lives (`--` before positionals
  is rejected on 3.11/3.12, accepted on 3.13+).
  **Upgrading from an earlier version fails once**: `self update` builds the new
  version's venv with the *old* code's pin (3.11), then cannot resolve a
  distribution requiring `>=3.13`, and exits 1 with uv's
  `does not satisfy Python>=3.13`. Nothing is damaged and the old install keeps
  working. Recover out-of-band, once per install:
  `curl -LsSf https://github.com/muratovv/ai-hats/raw/master/scripts/bootstrap.sh | bash -s -- --repair`
  — the launcher, not the old Python, builds the replacement venv. A session
  whose interpreter is off the pin now says so at startup instead of failing
  later somewhere unrelated. `scripts/check_python_pin.py` (stage `python-pin`)
  refuses a partial bump and a pin the matrix does not run. Migration: `docs/migration-v0.15.0.md` §1.

### Fixed

- **The backlog gate stopped reading a shell redirect token as a path** (HATS-1848).
  `shlex(punctuation_chars=True)` splits `2>&1` into `['2', '>&', '1']`, and both
  halves of `check_backlog_write` treated whatever followed a redirect operator as
  a filename. Two faces, pointing opposite ways:

  - **A false refusal.** The redirect-target reader resolved a bare `1` against the
    cwd, so `rack transition <ID> --log '…' 2>&1` was denied as a write under the
    tracker — but only from a cwd inside the backlog, which made it read as
    flakiness rather than as a rule. The gate refused the very CLI it prescribes.
  - **A silent pass, and the worse of the two.** `cp`/`ln` are judged on their last
    path (`DESTINATION_ONLY`), and the mutator branch counted paths from the raw
    argv — so a trailing `2>&1` put the descriptor last and the real destination was
    never examined. Appending `2>&1` walked a copy into the tracker unchallenged.

  `>&` still takes a filename (`>&out.log` is a real redirect), so the fix
  discriminates on the **operand** — all-digits or `-` after `>&` is a descriptor —
  rather than dropping the operator, which would have opened a third hole where two
  were closed. The mutator branch now counts paths from `without_shell_redirects`,
  the neighbour that already stripped descriptors correctly.

  `tests/e2e/_helpers/hook_chain.py` gains an optional `cwd=` on
  `run_chain` / `run_tool_chain`: the first face does not reproduce at all unless the
  hook process stands under the backlog, and the helper hardcoded the project root.

### Removed

- **The allow-rule lint is gone, and the consent grant works again** (HATS-1861, ADR-0031). HATS-1642 rested on one measurement: a broad `permissions.allow` rule silenced the consent gate, "the harness approved the call before any prompt could appear". Re-measured on Claude Code 2.1.247 it does not reproduce — a PreToolUse hook that returns `ask` blocks the call whatever `allow` says, in `default`, `auto`, `dontAsk` and `bypassPermissions` alike. The fixture is in the repo (`experiments/harness-permission-precedence/probe.sh`, four arms, two of them controls, verdict read from a side-effect file rather than the model's prose), because the claim is about someone else's release and nothing in CI can hold it.

  The lint's advice — answer each finding with a matching `ask` rule — turned out to have a price nobody had priced. An `ask` rule prompts even when the hook returned `allow`, so it silently disarmed the two places the framework deliberately says nothing: a **consent grant** (`consent wt.merge 30` bought no silence at all on the operations it names) and `allow_verdict()`'s routine-`rack` auto-allow. In this project the advice had grown `permissions.ask` to 66 entries. Deleted: `consent_permission_lint.py`, `permission_warning()` in `safety_gate.py`, five tests written against the retired behaviour, and the glossary's "Allow-rule lint (two verdicts)". `consent_spellings.py` stays — the HATS-1781 D6 runtime boundary reads the same table. Migration: `docs/migration-v0.15.0.md` §5.

### Added

- **`ai-hats wt discard` is a consent point** (HATS-1861, ADR-0031 D4). It tears a worktree down and can fall back to `rm -rf` (`--force-remove`), and no hook saw it: its only pause was a `permissions.ask` rule, which does not exist in a headless session. It now declares `pre-discard` under `apps.consent_gate` like `wt.merge` does, so it refuses where nobody can be asked and a grant can cover it. `ai-hats self update` deliberately stays on the permission layer alone — it rewrites a package but destroys no data.

### Reverted

- **The `role-curator` / `library-curator` trim from HATS-1825 is undone** (HATS-1843). That pass moved the engine-internals map and the worktree-verification recipe out of the trait into `docs/how-to-extend.md`, and replaced the role's five-step workflow with three bullets pointing at the trait — buying back ~870 resident tokens a turn. A pointer only pays off if the agent follows it: the session that measured this one reached for the recipe from memory instead and got `ImportError: cannot import name 'build_library_paths'`, which is precisely the bounce the still-active HYP-108 exists to count. Both files are restored byte-identical to `ac5f92a3^`; the doc stays where HATS-1825 put it, so the text now lives in two places on purpose, and whether that duplication survives is HATS-1844's call rather than this card's.

  Measured, not estimated: the composed `role-curator` prompt goes 53,128 → 56,665 chars, its budget 56,819 → 57,695 tokens (+876) — the original ~870 figure was accurate. `maintainer` does not move by a single token, which is the control: it composes neither component.

### Added

- **`ticket-ids`: a CI stage and a pre-commit hook that refuse a tracker id in
  shipped library prose** (HATS-1853). `scripts/check_no_ticket_ids.py` reads
  every `.md`/`.yaml`/`.yml` under the library except `hooks/` and `git_hooks/`,
  and `usage/skills/ticket-id-gate` carries the same invariant into a pre-commit
  hook over staged files. The stage runs in `all`, in the tier behind both gates,
  and in `push-gate`.

  Why a machine and not a rule: the sweep that preceded this cleaned a surface
  the prompt does not guard. The rewritten comment rule opens with "a **comment
  or docstring**" — it governs code — and no line of a composed role forbids an
  id in a `SKILL.md`, while `trait-agent` tells every role to *always* name task
  ids. Live driver, no brake; that is how 177 accumulated. A rule costs ~571
  resident tokens in every composing role, a stage costs none.

  Two design points carry the weight. **Digits are the discrimination**: the
  pattern needs them, so `<PREFIX>-NNN` in a CLI template is not an exception to
  maintain but not a match at all. And **the hook learns the prefix** from the
  project's own card ids instead of carrying one — the hook is itself shipped
  library content, so a hardcoded prefix in it would be the exact leak it
  refuses; a project with no tracker is a loud no-op.

  The gate carries its own positive control: every run reports how many ids the
  same pattern still finds in `docs/adr/` and `CHANGELOG.md`, and it exits
  non-zero when that reaches zero. A checker whose regex has rotted reports a
  clean tree in precisely the words a clean tree earns.

  The last 39 sites went with it — `pipelines` 20, `references` 16,
  `initial_injections` 2, `manifest.yaml` 1 — and the fortieth was rewritten
  rather than excused: `worktree-isolation` cited a ticket number for a message
  the checkout guard prints, so it now quotes the message, which a reader can
  actually grep. The library ships zero ids in prose; `tests/e2e` still pins one
  per file on purpose, since `e2e-catalog` derives the catalog from them.

  Mutation-checked in both halves, and one test did not survive: the staged-scope
  test planted its id in an *untracked* file, invisible to a whole-tree scan too,
  so it passed against the very mutation it existed to catch.

### Changed

- **`build_first_user_message` no longer takes `project_state`** (HATS-1100). The
  `# PROJECT_STATE` section — the whole-backlog `STATE.md` dump, ~5.4K tokens of
  mostly-finished cards on every spawn — was dropped in HATS-681; its keyword
  parameter outlived it by a year, and the docstring went on advertising a
  channel no caller fed. Passing it now raises `TypeError`, which is what the
  new regression test asserts: removing a guard is not the same as inverting it.

- **The sub-agent prompt is documented** (HATS-1100). `docs/how-to-orchestration.md`
  gains *What a sub-agent is handed* — the three first-turn sections, what
  `--ticket` actually pulls in (direct links only, latest `work_log` entry only,
  and the parent epic's whole `plan.md`, uncapped), and how to read the real
  bytes with `--dry-run` or `meta_prompt.txt`. The glossary gains **Sub-agent
  first turn** and **`linked_context`**, and the *Automate runner* entry now
  names the two engines behind one `SurfaceRunResult`.

- **A ticket id no longer appears in a comment or in shipped library prose.** The
  comment rule used to *prescribe* a `TICKET-NNN` pointer — it was introduced to
  displace four-line retellings of task history, so dropping it alone would have
  opened the road back. It now refuses both, and names the positive form: a
  one-line WHY, an ADR for anything longer. An ADR ships inside the repository,
  so its citation resolves for whoever reads the code and `adr-integrity` refuses
  it once it stops resolving; a tracker id does neither.

  176 of the 177 ids in `rule.md`, `SKILL.md` and `config.yaml` injections are
  gone. The one that stays is not provenance, and that distinction is the whole
  job: `tests/_checkout_guard.py` *prints* `HATS-1242` at the agent, so the prose
  naming it names what you will see on screen rather than citing history. The
  CLI-grammar placeholders (`rack transition PROP-NNN --link
  related_tasks:HATS-NNN`) and the 42 `ADR-NNNN` citations were never in scope:
  one teaches the shape of an id, the other resolves inside this repository. Two
  `core/` components were shipping this repo's own ids as their worked example,
  which is exactly the leak; those became neutral prefixes.

  One form keeps earning its id: a `TODO(<card>)` points FORWARD at work no
  commit holds yet, so `git log -S` cannot find it and an ADR is not its home.
  The rule blesses that spelling and refuses the ownerless `TODO:` instead —
  `scripts/todo_context.sh` is built on it and cites this rule as its authority,
  so banning it would have left a shipped tool arguing with the rule it names.

  Not swept, each for a reason: engine code and hooks (1,852 sites, where a bare
  `# HATS-NNNN` with no words has to be rewritten rather than deleted), `tests/`
  — `tests/e2e` cannot be, since `e2e-catalog` requires the pin — and
  `docs/adr/` plus `CHANGELOG.md`, where the number is the record rather than a
  leak. Resident prompt cost, measured on one commit: `role-curator` 56,665 →
  57,188 chars (+131 tokens), `maintainer` +127. It goes **up**, and honestly so:
  the rewritten rule is resident in both roles and grew by more than the
  injections saved, while the `SKILL.md` bodies that lost the most load on
  trigger. This was a shareability change, not a shrink.

### Added

- **A third library layer, `ai-hats-dev`** (HATS-1834). The shipped library was two layers, `core` (engine fundament) and `usage` (consumer catalog), and both leaked. `core` held `dev_rule_e2e_gate`, `rule_core_vs_usage_split` and `rule_composition_value_contract` — all three about ai-hats' own source; `usage` held the whole self-hosting toolchain a consumer never composes. The new layer gives each of those an address, and the rule that described the split stops violating its own criterion.

  Eleven components moved: the `maintainer` and `role-curator` roles, the `ai-hats-maintainer` / `library-curator` / `skill-engineer` traits, and the `maintainer-quality-gate`, `doc-protocol`, `worktree-venv`, `library-change-hypothesis-protocol` skills, plus the two rules named above. `skill-template`, `skill-optimization`, `retro-to-framework`, `skill-lint-gate` and `rule-delivery-gate` stayed in `usage` — they are portable, and `rule-delivery-gate`'s repo path is a guarded fast path, not a dependency.

  `LIBRARY_LAYERS` gained the layer; a new `REQUIRED_LIBRARY_LAYERS` keeps `is_library_root` an `all()` over `core` + `usage` only, so a newer integrator still validates an older library wheel. `check_prose_refs.py` now **discovers** layers instead of listing them — the old hard-coded `(core, usage)` meant a `component § "Heading"` citation into a new layer resolved against nothing and passed silently, which a positive control confirmed before and after.

  Measured, always-on tokens: `maintainer` 11 933 → 10 378 (−13%), `role-curator` 11 218 → 9 607 (−14%). Nine roles that carried `rule_composition_value_contract` via `trait-agent` — `assistant`, `dev-python`, `dev-web`, `architect`, `sre`, `go-dev`, `go-dev-full`, `judge`, `test-agent` — drop 259 tokens each; they were paying every turn for a rule naming `CompositionResult` and `WrapRunner`.

  Two components changed kind rather than address. `dev_rule_e2e_gate` was absorbed by the `maintainer-quality-gate` skill: the policy (what owes an e2e test) now sits with the machine that enforces it, and the trait injection keeps a always-on stub so the gate's existence never depends on a trigger firing. `rule_core_vs_usage_split` was deleted rather than rewritten: a decision tree consulted when creating a component is not a standing constraint, and the criterion was already stated always-on in the `ai-hats-framework` injection. Its worked examples and the trigger-surface test moved to `CONTRIBUTING.md`, leaving one copy instead of the four a replacement skill would have made. Package layout is unchanged: all three layers still ship. Unbundling is HATS-1839.

- **`prose-refs`: a CI stage that reads library prose** (HATS-1825). `scripts/check_prose_refs.py` refuses a reference in `rule.md`, `SKILL.md` or a `config.yaml` injection that no longer resolves. Four shapes, each exact enough that a finding is a fact rather than a guess: an **anchored path** (first segment is a tracked top-level entry), a **library prefix** (`library/` and `libraries/` name a directory the package left behind), a **`component § "Heading"`** section citation, and a **`Class.method`** symbol. It runs in `all`, in the linting `tier` behind both gates, and in `push-gate`.

  What it is not: a check on every backticked token. That form reads 61% of an ordinary corpus as a defect — go module paths, `n/a`, branch names. A resolver for bare component names was written and **dropped**: no marker form separated `reflect-session` from `benchdiff`, `data-testid` or `writing-great-skills`, and its one true finding sat in a file this card deletes. Every narrowing is printed on each run under `not covered`, alongside the count of unanchored paths the run declined to judge, because a gate that reports only findings cannot be told apart from one that looked at nothing.

  Generalised from `adr-integrity` (HATS-1646), which proved the shape on one rigid citation form.

### Removed

- **Rules no longer carry a `metadata.yaml`** (HATS-1836). A rule is one file, `rule.md`. The sidecar held a second copy of each rule's meaning in `description`, and **5 of 14 had drifted** from the bodies they described: `rule_pause_before_shared_state_write` still named `TaskCreate` (zero occurrences in the body), `rule_harness_reminder_hygiene` kept a conditional framing its §1 had made unconditional, `rule_verify_authored_claims` advertised four kinds against a two-row table, `dev_rule_tool_call_hygiene` sold batching where the thesis is now tool-narrowness, and `rule_backlog_discipline` said "ai-hats CLI" where the body says `rack`. Nothing machine-checkable holds two copies in agreement — a semantic divergence is not a gate's to catch — so the copy went. `ai-hats list rules` prints names, symmetric with `list skills` and `list traits`.

  The `delivery` field went with it, finishing what HATS-1515 started: it had been kept as a tripwire against a stale `delivery: summarized`, and its population is now zero.

  **For external libraries:** nothing to migrate. A leftover `rules/<name>/metadata.yaml` is simply never read — rule discovery has always been marked by `rule.md`, so the sidecar can stay or go without effect. Migration: `docs/migration-v0.15.0.md` §4.

### Fixed

- **`library-change-hypothesis-protocol` sent HYP authors down a route the tracker gate denies** (HATS-1831). The skill offered "`rack hyp create` (or hand-write the YAML; both routes work)", and `backlog_write_gate.py` denies the second. The one that works — `rack transition HYP-NNN --set verification_protocol=…` — was named nowhere in it. HATS-1825 met the deny, read it as the field being unreachable, and shipped HYP-112 with the protocol folded into `success_criterion` and no `source_task` link; a sibling session had written the same field normally an hour earlier. The deny names the working route in its own refusal, so what failed was not the machine: a false instruction in a trusted skill outweighed the machine's own correction. Same defect class as the three skills above, in a fourth that card missed.

  Three dead forms went with it, across ten sites in the producer and its consumer `review-hypothesis`: the pydantic `Hypothesis` model carrying `extra="allow"` (rack's `extras_policy` replaced it, and the class no longer exists), `status:` where a rack card says `state:`, and a `set-status` verb `rack` never had. `prose-refs` cannot hold this class — `symbol_resolves` passes a vanished class by design, since `found or not seen_class` cannot tell one from foreign vocabulary like `Path.cwd`. The stage was green before the fix and after it.

  Both skills also lost every ticket id they cited — twenty-eight across the two. A skill is a prompt, and the tracker it was pointing at is gitignored: an agent reading `HATS-510 / HATS-520 / HATS-521 shipped behavior-changing edits with no HYP filed` cannot open any of the three, so the ids read as evidence while carrying none. The claims stayed; only the numbers went. Placeholders an author fills in (`<TASK-ID> / HYP-NNN`) are not citations and stayed too.
- **`wt_gate.py` looked past its own copy of `code_extensions.json` to a path no layout produces** (HATS-1829). The third source it tried — `library/core/skills/worktree-isolation/hooks/code_extensions.json`, resolved from the repo root — was right when it was written and has been dead twice over since: the library moved under `packages/`, and a consumer's project layer is `libraries/skills/<name>/`. It was also **unreachable**, whatever the prefix: the JSON ships beside the hook in the session skill mirror (HATS-1268), so the candidate ahead of it always resolves. Removed, together with the `--show-toplevel` the `git rev-parse` fetched only to build it. In its place the road that does carry a project's edit is now tested — an extension added to the project's own copy of the file has to change the chain's verdict (`tests/e2e/test_wt_gate_extensions_source.py`) — and falling through to the embedded `_DEFAULT_LANGS` is written to the bypass journal instead of decided in silence, which is what let a dead path look alive for months.

- **Twenty-three references in the shipped library did not resolve** (HATS-1825), through months of green gates — none of them read prose. Fifteen were one stale prefix: the library moved under `packages/` and the prose kept saying `library/`. Two more lived in `description:` frontmatter, where backticks are unconventional, and were caught only after the prefix resolver was extended past backticks. `worktree-venv` still described the `library/wt-hooks/` tree HATS-1269 retired; `git-mastery` cited an `Assembler.sync_hooks` that never existed.

- **`rule_pause_before_shared_state_write` promised a guardrail stronger than the one it has** (HATS-1825). Its table said `gh pr merge` and a shared-branch `git push` were **denied**; the hook escalates to the user instead, as `tests/test_shared_state_guard.py` had pinned all along — green, in the same run. The test that was supposed to hold the two in step asserted that two substrings appeared in the rule body and called that "semantic lockstep". The table is gone (the classifier's four verdicts are named instead, and the hook prints its own refusal), and the test now refuses any per-command verdict claim in the rule, proving its pattern on a planted row first. The impossible `TaskCreate | allows` row — the hook matches `Bash` and never sees that tool, and `rule_harness_reminder_hygiene` forbids it outright four hundred tokens below — went with it.

- **`context-handoff`, `context-reset` and `rack-advanced` routed writes into paths `backlog_write_gate.py` denies** (HATS-1825). Handoffs now go through `rack transition <ID> --attach`; a custom backlog catalog goes beside `backlog/`, not inside it.

- **`review-role` is removed** (HATS-1825). A skeleton — "full body to follow" — whose stated trigger named a role that does not exist (`ai-hats reflect role` launches `role-judge`), and the only skill `role-curator` attached on its own. `role-auditor` carries the full `role-coherence-protocol`.

### Changed

- **The `role-curator` prompt is 1,820 tokens lighter** (HATS-1825), 17,043 → 15,223 resident per turn, measured the same way on both sides of the same commit. Engine internals and the worktree-verification recipe moved to `docs/how-to-extend.md`; the role stopped restating the trait it composes; six skill descriptions became triggers instead of procedure summaries; origin retellings in six rules became pointers. Contrastive `✅`/`❌` examples stayed — HATS-638 requires them. On review, three rules gave up what a machine already holds: `dev_rule_e2e_gate` stated its criteria three times, `rule_pause_before_shared_state_write` restated consent channels the hook prints on every refusal, and `rule_verify_authored_claims` dropped the two kinds — name and path — that `prose-refs` now refuses, keeping the quantifier and the count, which nothing checks.

- **`plan-gate` names the absence-proof contract** (HATS-1825). A verification whose result is "X no longer occurs" states three things: the pattern, the scope, and a known-present sample the pattern must still find. No sample, no verdict.

### Changed — BREAKING

- **The surfaces area speaks `surface`; the product keeps saying `provider`** (HATS-1826). Every in-process symbol renamed: `Provider` → `Surface`, `ProviderHint` → `SurfaceHint`, `ProviderRunResult` → `SurfaceRunResult`, `ClaudeProvider` → `ClaudeSurface` (and the four siblings), `ai_hats.providers` → `ai_hats.surface_registry` with `get_provider`/`register_provider`/`provider_names` → `get_surface`/`register_surface`/`surface_names`, and `ai_hats.surfaces_registry` → `ai_hats.surface_catalog`.

  **Unchanged, on purpose:** the `ai_hats.providers` entry-point group, `-p/--provider`, `ai-hats list providers`, the `provider:` key in `ai-hats.yaml`, and the `provider` marker in session artifacts. Renaming those would break installed third-party surfaces and existing sessions and buys nothing; `docs/glossary.md` records the boundary so neither side gets "fixed" to match the other.

  **What actually breaks for an out-of-tree surface:** `Provider` / `ProviderHint` / `ProviderRunResult` survive as deprecated aliases on `ai_hats.surfaces`, so `class MySurface(Provider)` still imports. The **method rename does not alias** — a surface overriding `provider_hints` is silently never called again; rename the override to `surface_hints`. Migration: `docs/migration-v0.15.0.md` §6a.

- **A rack lifecycle point is now an arrow, and it denotes a SET** (HATS-1719). `at: [edge:<from>--<to>]` is replaced by `at: ['<from>-><to>']`, and the new `at: ['-><to>']` binds **every** road into a state. The retired spelling is **removed, not aliased**: a role still carrying it is refused at composition, naming the row. Grammar and legality: ADR-0017 §3.

  Why it is worth the break: the shipped `->done` gate ("is master green after this card") was bound to `edge:review--done` — **one** of the **eight** edges into `done` that the worktree teardown-merge fires on. A forced close (`rack transition <id> --state done --force`, blessed by `docs/ARCHITECTURE.md`) merged into master with that question never asked. `at: ['->done']` closes all eight, and the forced close is now gated like every other road.

  Also breaking, for anyone reading these surfaces:
  - **The event key** in the transition journal, `audit.jsonl` and `AI_HATS_HOOK_POINT` is now `<from>-><to>`. Records written earlier keep the old spelling; `rack context --attr audit --event` accepts **both** and resolves them to the same record.
  - **`rack doctor --json`** renamed its per-row key `point` → `selector`, and `role_materialization.json` did the same — the latter additionally carrying `from` and `to` already parsed, because the PreToolUse guard is stdlib-only and must not hold a second copy of the grammar.
  - **`ai_hats_rack`**: `Subscription.event_key` → `.selector` (a `Selector` or a non-FSM key string), `BindingStatus.point` → `.selector`, `checks.parse_edge_point` → `selectors.parse_selector`, `checks.EDGE_PREFIX` removed. `Dispatcher.subscribers_for` now answers for non-FSM keys only; an edge is addressed by its pair via `subscribers_for_edge`.
  - **`ai_hats_core`**: `ConsentPoint.point` → `.selector`.
  - **`ai_hats_rack.all_edge_keys` is gone** — the product is `all_edges`, and it returns typed pairs. The string form had no caller left and its docstring named a spelling the journal does not take from it.
  - **A topology may no longer name a state `ANY` or `NONE`** — refused at load. The selector grammar owns both words, and a state actually called `ANY` would turn exact subscriptions into wildcards.

  Consent deliberately did **not** widen: it migrates one-to-one and stays on the edges the trait already named. A wide question without a batch is click-spam (HATS-1728). That split the `maintainer` role's single row in two — the "run + consent on one row" form ties the gate's reach to the question's, and here they differ.

  Not yet legal, each refused by name of the card that opens it: `NONE->` / `->NONE` (HATS-1703). `<from>->` and `ANY->ANY` arrived in HATS-1720, below. Migration: `docs/migration-v0.15.0.md` §7.

- **A rack selector may now leave the TARGET open — `<from>->` and `ANY->ANY`** (HATS-1720). Every road OUT of a state, and every move of a backlog, said in one arrow. This is what the code channel had been writing by hand: three helpers in the integrator and four subscribers inside the package each rebuilt the topology's state product to say what one selector says, which is why `FrozenIntegrityExtension` had to be handed a topology at all.

  **A declared row may not stand on a wide output, and each refusal names the card that lifts it.** A `run:` row is in-lock and can refuse, and one refusal on every way out locks the card in that state — measured, `on_error: warn` softens a check that BROKE and never one that refused, and `--force` relaxes the FSM arrow while the check still runs; a notify-only row becomes legal there with HATS-1723. `consent:` is refused for an unrelated reason: the guard matches on the target state and never learns which state the card is leaving, so the question would go unasked in silence (HATS-1706 opens it, after HATS-1712).

  Also in this slice:
  - **A subscriber runs at most once per event and phase.** Wide selectors let a subscriber's own bindings overlap — `ownership-release` holds `execute->` and `->done`, and `execute->done` matches both — and measured, the dispatcher used to hand it the event twice.
  - **`FrozenIntegrityExtension(tasks_dir, topology=…)` no longer takes `topology`** — it says `ANY->ANY` and no longer needs one. Same for the integrator's ownership/worktree/consent adapters.
  - **`ANY` on ONE side is refused** as a second spelling of the empty side: write `->done`, `execute->`, or `ANY->ANY` for everywhere.
  - **A card sitting in a state the topology no longer has now reaches the subscribers.** A wide selector matches a PAIR; the enumeration it replaced was drawn from `topology.states`, and the kernel validates only a transition's target. So after a state is renamed or dropped in `backlog.yaml`, `rack transition <id> --state done --force` on a card left behind used to write the state and run nothing at all — no gate, no consent, no worktree teardown, no ownership release. Now it runs them, like every other road into `done`. Migration: `docs/migration-v0.15.0.md` §7.

- **Four surfaces stopped being distributions of their own** (HATS-1826). `agy`, `cline`, `codex` and `opencode` shipped as packages under `packages/surfaces/<name>/` — four PyPI projects, four publish jobs, four trusted-publisher environments — for a tier ADR-0014 created to state a dependency rule, not to ship wheels. Each is now a folder in the `ai_hats.surfaces` area (`src/ai_hats/surfaces/<name>/`), declared next to `claude` under `[project.entry-points."ai_hats.providers"]` in the root `pyproject.toml`, so it installs, versions and releases with `ai-hats` and nothing resolves it separately. The seam is untouched: providers keep their names, discovery is still the `ai_hats.providers` entry point, and an out-of-tree package can still register a surface. What is gone is the claim that being a surface means being a distribution — ADR-0026 D10 asks for a real consumer outside ai-hats, an entry point a human types or a documented API someone imports, and an owner of the release cycle; none of the four had one. Selecting a surface no longer installs anything either: ai-hats never runs an installer for a provider it already ships.

  **The upgrade prunes what it replaced.** `ai-hats-agy` (0.2.0) and `ai-hats-cline` (0.5.0) had reached PyPI, and `self update` installs rather than synchronizes — both would have stayed in the venv shadowing the folded code, with agy's `ai-hats-hook-dispatcher` console script still on `PATH`. They join `retired_dists` (HATS-1280), which uninstalls a retired distribution during the upgrade to the release that retires it; no user action. `ai-hats-codex` and `ai-hats-opencode` need no prune — their publish jobs were gated behind an `ai-hats>=0.15.0` floor that no tag has ever met, so no venv can be carrying them. Migration: `docs/migration-v0.15.0.md` §6b.

### Added

- **`rack ls --id <ID>`** (HATS-1654). Accept `--id` as an alias for the positional `TASK_ID`, the spelling `rack create` already uses. Until now it exited 2 and click's nearest-string hint offered `--deep` / `--link`, neither of which selects a card.

- **Every blocker named in one refused merge** (HATS-1654). A refused `ai-hats wt merge` now prints, under its own refusal, the other blockers a read-only probe can see — consent, drift, dirty tree, wandered HEAD, rebased branch — plus a line saying `wt:pre-merge` checks were not probed. The guards still fire in the same order and still raise the same exception; only the report grew. Measured cost of the old behaviour: three runs to learn three facts, with a four-minute gate rerun between two of them.

- **Opt-in rule delivery (`delivery: always_on` in `metadata.yaml`)** (HATS-1511). Allow rules from any library layer (including user-global and project-local) to request full body delivery into system prompt `## RULES` via `delivery: always_on` in `metadata.yaml`.

- **User-global library paths (`~/.ai-hats/library_paths.yaml`)** (HATS-1508). Support user-level external library directories (`paths: [<dir>, ...]`) inside `build_library_paths` without modifying project `ai-hats.yaml` or using symlinks.

- **Runtime role spec composition (`-r "maintainer + leader"` / `-r "maintainer - trait-base"`)** (HATS-1456). Support ad-hoc runtime expressions in `-r` / `--role` to add or remove traits, rules, or skills for a single session without editing `ai-hats.yaml`. Evaluates as an ephemeral third overlay layer (`[global, project, runtime]`).

- **`leader` and `worker` traits for paired sessions** (HATS-1491). Two `usage/` traits that split one card between two live sessions: the leader owns the plan and a two-contour review (completeness first, then discipline) and writes no code; the worker owns every mechanical step, sleeps on `ai-hats wait --until execute --until done`, and hands work back with the artifacts that settle each claim. Mix onto any base role — `ai-hats -r "maintainer + leader"`, `ai-hats -p agy -r "maintainer + worker"`. Until now the `leader` / `worker` examples in the docs named components that did not exist, so a command copied from them exited 2.

### Fixed

- **Editable installs now repair stale built-in step metadata before the CLI starts** (HATS-1810). A checkout whose `[project.entry-points."ai_hats.steps"]` declarations changed after installation no longer fails pipeline resolution while `uv sync --check` reports no work: bootstrap detects the exact name-to-target drift, reinstalls the checkout, verifies the refreshed entry points, and re-execs the requested command. Local `ai-hats self update` now runs the same post-install integrity check and fails loudly if a first-party step target cannot load.

- **A consent recipe now works when followed one line at a time** (HATS-1654). Every gate that asks for `AI_HATS_MERGE_ACK` / `AI_HATS_PLAN_ACK` printed the `export` on a line of its own with the command it unlocks on the next one. Typed one command at a time — a harness bash call, a tool call, any per-command subshell — the export died with its shell and the gate refused a correctly typed command; measured twice on one merge. The export and the command it unlocks now ride one line joined by `&&`. `export` remains the only spelling: an inline `AI_HATS_*_ACK=1 <cmd>` prefix is still refused as a self-grant (HATS-1639).

- **Worktrees are minted outside `$TMPDIR`, so the OS stops eating them** (HATS-1632). `create()` took the temp root, which macOS reaps by *access* time (`com.apple.bsd.dirhelper`, daily at 03:35, `CLEAN_FILES_OLDER_THAN_DAYS=3`), deleting files and leaving the directory skeleton. A worktree venv was born already past that threshold, because `uv` materializes packages from `~/.cache/uv` preserving the cache's atime: measured on a venv hours old, 595 `.py` files carried an atime 15 days stale, and three worktrees that had survived a sweep held 542–554 of 2234 `.py` against 574 predicted by the 3-day rule. It surfaced mid-session as a `ModuleNotFoundError` naming an unrelated module, and a worktree idle past the threshold lost its **source tree** too (measured: 0 files, directories intact). The checkout root is now injected as `<cache_root>/worktrees/` the way `state_dir` already was (ADR-0013 D4); a bare core keeps the `mkdtemp` fallback. Worktrees hold uncommitted work, so the cross-project key sweep spares any key holding them — by presence, not age — and no reaper replaces the OS one: unbounded growth (~390 MB per tree) is the accepted trade.

- **A leaked session pin no longer makes ai-hats refuse the project's own venv** (HATS-1621). The self-location guard read `AI_HATS_VENV` with a raw `os.environ.get` and honoured it verbatim, so a pin inherited from another project became the venv it compared against. Running from a venv outside `.agent/ai-hats/` then exited 3 and instructed the user to uninstall ai-hats from the very venv the project resolves to. Both the launcher (HATS-944) and `venv_path` drop a foreign-pinned override per ADR-0025 D3; the guard now resolves through `venv_path`, which applies that scoping as the first step of its own precedence chain. A bare `AI_HATS_VENV` with no pin beside it keeps env-wins semantics.

- **A hung predicate no longer makes `ai-hats wait --timeout` unreachable** (HATS-1598). The probe ran through `subprocess.run` with no `timeout=`, and the deadline was read only after it returned, so `ai-hats wait --until-cmd 'ssh box test -f /out/done' --timeout 60` waited forever on a dead connection — exit 124 was unreachable for every hung predicate. A probe is now bounded by whichever is nearer: the wait's own deadline (exit 124) or the new `--probe-timeout SEC` (default 30, exit 2), which also covers `--timeout 0`, where there is no deadline to bound a probe with. An expired probe is killed by process group, so a compound predicate's children do not outlive it. The same hole in the agy hook dispatcher is closed with a 60s per-hook budget (`AI_HATS_AGY_HOOK_TIMEOUT_S`): it runs on every tool call, so one hung `PreToolUse` hook wedged the whole session; a killed hook returns 1 — `BROKE` per ADR-0020 D2, not a refusal.

- **`--dry-run` and the launch record describe the sub-agent launch that actually happens** (HATS-1552). `ai-hats agent --dry-run` built the meta-prompt with its own function, so the reported argv for cline and agy dropped `WORKING_DIRECTORY` and both ticket sections — and for a CLI surface the whole prompt is one argv token. `role_materialization.json` reported `env_keys: []` while the sub-agent received six variables (`AI_HATS_SESSION_ID` among them), reported `artifacts.extra_env`, which the child never received, and reported `checks: []` for every sub-agent ever launched, leaving `session-reviewer` blind to whether its gates were armed. The runner also re-derived its command at spawn time and matched its own record by coincidence, and the claude engine sent the SDK a one-line `Ticket: <id>` while the saved audit rendered the whole card. Prompt, argv, env and SDK options now each come from one expression shared by the runner and the report.

- **`--dry-run` under cline no longer binds a socket** (HATS-1554). `ClineProvider.get_env` allocated the hub port by binding `127.0.0.1:0`, and it sits on the report path, so a dry-run performed a network side effect invisible to the materialization port and then named a port the launch would never use. `get_env` is pure; the bind moved to the new launch-only `Provider.claim_launch_env` hook (empty by default, so other surfaces are unaffected).

- **Multi-root, hyphenated, and structural dangling rule pointer detection** (HATS-1514). Fix four gaps in `find_dangling_rule_pointers`: support hyphenated rule names in prose regex, scan all library roots (`build_library_paths()`), validate `composition.rules` in `config.yaml`, and recognize HATS-1511 `delivery: always_on` opt-ins.

- **Silent drop of composed rules and empty rule bodies** (HATS-1511). Log explicit warnings when a rule in composition is not delivered to the prompt or when an always-on/opt-in rule has an empty body, closing previously silent drop paths.

### Changed — BREAKING

- **Inverted rule-delivery default: every composed rule body is delivered in full** (HATS-1515). Every rule in `composition.rules` delivers its `rule.md` body into system prompt `## RULES`. Removed `ALWAYS_ON_RULES` and `SUMMARIZED_IN_INJECTION` sets; `rule-delivery-gate` checks that all rule pointers name existing rules in the library. Migration: `docs/migration-v0.15.0.md` §3.

- **`AI_HATS_DIR` + foreign `AI_HATS_PROJECT_DIR` pin raises exit code 1 (`foreign_project_pin`)** (HATS-1471).
  When `AI_HATS_DIR` is set to a sandbox directory and `AI_HATS_PROJECT_DIR` is set to a foreign project path, `rack` commands and `ai-hats wait` now refuse execution with exit code 1 and typed error `foreign_project_pin` detailing both paths and `ai_hats_dir`. Previously, `rack` ignored `AI_HATS_DIR` on CLI resolution and wrote to the live project root. Migration: `docs/migration-v0.15.0.md` §8.

- **The two role-audit roles were renamed** (HATS-1425): `auditor-for-role` →
  `role-auditor`, `judge-for-role` → `role-judge`. The system carries two
  headless-auditor → HITL-judge pairs, and only this one was named off-pattern
  (`judge-auditor` → `judge` beside `auditor-for-role` → `judge-for-role`),
  which read as if the auditor were a curator rather than its opposite. Neither
  role's composition, protocol, or contract changed. **If a customization
  block, project `ai-hats.yaml`, or script names either old role, update it:
  `ComponentConfig` is declared `extra="ignore"`, so a stale role key is
  dropped silently — the customization simply stops applying, with no error.** Migration: `docs/migration-v0.15.0.md` §2.

### Fixed

- **`LibraryResolver.list_components` now discovers symlinked components and namespaces** (HATS-1505). Replaced `Path.rglob` with `os.walk(followlinks=True)` guarded by realpath traversal tracking, allowing symlinked trait, skill, rule, and role directories to be listed properly and preventing `RoleSpecError` during runtime composition.

- **Symlinked library components no longer break worktree teardown** (HATS-1494). `resolve_hook_script` removed the search-root containment check that refused skills living under a symlinked library layer (e.g. `~/.ai-hats/skills -> ~/dev/ai-hats-custom/skills`). M11 security containment of the resolved hook script inside its skill root remains strictly enforced.

- **Deferred rule removals in overlays and customizations** (HATS-1456). Rule removals (`remove: rules: [name]`) in `customizations` and overlays now resolve against the full composed set (mirroring skill removals), allowing rules brought by traits to be removed cleanly.

- **An estimated token count no longer reaches `metrics.json` looking measured**
  (HATS-1433). The agy recovery ladder (HATS-1427) falls back from the real
  count in `metrics.json` to scraping the rendered TUI trace and, failing that,
  to estimating off text length — and any of the three cleared the record's
  flags, so a guess landed beside `measured: true` and read as fact to every
  consumer, including the cost comparison between provider arms. Since agy emits
  no token telemetry at source (HATS-1420), the guessing tiers are the ones that
  actually run there. The counts are still written, now carrying
  `token-telemetry-estimated`; `is_zero_output` refuses to discard a sub-agent's
  work on the strength of one; and a half-scraped count no longer pads its
  missing half with an invented `100`/`10`. Also unblocks the local gate, which
  had been failing at its first stage on an `E402` in the same file.

  Folds in HATS-1441, which fixed the same defect in parallel with a two-value
  vocabulary. Three states are real — measured, estimated, nothing to measure —
  and two values cannot spell them without lying, which is how `metrics.json`
  came to say "no telemetry" while `usage.json` showed 4200 input tokens for the
  same session. Both reports now derive from one ladder that returns the counts
  *and* their provenance, and a test asserts they agree across seven input
  shapes — the guard neither fix had, and the reason the drift survived two
  attempts. Writing that test revived the estimate tier in the usage fallback,
  dead since HATS-1427 because `getattr(report, "turns")` read a dict.

- **A stray Ctrl-C at session end no longer costs a finalize step** (HATS-1426).
  Once the provider exits, the terminal is back in cooked mode, so a press that
  used to reach the child as a byte now arrives at the parent as a real SIGINT —
  landing inside whichever finalize step is running. One press was enough: an
  observed 74-minute session died inside the retro decision and left no
  `retro.log` at all, so it never reached the reflexive loop, while the banner
  still printed green. SIGINT is now held for the whole finalize window (both
  the HITL and sub-agent arms); each press prints one line, and three inside the
  1.5 s window still abort with exit 130 — the same gesture as the wedged-child
  escape hatch, now sharing its counter. The abort travels as `FinalizeAborted`,
  which is neither `Exception` nor `KeyboardInterrupt` precisely because the
  per-phase HATS-086 catches would otherwise swallow it. Two guards that came
  with the same incident: the retro breadcrumb is written *before* the decision
  rather than after, and `make_decision`'s "never raises" promise now covers
  `KeyboardInterrupt` and the path resolution it actually died on.

- **An editable install whose METADATA went stale now heals itself on the next
  run** (HATS-1368, HATS-1367, epic HATS-1364). METADATA is written once, at
  install time, and then drifts from the code the `.pth` points at — in both
  directions. A sweep of 16 consumer venvs on 2026-07-30 found 11 broken by
  that drift: 8 with metadata predating the workspace split (it declares no
  first-party deps, so the startup gate saw nothing missing and the process
  died importing `ai_hats_wt`) and 3 still declaring the `ai-hats-tracker`
  deleted in 0.14.0 (healed forever as an already-satisfied no-op). On an
  editable install the gate now reads the checkout's own `pyproject.toml`,
  which cannot drift from the code beside it; wheel installs keep reading
  METADATA. Both the repair it runs and the one it prints re-point the
  checkout (`uv pip install --python <exe> -e <path>`) instead of naming
  distributions — the form that no-ops, or, since the workspace members are
  published, quietly replaces the developer's checkout with PyPI wheels.

  Three fixes ride along. The gate moved ahead of the `ai_hats.cli` import it
  protects (a venv missing a workspace member used to die importing the module
  that held the gate, and the "broken install" notice it printed advised
  `python -m ai_hats self update` — which re-enters the same failing import).
  The HATS-1359 no-op-heal recheck no longer misreads a successful editable
  heal as a no-op: `.pth` files are processed only at interpreter startup, so
  the site hook is re-run before the recheck. And `heal-editables`, the
  launcher's heal channel, now covers `packages/*` workspace members, not only
  `packages/surfaces/*`.

  Unchanged: the `ai-hats` launcher still refuses a venv missing a workspace
  member and points at `ai-hats self update` (HATS-895) — that hint heals via
  the launcher's own path and stays the fail-closed contract.

### Changed

- **The machine-only cache moved out of the project** (HATS-1398, epic
  HATS-1266, **BREAKING** on-disk layout). Per-session artefacts, the
  update-check probe mirror and `update-check.json` used to live in the
  workspace at `<ai_hats_dir>/.cache/`; they now resolve under
  `<cache_home>/<project-key>/`, where `cache_home()` is
  `$AI_HATS_CACHE_HOME` → `$XDG_CACHE_HOME/ai-hats` → `~/.cache/ai-hats` and
  `project_key()` is `<dirname>-<sha256(abs path)[:8]>`, so two checkouts
  sharing a basename never collide. Rationale: the project is a tree that
  file-watchers, `git status`, greps and indexers all pay for, and regenerable
  state should not be in it. Both env vars name a *base*, never a final root —
  `cache_root()` always appends the project key, so a var leaked into another
  project's shell cannot merge two caches. No user action and no migration —
  a cache is re-derived, not carried: new sessions build in the new root and
  the session-start sweep deletes the old in-tree `.cache/` (session dirs wait
  out the TTL, since one may belong to a session that started before the move).
  The only cost is one re-fetch of the probe mirror.
  New resolvers `cache_home()` / `project_key()`
  / `cache_root()` in `src/ai_hats/paths/_dirs.py`; `session_cache_root()` /
  `session_cache_dir()` keep their names. The standalone agy hook dispatcher
  runs without importing ai-hats, so it takes the resolved dir from
  `AI_HATS_SESSION_CACHE_DIR`, pinned into the session env at spawn.

## [0.14.0] - 2026-07-29

### Removed

- **The legacy `ai-hats task` CLI is unmounted** (HATS-1260, epic HATS-1159).
  The four command groups — `task`, `task hyp`, `task proposal`, `task attach`
  (28 verbs) — are gone from the `ai-hats` surface; `rack` is the only backlog
  CLI. Migration: `docs/migration-v0.14.0.md` (ships with the release). An
  unrecognized leading word now follows the standard bare-positional-prompt
  rule (HATS-087 covered flag-shaped tokens; bare words became passthrough in
  HATS-1202, in this same release), so `ai-hats task …` no longer errors — it
  starts a session with that text as the prompt. Recorded behavior change:
  `task_prefix`
  auto-detection from pre-existing task folders (with persist-back to
  `ai-hats.yaml`) was a feature of the removed CLI path; rack reads
  `task_prefix` from `ai-hats.yaml` only — legacy projects should set it
  explicitly.

- **The `backlog-manager` skill tree and its git hook are deleted** (HATS-1261,
  epic HATS-1252). Once `hatrack` became the default manager (HATS-1054) the
  classic skill was composed by no role and taught a CLI that no longer exists.
  The skill, its five reference files and the `pre-commit-attachments.sh` hook
  that guarded the legacy card subtree are gone from `ai-hats-library`;
  `hatrack` is the only shipped backlog-manager skill. Migration:
  `docs/migration-v0.14.0.md`.

- **`packages/ai-hats-tracker` is deleted** (HATS-1262, epic HATS-1252). The
  package backing the retired `ai-hats task` CLI is gone from the uv workspace,
  the root dependency set, and the publish workflow. Its surviving consumers
  were re-homed first — the ownership registry, `linked_context` and
  `TrackerPaths` into `src/ai_hats` (HATS-1258), and the retro window onto the
  rack facade (HATS-1259). Migration: `docs/migration-v0.14.0.md`. An existing
  venv keeps the orphaned distribution — and with it a working legacy CLI over
  the same store — until it is pruned; see `self update` under *Fixed*.

### Fixed

- **A no-op self-heal no longer deadlocks the CLI in an infinite re-exec loop**
  (HATS-1359). `bootstrap_or_die()` treated `attempt_self_heal()`'s exit code as
  proof the missing dependency was importable, then `os.execv`'d
  unconditionally. But `uv pip install <bare-name>` can exit 0 by auditing an
  existing dist-info as already-satisfied without the module ever becoming
  importable — so the re-exec'd process found the identical dep missing and
  looped forever, until Ctrl-C. Because `cli.main()` calls it before subcommand
  dispatch, even `ai-hats self update` — the in-band fix — hung, leaving no
  escape but an out-of-band `uv` command. It now rechecks
  `find_missing_runtime_deps()` before re-execing (the pattern
  `verify_after_install()` already used one function over) and fails loud with
  the rescue command instead of looping. **This release can trigger the
  condition**: deleting `ai-hats-tracker` (above) leaves an editable install
  whose metadata predates the removal still declaring a dependency whose source
  directory is gone. Migration: `docs/migration-v0.14.0.md` §6.

- **`ai-hats self update` prunes distributions the new version retired**
  (HATS-1280). `self update` installs, it does not synchronize: a dependency the
  new version dropped stayed in the venv with its console scripts. After 0.14.0
  that would leave `ai-hats-tracker` installed alongside — a working legacy
  backlog CLI over the same store, with a diverging plan-section catalog and no
  `edge:` bindings — so the exact hazard the cutover exists to remove would
  survive the upgrade under a different name. The prune runs post-install in the
  **new** interpreter, so it fires on the upgrade that introduces it rather than
  one release later, and works from an explicit retired-distribution list rather
  than generic orphan detection. No-op on the managed blue-green path, stands
  down on editable installs, and fails open when `uv` is unavailable.
  Migration: `docs/migration-v0.14.0.md`.

- **`wt merge` refuses a stale ref and never deletes a branch it did not land**
  (HATS-1346). A live incident dropped two commits: the auto-merge on
  `review → done` consumed an integration ref prepared by an *earlier* session
  and fast-forwarded that, then removed the worktree and deleted the branch —
  whose tip was two commits ahead. The work survived only because the objects
  were still unreachable-but-present in the shared object store, one `git gc`
  from gone. `merge` now resolves the task branch tip at merge time; a
  caller-supplied `expected_tip` that no longer matches is a typed
  `WorktreeStaleRefError` naming both SHAs, and teardown is gated on containment
  (`merge-base --is-ancestor <tip> <target>`) rather than on the merge step
  having returned zero — a target that does not contain the tip raises
  `WorktreeMergeIncompleteError` and leaves the worktree and branch in place.
  Both are precondition refusals, so neither is reported as a failed merge. Same
  defect class as HATS-1307 — validating against cached state instead of live
  state — this time on the destructive path, where the `review → execute` rework
  loop makes "another session advanced the branch" a normal condition.

- **A freshly created worktree comes with its own venv** (HATS-1291). A
  rack-created worktree had none, and the `git-mastery` pre-commit smoke hook
  resolved `pytest` through `PATH` — landing on the main checkout's interpreter,
  which the wrong-checkout guard then refused. The very first `git commit` inside
  a new worktree failed, and the printed remedy was a ten-line manual
  provisioning recipe. A new `worktree-venv` skill contributes a `wt_in` hook
  that provisions the venv at worktree creation; the smoke hook now runs the
  committed checkout's own pytest.

- **The pre-commit smoke hook stops blocking commits in projects without
  `tests/e2e/`** (HATS-1352). The hook is shipped to consumers through the
  `git-mastery` skill, and it passed `tests/e2e/` to pytest unconditionally.
  pytest answers a missing path with rc=4 (usage error) — not the rc=5 the hook
  treats as "nothing to run" — so any consumer project with an `integration`-
  tagged task in `execute` and no such directory had **every** commit blocked.
  The path is now passed only when it exists; without it pytest falls back to the
  configured `testpaths`, which is the pre-scoping behaviour.

- **`ai-hats-agy` is published** (HATS-1353). The `agy` surface was listed in
  `KNOWN_SURFACES` and self-heal ran `uv pip install ai-hats-agy` for it, but
  nothing ever built or published the distribution — so selecting the surface on
  a stable-channel install ended in a missing package. It now builds and
  publishes from `release-packages.yml` in its own `pypi-agy` environment, and a
  test pins that every surface in the registry has a publish job.

- **The `ai-hats` binary runs from inside a worktree** (HATS-1306). The launcher
  resolved the project venv relative to cwd, so any invocation inside a linked
  worktree died with `venv missing at <worktree>/.agent/ai-hats/.venv`. That
  killed `ai-hats wt exec <branch> -- git commit` outright and every hook or
  script that re-enters ai-hats from inside a worktree. The launcher now hops to
  the main checkout, guarded on that root actually carrying `.agent/` or
  `ai-hats.yaml`.

- **`rack transition --append <field>=<json>` can no longer render a card
  unreadable** (HATS-1299). `--append tags='["x"]'` nested the array as a single
  entry, and the card then failed strict validation on *read*: `rack context`
  reported `Task not found`, `--set` could not repair it because it validates
  before it mutates, and the only way out was hand-editing `task.yaml`. Reads are
  now tolerant and writes strict: `from_yaml` coerces stray entries and reports
  them as warnings, `save` refuses any mapping the strict model cannot load back,
  and an array extends rather than nests.

- **Link, field and document ops reach the audit journal, on both sides of a
  link** (HATS-1351). Only state transitions and `epicify` ever reached
  `audit.jsonl` — there were zero records for `--link`, `--unlink`, `--set`,
  `--append`, `--log` or the document ops. A re-parent left no trace at all, and
  the mirrored side learned nothing. `transition` now emits `op:*` records
  carrying the field or document name, and the post-lock mirror delta is
  persisted on the target card rather than dropped.

- **A second `fold` is refused instead of silently overwriting the first**
  (HATS-1328). `--link fold:<ID>` on an already-folded card overwrote the scalar
  link field, so the first fold vanished — silent loss of exactly the audit trail
  folding exists to leave. It is now a typed `already_folded` refusal naming the
  current target, and `folded_into` declares the derived inverse `subsumes`, so
  "what was folded into this card" is answerable again.

- **The documented re-parent command is the one that works** (HATS-1350). The
  `hatrack` skill and `docs/how-to-hatrack.md` both taught
  `rack transition <ID> --set parent_task=<EPIC>`, which rack refuses, and the
  refusal's own suggestion then failed `already_linked` because the field was
  occupied — an agent following the docs hit two typed refusals in a row. Both
  now show the working form: `--unlink parent_task:<old> --link
  parent_task:<new>` in one transition.

- **The drift guard no longer dead-ends `rack transition <id> done`** (HATS-1307).
  Drift was measured against the base SHA snapshotted at `wt create`, so a branch
  rebased onto the moved base was still refused with "N commits ahead" — and the
  only override, `--accept-drift`, lives on `ai-hats wt merge`, which
  `rack transition` cannot pass. Drift now also asks whether the branch already
  contains the base, so the rebase every operator reaches for first is what
  actually clears it. Both refusal recipes lead with that rebase and demote
  `--accept-drift` to what it always meant: merging a baseline you knowingly
  leave stale.
- **`ai-hats reflect *` reports a config error instead of a traceback**
  (HATS-1228). Friendly rendering of the compose-seam errors — unknown role,
  unknown provider, no provider configured — was wired per call site, and
  `cli/reflect.py` composes in five places without a single handler, so those
  `reflect` subcommands exited 1 on a 9-frame trace. (`_handle_role_not_found`
  had listed `ai-hats reflect *` among its covered surfaces since HATS-547; it
  never was.) Dispatch now lives on the root command group, so a surface cannot
  opt out by omission — the 15 per-site handlers are gone and `reflect.py` was
  not touched. In-process invocations (`CliRunner`) get the same rendering as
  the shipped binary, and anything unregistered still surfaces its traceback
  rather than being flattened into a tidy exit 2. (The temporary carve-out
  where `reflect issue` caught `MissingProviderError` was eliminated in
  HATS-1348, so all `reflect` subcommands now render consistently.)

- **An empty `provider:` in `ai-hats.yaml` no longer ends in a traceback**
  (HATS-1224). The compose seam raised a bare `RuntimeError` that no CLI arm
  caught, so `ai-hats`, `--dry-run`, `execute --batch` and `agent` all crashed
  with a 9-frame trace — while the adjacent failure, an *unknown* provider name,
  had exited 2 with the available list since HATS-965. All of them now print the
  same friendly block, name `ai-hats config set -p <provider>` as the fix, and
  exit 2. The message no longer carries the `launch_provider:` prefix (a step
  renamed in HATS-535) or the `materialize_system_prompt:` one — the typed error
  carries its own text, so `config show-prompt` reports it identically.

- **`-p/--provider` is honoured on the batch surfaces** (HATS-1218). The compose
  seam read the configured provider whenever composition was non-interactive, so
  `ai-hats execute -p <x> --batch` accepted the flag and launched the surface
  from `ai-hats.yaml` instead — no warning, no error. **This changes shipped
  behaviour**: that command now runs `<x>`. `interactive` still gates the
  first-run `active_role` write; it no longer decides whether an explicit
  override counts. `ai-hats agent` gains `-p/--provider` (also honoured by its
  `--dry-run`), so a sub-agent surface can be chosen without editing
  `ai-hats.yaml`, and both commands now report an unknown provider the way bare
  `ai-hats` has since HATS-965 — named, with the available list, exit 2, no
  traceback. Internally the two commands stopped hand-wiring one pipeline twice:
  the duplicated funnel seed and post-run report are one shared path, which is
  what let `execute` grow the flag while `agent` silently went without.

- **`ai-hats execute` refuses a flag its mode cannot act on** (HATS-1218). The
  provider override was one case of a class. `WrapRunner` accepts only
  `(extra_args, tags)`, so `--model`, `--isolation`, `--ticket` and `--json`
  were inert under `--interactive` — hedged as "(batch only)" in `--help` and
  enforced nowhere; `SubAgentRunner` takes no `extra_args`, so `--batch`
  swallowed trailing arguments with no note at all. **This changes shipped
  behaviour**: each of those six combinations is now a usage error naming the
  mode, following the existing precedent that `--batch` without `-r/--role` is
  refused at the boundary. Defaults are unaffected — the check reads Click's
  `ParameterSource`, so only a flag you actually typed can be refused.

### Changed

- **The breaking-change protocol's warning release is now scoped** (HATS-1272).
  `docs/RELEASING.md` demanded a runtime `DeprecationWarning` for at least one
  MINOR release before *any* removal from the stable surface. Pre-1.0, a removal
  whose consumer set is known and already migrated may now ship without one: the
  warning release buys an unnoticed consumer a cycle to react, and there is no
  such consumer to buy it for. The exemption is claimed in writing in the
  migration doc — naming each consumer and how it was verified migrated — and a
  consumer set that cannot be enumerated stays ineligible. Steps 2–3 (migration
  doc, `Migration:`-prefixed CHANGELOG entry) remain unconditional, and the
  clause expires at `v1.0.0` with the rest of the pre-1.0 caveat.

- **`SubagentEngine.run` accepts optional keyword argument `artifacts`** (HATS-1207). Custom `SubagentEngine` subclasses receive prebuilt session artifacts (`BuiltArtifacts | None = None`) to avoid recomputing system prompt and plugins.

- **`ai-hats wt exec` runs where you stand** (HATS-1205). It is an environment
  wrapper, not a teleporter: with a cwd inside the worktree the command runs
  *there* instead of being moved to the worktree root (the published shape is
  unchanged — a call from the main checkout still lands at the root). New
  `-C/--cd <subdir>` names a worktree-relative directory from outside, refused
  if it escapes the worktree. `PYTHONPATH` now roots at the project that **owns**
  the run directory — the nearest ancestor with a `pyproject.toml`, bounded by
  the worktree root — so a worktree subproject with its own venv gets its own
  `src` rather than the outer repo's packages, while a plain subdirectory keeps
  the worktree-root workspace. This closes the gap that left "always use
  `wt exec`" unfollowable for a subproject and pushed agents onto absolute
  worktree paths. Rides `ai-hats-wt` 0.4.1, whose `list_active` stops offering
  worktrees git no longer backs (phantoms padded the selector-ambiguity list).

- **hatrack is the default backlog manager** (HATS-1054). `trait-agent` composes
  the `hatrack` skill instead of `backlog-manager` — every library role drives
  the task lifecycle through the `rack` CLI; `backlog-manager` is composed by no
  role (frozen until its retire task). The `hatrack-trait` overlay remains as the
  rollback selector for the classic manager. The symbiosis table is collapsed
  across the library: field edits ride `rack transition --set/--append`,
  hypotheses and proposals ride the `rack hyp` / `rack proposal` groups,
  fast-close is a forced `--state done`, STATE.md sync is the automatic derived
  view, and `rack plan-extract` (new verb over the doc store) ports the last
  tracker-only command — non-interactive, `--dry-run`/`--json`, idempotent via
  child-id stamps. The data migrator now targets the normalized
  `tracker/backlog/hypotheses/` catalog (proposals already normalized; the tasks
  catalog is untouched), and the tracker stores read both the new catalog and
  the legacy flat files.

### Added

- **Batch `rack context ID1 ID2 …`** (HATS-1074). Two or more ids are assembled in
  ONE process, amortizing the fixed interpreter/import start-up tax and the
  `Workspace` discovery walk across the whole set — a consumer (e.g. the hatrack
  fzf-TUI) can prefetch a viewport in one spawn instead of paying the ~136 ms tax
  per id. Measured on the 615-card backlog: a 20-id batch runs ~3.5× faster than 20
  single spawns (fixed tax O(N) → O(1)). One id keeps the legacy **unwrapped**
  payload byte-identical; `≥2` ids return a `{"contexts": {id: …}}` map and
  **skip-and-continue** — a bad id yields a per-id `error` entry while the rest
  resolve (single-id stays fail-fast).

- **Verb-builder `rack` CLI + per-backlog groups** (HATS-1036, ADR-0017 §4/§7).
  The CLI is now built from the backlog definition: `create`'s options are
  generated from `fields[]` (required/choices/default enforced write-strict, so a
  bad value is a typed `invalid_field` refusal, not a click usage error), the
  declared edge names give `transition` a vocabulary — `rack transition HYP-1
  refute` accepts a named edge alongside a state name (a name colliding with a
  state is a typed load error) — and `--set <field>=<value>` / `--append
  <field>=<json>` write declared fields as ops on the one mutating verb. Every
  NON-tasks backlog the workspace mounts becomes a group named by its declared
  `cli_alias` (its `name` when unset; the packaged `hypotheses`/`proposals`
  declare `hyp`/`proposal`), so the verbs layer holds no per-backlog knowledge
  and a duplicate effective group name fails closed: schema-driven
  `create`, an `update <ID> --<field>` sugar mapped onto the `--set` field ops,
  and the verbs its extensions contribute via the optional `verbs()` hook —
  `hyp append-verdict` / `hyp autoclose [--k --dry-run]`, `proposal vote`. The
  base four-verb surface is unchanged until a sibling catalog is mounted (groups
  are resolved lazily from the ambient workspace). `kernel.create` gained a
  generic `fields=` mapping so a custom backlog's required fields (e.g.
  `hypothesis`) flow through one create path; the tasks path stays byte-identical
  (parity-pinned). Old `ai-hats task hyp/proposal <verb>` map word-for-word onto
  `rack hyp/proposal <verb>`.

- **Multi-backlog workspace** (HATS-1044, ADR-0017 §2, §5). N kernels — one per
  backlog — under one `Workspace`: `discover(roots)` scans `tracker/**` for
  `backlog.yaml` catalogs (the tasks catalog is always mounted), routes ids by
  prefix (`kernel_for("HYP-042")`, typed `UnknownPrefixError` /
  `AmbiguousPrefixError`), and answers cross-backlog existence for link kinds
  declaring `targets:` — all three link existence checks funnel through one
  seam. Both sides now observe a link: after the owning card persists, the
  workspace dispatches a post-lock `link-target:<kind>` mirror event to the
  target backlog; the stock `mirror-link` reaction keeps stored inverse pairs
  (`supersedes`/`superseded_by`) convergent, and declaring a stored non-symmetric
  inverse without it is a typed load error. The hypotheses and proposals
  backlogs ship as packaged definitions (promoted from the ADR §5 proofs) with
  stock validators (`hyp-validation-log`, `hyp-exit-criteria`,
  `prop-vote-entries`) and extensions: `hyp-verdicts` (verdict append + atomic
  append-then-transition), `prop-votes`, and the `hyp-quorum-gate` edge handler
  — quorum semantics ported byte-for-byte from the tracker (distinct real
  sessions, `auto-quorum` sentinel excluded, automation-actor-only gating per
  ADR-0009; a manual refute is never gated). A one-shot migration
  (`python -m ai_hats_rack.migration <ai_hats_dir> [--dry-run]`) moves flat
  `HYP-NNN.yaml`/`PROP-NNN.yaml` files to dir-per-card catalogs with an
  inventory diff and idempotent re-runs; the tracker's stores gained a
  dual-layout shim (rack-aligned `.lock` path) so `ai-hats task hyp/proposal`
  keeps working over both layouts, and the reflect/judge pipeline consumers now
  talk to the workspace instead of importing the tracker.
- **Schema-driven card fields** (HATS-1035, ADR-0017 §1–§2). A card's field set
  is declared in `backlog.yaml` `fields[]` — `create` now requires only a
  non-empty `title`; everything else (priority, reviewer, role, tags, …) comes
  from the schema. A field entry carries `type` (`str | int | list | any`),
  `default`, `required`, `choices`, `validator: <name>` (resolved through the
  open registry, fail-closed on an unknown name), and `emit: always | when-set`.
  Writes are strict — `create`, a transition's touched fields, and subscriber
  `Delta.fields` ops all validate against the schema (required / choices / type /
  validator), a typed refusal that persists nothing; reads stay tolerant (an old
  card violating `choices` still loads, surfaced as a `context` warning, never a
  load failure). A per-backlog `extras: allow | forbid` gates unknown keys on
  writes only. `emit: when-set` drops an empty value at persist time (the write
  layer hangs it off the schema; `TaskCard.to_dict` is untouched and a parity pin
  holds the packaged declarations equal to its behaviour for the nine fields).
  The `document` anchor (PROP-012) moved out of the generic topology validator to
  composition-time `requires_states` (ADR-0017 §3), so an HYP/PROP topology with
  no `document` state now loads — proven by ADR §5 fixtures driven end to end.
- **Declaration-bound handlers** (HATS-1043, ADR-0017 §3–§4). `backlog.yaml` now
  says not only which edges exist but *what fires on them*: `states[].on_enter`/
  `on_exit`, `edges[].handlers`/`skip`, and `links.kinds[].handlers` bind
  handlers to events, and the loader expands each to its subscription keys — the
  full forced-inclusive `edge:` product for state slots (HATS-518),
  `link:<kind>`/`unlink:<kind>` for link slots. Every referenced name resolves
  through one open factory registry (unknown name → typed fail-closed
  `UnknownHandlerError`); a reference may pin `priority:` or take a positional
  band into the one total in-lock order. `Delta` grows a declared-`fields`
  surface (set/append, applied in the single persist); `bind(kernel)` and
  `requires_states()` become contract lifecycle hooks (composition validates the
  state vocabulary fail-closed). The stock `stamp-lifecycle`/`clear-lifecycle`
  handlers replace the `kernel._stamp_lifecycle` hardcode, and the packaged
  tasks kit (plan-scaffold, plan-gate, frozen-integrity, stamp/clear, the reopen
  `skip: [plan-gate]`) migrates onto these declarations with the in-lock
  priority chain pinned to today's order — zero behaviour change. Link/unlink of
  a declared kind now dispatches an in-lock `link:<kind>`/`unlink:<kind>` event
  on the owning side (a handler may abort the mutation); kinds without handlers
  dispatch nothing. Handler `timeout:` closes the HATS-1015 liveness hole: the
  hook-runner budget (default 30) is configurable, and worktree git shell-outs
  gain a generous budget (default 60) — a hung subprocess is killed → in-lock
  error → abort + journal. A packaged `backlog-schema.yaml` ships the language-
  independent key grammar (allowed keys per level, reserved keys, the priority
  scale as data) as the single authority — the loader's allow-sets load from it
  so they cannot drift, and the packaged `backlog.yaml` self-lints against it.
- **Gemini native skill discovery** (HATS-993, ADR-0016). `GeminiProvider`
  mirrors the composed role's skills into `.gemini/skills/` — Gemini CLI's
  workspace Agent-Skills tier (>=0.45) — via a new generic ref-counted
  materializer (`ai_hats.skills_dir`, the HATS-981 additive-marker pattern),
  so parallel sessions never sweep each other's skills. The `AVAILABLE
  SKILLS` prompt text-index (HATS-701) is retired for gemini; skills now get
  consent-gated progressive disclosure instead of an always-on prompt tax.

### Changed

- **`rack` read verbs skip the write-lock import** (HATS-1072). `filelock` (and
  the `asyncio` it pulls in, ~30 ms combined at import) is now imported inside
  the write methods that take a lock, not at module top — `ls`/`context` never
  lock, so they no longer pay for it. ~17 ms off warm `rack ls`/`context`;
  output byte-for-byte unchanged.

- **`rack ls` scans ~13× faster on large backlogs** (HATS-1065). The card scan
  was dominated by PyYAML's pure-Python `SafeLoader` (~830 ms parsing 600
  `task.yaml` files per call); rack now reads YAML through libyaml's
  `CSafeLoader` (one `fastyaml.load` home, falling back to the pure loader when
  libyaml is unavailable — correct, just slower). End-to-end `rack ls --all
  --json` on a 600-card backlog drops from ~960 ms to ~210 ms; output is
  byte-for-byte unchanged and no cache is introduced, so create/transition/edit
  stay immediately visible.

- **One `backlog.yaml` defines a backlog** (HATS-1042, ADR-0017). The rack's
  packaged `fsm.yaml` (topology) and `links.yaml` (link kinds) fold losslessly
  into a single `backlog.yaml` and are removed; `load_backlog()` returns one
  immutable `BacklogDefinition` the kernel, CLI, and every subscriber are built
  from. Declared edge `name:`s (`reclaim`, `reopen`) add alias event keys
  additively — canonical `edge:<from>--<to>` keys and all existing subscriptions
  are unchanged. The project-root `links.yaml` override is retired (fold it into
  `backlog.yaml`, fail-closed); unimplemented sections raise a typed load error
  naming the successor task. Zero behaviour change to the CLI, events, or wiring.

### Fixed

- **user-rules reach the agent again** (HATS-1203). `<ai_hats_dir>/user-rules/*.md`
  had exactly one delivery channel — the `@`-import inside the root `CLAUDE.md`
  scaffold — and HATS-1170 stopped writing that scaffold. On greenfield projects
  ai-hats listed every user-rule in `imports.md` and no one read it: no warning,
  and a health check that asserted only that the aggregator *existed*. The rules
  are now read at compose time and emitted as a `## USER RULES` section of the
  composed system prompt, after `## RULES`. Because the section is built in the
  shared provider seam, agy and cline gain a user-rules channel they never had;
  `config show-prompt` and the session prompt are fed by the same funnel, so the
  preview cannot drift from what the session gets.

  The `imports.md` aggregator is **retired** with it — writer, health probes,
  `config status` row and all. Leaving both channels live would have injected
  every user-rule twice in upgraded projects. An existing `imports.md` is swept
  by the MANAGED cleanup on the next refresh, and the migration that drops the
  orphaned root `CLAUDE.md` block no longer skips projects that have user-rules
  (that gate existed only because the block was still their delivery channel).
  `user-rules/` itself is untouched — it remains the hand-authored DATA landing
  zone. `@`-references *inside* rule bodies still reach the model as literal
  text (HATS-1206).

- **An explicit `wt exec` worktree selector beats cwd** (HATS-1213). The selector
  peel ran only in the ambiguity-refusal path, so it was skipped whenever the
  worktree resolved on its own — from inside any linked worktree, and with a sole
  active worktree. The branch name then stayed in the command vector: `wt exec
  task/hats-1193 -- pytest` from inside another worktree ran `task/hats-1193` as
  the program (`Command not found`, rc=127), and under `-C` the refusal named the
  subdirectory of a worktree the caller never asked for — reading as a missing
  subdir rather than an ignored selector. The selector is now peeled before cwd is
  consulted, so it wins from anywhere; an unresolvable one refuses instead of
  falling back. `ai-hats wt env` gains the same optional `[<branch>]` reach-in.

- **Gemini wrap sessions get their session role again** (HATS-993). gemini-cli
  > =0.45 silently ignores `GEMINI_CLI_PROJECT_RULES_PATH`, so the per-session
  > composed role never reached the agent (it fell back to the last-applied
  > `GEMINI.md` role). The session role now rides a `GEMINI.md` inside a
  > session-scoped `--include-directories` dir, verified against live memory
  > discovery. The headless automate path adds `--skip-trust` — gemini hard-fails
  > in non-trusted dirs and isolation worktrees under `$TMPDIR` are never trusted.

## [0.13.2] - 2026-07-10

### Added

- **Provider open-registry + entry-points IoC seam** (HATS-870, T10). The closed
  `PROVIDERS` dict is now an open registry: built-ins self-register at import and
  third parties register via `register_provider()` or the `ai_hats.providers`
  entry-point group — ai-hats discovers and registers an out-of-tree provider
  without importing its package (a broken or duplicate entry point is warned and
  skipped, never fatal). `get_provider()` behaviour is unchanged. Extracting the
  built-in providers into their own packages stays a separate future arc
  (providers remain integrator-bound per ADR-0014 P0 #4).
- **Cline surface plugin** (HATS-956) — `ai-hats-cline`, the first in-tree
  consumer of the provider IoC seam, registers the `cline` CLI as a provider via
  the `ai_hats.providers` entry point (`ai-hats -p cline`). Lives under the new
  `packages/surfaces/` category; ADR-0014 gains a **surface** tier that may
  depend up on the integrator, enforced by the workspace-boundary lint. Inline
  `-s` role delivery, interactive TUI for HITL, headless `--yolo --json` for the
  automate path. A transcript parser (`ClineParser`) and native `.cline/skills/`
  materialization landed as follow-ups — see the `ai-hats-cline` changelog.

### Fixed

- **Unknown `--provider` fails friendly, not with a traceback** (HATS-965).
  `ai-hats -p <unknown>` now reports the bad name and lists the available
  providers instead of surfacing an uncaught `ValueError`.

- **Worktree-isolation gate no longer fires on unrelated repos** (HATS-959). The
  `wt_gate.py` PreToolUse guard classified the *edited file's own* repository, so
  an Edit/Write to a tracked file in a different repo than the session — e.g.
  `~/dotfiles/.claude/settings.json` — was hard-denied, and the recovery text told
  the agent to branch that unrelated repo. The gate now scopes to the session's own
  repository (keyed on the payload `cwd`'s `--git-common-dir`, shared across a
  repo's main checkout and its linked worktrees): a file in a different repo is
  silent, while same-repo main-checkout edits — including editing main from inside a
  linked worktree — still deny. An unresolvable `cwd` (absent / non-git) falls back
  to the prior location-only behaviour, so scoping only ever suppresses a deny,
  never adds one.

- **ai-hats-wt 0.3.0 + integrator pin `>=0.3.0`** (HATS-942 drift). The
  configurable base/merge-target work grew the `ai_hats_wt` public surface
  (`get_default_base_branch`, `get_default_merge_branch`) and edited
  `locks.py` / `manager.py` after 0.2.1 shipped to PyPI, without a bump —
  caught by the HATS-921 drift guard, which resolvers would otherwise have
  ignored while serving fresh installs the stale 0.2.1 wheel. Minor bump
  (new public API); publish rides the release flow.

- **ai-hats-core 0.4.1 + integrator pin `>=0.4.1`, published-version drift
  guard** (HATS-921). `safe_delete.py` was patched twice after 0.4.0 shipped to
  PyPI (concurrent-discard idempotency, unique-tmp atomic write) without a bump,
  so resolvers preferred the stale equal-version index wheel over the fresh
  local build and served fresh installs code missing both fixes. The patch bump
  moves the local source past the published wheel; a new drift-guard test
  (`tests/test_package_version_drift.py`) byte-compares every published
  `packages/*` version against local source and fails on unbumped drift. Until
  0.4.1 is published, fresh installs fail loud ("no matching distribution") —
  deliberate interim (publish rides the release flow).

- **Marker-less pre-marker `.claude/skills/` mirror now auto-heals** (HATS-931).
  A stale project-scope skills mirror written by a pre-marker ai-hats version
  (no `.ai-hats-managed` marker) used to warn about a double skill registration
  every session with no way to clear it — the auto-heal was gated on the marker.
  Session start now treats any project-scope `.claude/skills/<name>` that
  collides with a composed skill as ai-hats-owned (project `.claude/skills` is
  not a user-authoring surface) and sweeps it to the recoverable trash with a
  heal NOTE. Home-scope collisions (`~/.claude/skills`) are still only warned
  about, never touched (HATS-465).

## [0.13.1] - 2026-07-06

### Added

- **Worktree lifecycle effects recorded in the task card** (HATS-866).
  `ai-hats wt create` / `merge` / `discard` now append a structured effect line
  to the card's `work_log/` (branch, worktree path, merge SHA), so the tracker
  carries the worktree history. Routed through a `WorktreeEffects` seam that
  decouples `state` from `wt`.
- **Owner registry + unclaimed-marker sweeper** (HATS-905 phase 1, HATS-910).
  Every mechanism materializing files outside `<ai_hats_dir>` registers an
  `owner_key` in the open registry (`ai_hats.owners`); on `self init`/`bump` a
  generic sweeper (`ai_hats.sweeper`) reclaims artifacts whose colocated marker
  names an unregistered (dead) owner — the HATS-901 forgotten-migration class
  is now healed by the engine. Deletion requires content proof (hash recorded
  in the marker or an embedded ownership string); user-edited files are left
  in place with a WARN. Gated off under version skew and hard-delete mode
  (`AI_HATS_TRASH_DIR=-`), never runs on session-start/`set_role`. The legacy
  `.claude/` publish and skills-mirror cleanups now ride the same shared
  procedures (`skills-export`, `claude-publish` owners), and the publish
  manifest path gained the HATS-907 traversal guard.
- **Hashed `owner_key` marker convention** (HATS-905 phase 2, HATS-911).
  Line-manifest markers are written via `ai_hats.sweeper.write_marker`: an
  `# ai-hats-owner: <key>` header plus a `<sha256-12>  <relpath>` content
  hash per entry — the sweep-time proof that an entry is still engine-owned.
  The live `.githooks/.ai-hats-manifest` now uses this format (readers accept
  both; old hash-less manifests converge on the next rematerialization). A
  coverage test pins every mechanism materializing outside `<ai_hats_dir>`
  to a registered owner; sweep liveness no longer depends on import order,
  and a crashing legacy sweep procedure defers with a WARN instead of
  aborting the bump.

### Fixed

- **Concurrent `ai-hats.yaml` / `customizations.yaml` writers no longer lose
  each other's changes** (HATS-526). Every config writer (customize, `config
  set`, `init`/`bump`, session-start `set_role`, relocate, feedback) loaded
  the file at command start, mutated and saved the whole object — any
  concurrent write since the load was silently dropped (3 parallel
  `customize --add-trait --global` kept 1 of 3). Writes now go through
  `locked_update`: a cross-process `file_lock` (new `ai_hats_core` primitive,
  `filelock`-backed) around a fresh re-read plus only the caller's field
  delta. Contention past 10s exits with a friendly error instead of hanging;
  a static guard test keeps whole-object saves from coming back.
- **Worktree runs and sub-agents resolve the workspace packages** (HATS-913).
  `ai-hats wt exec` and the worktree env thread `packages/*/src` (ai_hats_core,
  ai_hats_wt) into `PYTHONPATH`, so code run inside a linked worktree imports the
  worktree's own workspace sources instead of the main checkout's (or failing to
  import them).
- **A fresh `pip install ai-hats` can no longer resolve stale workspace
  subpackages** (HATS-923, HATS-928). `ai-hats-core` published at 0.3.0 (adds the
  `file_lock` / `LockTimeoutError` RMW-lock helper) and `ai-hats-wt` at 0.2.1
  (adds `WorktreeHook` / `parse_worktree_carry`); the prior 0.2.0 / 0.1.0
  releases lacked these symbols, so a subprocess importing them raised
  `ImportError`. The integrator now floor-pins `ai-hats-core>=0.3.0` and
  `ai-hats-wt>=0.2.1`.

## [0.13.0] - 2026-07-03

### Changed

- **ai-hats-core grew from atomic_io into the kernel** (HATS-862, ADR-0014 T2).
  Moved out of the integrator into `ai-hats-core` 0.2.0: `scrubbed_git_env`,
  the composition value-types (`CompositionResult`, `ResolvedComponent`) typed
  by the new narrow `ComponentKind(RULE, SKILL)` enum, `safe_delete`, and the
  YAML model base (`ai_hats_core.YamlModel`, ex `_YamlModel`). Core's charter
  changed from "dependency-free" to "minimal deps, each load-bearing" — pydantic
  is the first sanctioned dep. The full `ComponentType` taxonomy and `Channel`
  deliberately stay integrator-side (ADR-0014 Amendments, 2026-07-03). The
  integrator now pins `ai-hats-core>=0.2.0`. Direct repoint, no shims.

- **Workspace import-boundary gate** (HATS-869, ADR-0014 T8).
  `tests/test_workspace_boundaries.py` enforces the dependency rule
  (`ai-hats → packages → ai-hats-core`) for every uv-workspace member:
  allowlists derive from each member's own `pyproject.toml`, the declared
  first-party graph is tier-checked (no declare-to-evade), and the integrator
  may import only members it declares. Smoke-marked — rides the pre-push wall.

### Added

- **Duplicate-skill-registration warning at session start** (HATS-901). Claude
  Code registers skills by name, so a same-name dir under `~/.claude/skills/`
  or `<project>/.claude/skills/` silently doubles the session-plugin delivery.
  `ai-hats` wrap sessions now intersect the composed skill names with both
  auto-discovery dirs and surface a pre-launch WARN naming each collision:
  byte-identical to the plugin copy → "safe to remove"; listed in an
  `.ai-hats-managed` marker → auto-healed at session start (HATS-907, below);
  otherwise → "review: remove or rename". Fail-open, HITL sessions only.
- **Session-start auto-heal of the stale skills mirror** (HATS-907). When the
  HATS-901 collision check proves ownership via the project-scope
  `.claude/skills/.ai-hats-managed` marker, the stale mirror is now swept
  pre-spawn instead of asking the user to run `self init` — the current
  session already launches clean. The green startup note names the removed
  skills and the trash path (safe-delete, recoverable). Guards: home-scope
  `~/.claude/skills` is never touched (user-owned, HATS-465); no heal from a
  binary behind upstream (version-skew, as HATS-833) or under
  `AI_HATS_TRASH_DIR=-` (removal would be unrecoverable); marker entries are
  validated as plain child names, so a committed hostile marker cannot
  traverse outside `.claude/skills/`.

### Fixed

- **Legacy `.claude/skills/` mirror removed on heal** (HATS-901). HATS-294
  (v0.7) dropped the permanent skills export but left already-materialized
  project-level mirrors behind — frozen at their last export and
  double-registered alongside the session plugin ever since. `self init` /
  `self update` now discard exactly the skill dirs listed in
  `.claude/skills/.ai-hats-managed` (plus the marker) via the safe-delete
  trash bin; user-authored entries survive.

## [0.12.0] - 2026-07-02

### Added

- **Standalone `ai-hats-core` + `ai-hats-wt` packages** (HATS-885). The atomic
  filesystem-I/O core primitives and the hook-agnostic git-worktree engine are
  extracted into two independently-versioned PyPI packages; `ai-hats` now depends
  on them (`ai-hats-core>=0.1.0`, `ai-hats-wt>=0.1.0`) and `ai-hats self update`
  pulls them transparently. The worktree engine is importable standalone as
  `ai_hats_wt` (`WorktreeManager` + the L1–L4 lock model) against a bare git repo
  with zero ai-hats config.
- **Tool-call-hygiene `PreToolUse` guard** (HATS-632). The `tool-call-hygiene`
  skill now ships a non-blocking `PreToolUse` Bash runtime hook: when a command
  is a pure invocation of `grep`/`find`/`cat`/`sed -i`/… that a dedicated tool
  covers, it injects an `additionalContext` nudge toward Grep/Glob/Read/Edit
  without blocking the command or prompting the user. Conservative by design —
  any pipe / redirect / chained command is left alone. Kill switch:
  `AI_HATS_TOOL_HYGIENE_OFF=1`. This is the first in-library `runtime_hooks`
  consumer, setting the shared `stdin tool_input → JSON hookSpecificOutput`
  convention for the behavior-hook family.
- **Python security-lint `PostToolUse` hook** (HATS-660). A new `py-security-lint`
  skill (composed by the `dev::python` trait) runs `ruff check --isolated --select S`
  (flake8-bandit security rules) on every `.py` you Edit/Write and forwards any
  findings to the agent via a non-blocking `additionalContext` note — an early,
  edit-time security layer that complements (does not replace) the project's CI
  lint. Zero egress, fail-open when `ruff` is absent. Kill switch:
  `AI_HATS_SECURITY_LINT_OFF=1`.

## [0.11.0] - 2026-06-26

Headline: epic **HATS-835 — worktree lifecycle & merge robustness**. A sweep
that hardens the git-worktree lifecycle (create → merge → teardown → tracker
consistency) against the failure modes that silently lost or corrupted state.

### Fixed

- **`task transition done` tolerates an already-merged, state-lost branch**
  (HATS-697). When work shipped on the base out-of-band (manual `git merge
  --no-ff task/<id>`) and/or the auto-worktree was removed by hand, `done`
  refused with a false `worktree state lost` ("un-merged commits") even though
  the branch was fully integrated. It now detects the already-merged branch,
  finalizes without a re-merge, and cleans up the stale ref; only a genuinely
  divergent branch still refuses (the silent-data-loss guard stays intact).
- **Forced `execute` spins no fresh worktree** (HATS-697). `transition execute
  --force` is a manual state correction; it no longer creates a worktree off
  HEAD that orphaned retrospective shipped-on-master work in the main tree.
- **In-worktree `transition done` / `close` is refused before teardown**
  (HATS-788). Running it from inside the task's own linked worktree used to
  delete the cwd and leave the CLI resolving a phantom tracker (false `task
  not found`); it now refuses with guidance and preserves the worktree.
- **No phantom tracker on a wrong-but-alive root** (HATS-839). `<ai_hats_dir>`
  is no longer created unconditionally, which had resurrected a phantom
  `.agent/` tracker and drove the HATS-788 id-collision.
- **Worktree-adopt short-circuit works from inside a worktree** (HATS-840).
  The HATS-060 adopt path no longer no-ops on a hopped `_project_dir`, so it
  adopts the caller's worktree instead of spinning a fresh one off main.
- **Typed refusal for `original_branch: null`** (HATS-714). `wt merge` /
  `task transition done` raise an "incomplete worktree state" error naming the
  field instead of an opaque `TypeError`.
- **`execute --batch` without `--role` fails cleanly** (HATS-827), instead of
  crashing on an invalid `agent//<session>` worktree branch.

### Added

- Capstone e2e matrix `test_worktree_lifecycle_robustness_matrix.py` asserting
  the epic's invariants hold together on the real launcher + binary.

## [0.10.0] - 2026-06-20

### Added

- **Self-location guard + out-of-band recovery + stray-shadow detector**
  (HATS-791, child of HATS-786). Closes the residual "shadow" case HATS-790's
  generator removal left open: a stale ai-hats running from a FOREIGN
  (non-managed) venv reached ahead of the host launcher. A pure classifier
  `ai_hats.self_location.classify_invocation` (`"sanctioned"` / `"foreign"`),
  wired by `_guard_self_location` into `main_entry`, **refuses-and-instructs**
  on a foreign invocation — prints `remediation_text` (run the host launcher /
  re-bootstrap / uninstall from the offending venv) to stderr and exits 3. It
  biases HARD toward fail-open (only a positively-identified foreign venv that
  ACTUALLY EXISTS as a resolvable managed venv is refused; every ambiguity,
  editable dev clone, or `--version`/`--help`/`--tree` info command resolves to
  sanctioned), is wired into `main_entry` (not the `main` click group, so
  in-process `CliRunner` tests bypass it), and has an escape hatch
  `AI_HATS_SKIP_SELF_LOCATION_GUARD=1` (`SKIP_ENV_VAR`). `scripts/bootstrap.sh`
  becomes the canonical **out-of-band recovery** hatch — paradox-immune because
  it is fetched fresh (`curl … | bash`) and drives the launcher by ABSOLUTE path
  (`"$LAUNCHER_DEST"`), so a shadow cannot intercept it — with a new `--repair`
  flag that force-reinstalls the launcher + the framework-managed default venv
  (`.agent/ai-hats/.venv` + `versions/`, never a user override). Both
  `bootstrap.sh` (`detect_stray_launchers`) and `ai_hats.cli.maintenance`
  (`find_stray_launchers`) scan `$PATH` for stray `ai-hats` binaries outside the
  sanctioned launcher and WARN — never delete.
- **Forward-safe `ai-hats.yaml` reader — preserve unknowns, fail loud on a newer
  schema** (HATS-792, child of HATS-786). `ProjectConfig` now round-trips a
  same-version unknown top-level field instead of dropping it: `from_yaml`
  stashes the pre-stripped unknown keys on an `_extra` `PrivateAttr` and
  `to_dict` merges them back (mirrors `TaskCard.extras`), so an OLDER ai-hats
  preserves (does not silently delete on `save()`) a field a NEWER ai-hats wrote
  without a `schema_version` bump — while the HATS-581 stderr WARN still fires.
  A genuinely newer schema fails loud: `from_yaml` raises `ProjectConfigError`
  pointing at `ai-hats self update` when on-disk `schema_version` exceeds
  `KNOWN_SCHEMA_VERSION` (4), and a matching `save()` clobber guard refuses to
  overwrite a file whose on-disk schema is newer than this binary knows.

### Removed

- **Migration:** see [`docs/migration-v0.10.0.md`](docs/migration-v0.10.0.md) for
  the one-time crossover (reinstall the host launcher; clear stray app-venv
  installs). **Removed the `ai-hats` console-script entry point; `python -m
  ai_hats` is now the sole package entry** (HATS-790, Alt 5). The `[project.scripts] ai-hats =
  "ai_hats.cli:main_entry"` generator made every venv depending on `ai-hats`
  materialise a `bin/ai-hats` that direnv could prepend ahead of the host
  launcher (`~/.local/bin/ai-hats`), silently running stale code. With the
  generator gone, no venv produces `bin/ai-hats`; the bash launcher now execs
  `<venv>/bin/python -m ai_hats "$@"` and probes venv health/usability via
  `bin/python` + a `python -c "import ai_hats"` import probe rather than the
  removed console-script proxy. `is_usable_version` / `read_current_sha`
  (`paths.py`) drop the `bin/ai-hats` clause and key on the `.complete` sentinel
  - `bin/python` (behaviour-equivalent for any real install). `python -m ai_hats`
    routes through `main_entry` so `--tree` / `--help --tree` ordering is identical
    to the old console entry. The host launcher remains named `ai-hats` and on
    `$PATH` — only the per-venv generated binary is gone.

## [0.9.0] - 2026-06-17

### Added

- **Release CI to PyPI via OIDC trusted publishing** (HATS-765, child of the
  HATS-762 distribution overhaul). A new `.github/workflows/release.yml` builds
  the wheel + sdist with `uv build` and publishes to PyPI on a `v*` tag push via
  tokenless OIDC trusted publishing — build and publish are split into two jobs
  so the `id-token: write` privilege is held only by a publish-only job. This is
  the artefact that makes the `stable` channel real: end users install a
  prebuilt `ai-hats==<version>` wheel instead of a git source build. A
  self-skipping live e2e (`tests/e2e/test_stable_channel_live.py`) exercises a
  stable-channel `self update` against the real PyPI index (skips until the name
  is published). `docs/RELEASING.md` documents the trusted-publisher one-time
  setup and the post-publish verify step.
- **`ai-hats session show` renders a Usage section from `usage.json`** (HATS-734,
  child of HATS-699 / HATS-698 audit) — the HATS-664 producer (`compute_usage`)
  had zero in-src consumers, so a producer regression (the resume-mode discovery
  bug fixed below) was invisible for months. `session show` now renders a
  fail-soft Usage block (measured/static always-on, `skill_loads`, tool
  success-rate, sidechain, parser flags) and lists `usage.json` among the
  session artefacts, making the channel falsifiable.

### Changed

- **Deleted the dead lifecycle `hooks:` composition channel; re-homed the one
  real consumer** (HATS-707, child of HATS-699 / HATS-698 audit). The
  role/trait `composition.hooks` channel (`CompositionResult.hooks`,
  `HooksConfig`, the `LifecycleEvent` enum, `composer._merge_hooks`, and
  `HooksRunner`) was composed and displayed in `config status` but had **zero**
  runtime execution consumers — `HooksRunner` scanned `library/hooks/` by
  filename convention (empty of lifecycle scripts since HATS-314) and never
  read `result.hooks`; `TASK_*` events never fired. `config status` no longer
  advertises a hook subsystem that never runs. The single piece of real intent —
  the maintainer's `session_start: [ai-hats self sync-hooks]` git-hook drift net
  (HATS-593 layer B), itself silently dead — is re-homed to a direct
  `WrapRunner._resync_git_hooks()` call at session start, for every role
  (idempotent, fail-open). Existing user configs with a `hooks:` block are
  unaffected (`Composition` is `extra="ignore"`; no migration). `RunSessionEnd`
  is now the retro-banner-only finalize step.
- **Claude system prompt no longer carries the `AVAILABLE SKILLS` index**
  (HATS-701, audit F2 of HATS-698, child of HATS-699 — harness optimization).
  `ClaudeProvider.build_system_prompt` appended a skills index built from
  `SKILL.md` frontmatter (5,988 chars for the 22-skill maintainer role) while
  the *same* session already passes the composed skills via `--plugin-dir`
  (HITL) / SDK plugin (sub-agent) — Claude Code natively lists every plugin
  skill with its full description, so the index was a 2-3x duplicate. It is
  now suppressed for Claude (returning ~1.5k tokens to the context window on
  every session and every sub-agent spawn, with less selector noise from the
  duplicate qualified/unqualified listings) and kept for Gemini, which has no
  native skill registry. The two near-identical `build_system_prompt` bodies
  are de-duplicated into `Provider._compose_sections(result, *, include_skills)`;
  `show-prompt` now mirrors the real (index-free) Claude prompt.
- **Trimmed the always-on `## RULES` block ~2.1 KB** (HATS-702, child of
  HATS-699 / HATS-698 audit). The block ships verbatim in every composed
  prompt for every role and consuming project — the one cost nobody can opt
  out of. `rule_pause_before_shared_state_write` (3,005 → 1,542 chars) drops
  the incident-narrative rationale and worked example (→ HYP-026 / HYP-027 /
  PROP-052 pointer) while keeping every behavioral clause, the command/
  reversibility table, and the hook-backstop + ACK warning — enforcement is
  unchanged (`pre_bash_shared_state_guard.sh` is wired unconditionally).
  `rule_composition_value_contract` (1,690 → 1,064 chars) compresses its four
  invariants to one-liners + ADR pointer, and its stale `providers.py` budget
  comment is corrected (`~600` → `~1.0 KB`). Net: block 8,149 → 6,060 chars,
  ~520 fewer tokens on every session and sub-agent.
- **Maintainer role injection deduped against its traits** (HATS-703, child of
  HATS-699 / HATS-698 audit — finding F4). The `maintainer` role injection
  re-described its own traits and restated the `brainstorm→…→done` workflow that
  `trait-agent` already delivers. Dropped the redundant `## Workflow` (covered by
  `trait-agent` Agent Protocol + `trait-base` pessimistic-verification /
  concise-communication) and `## Delegation` (near-verbatim `trait-agent`
  `### Delegation`); moved the author-facing "what sets this role apart"
  meta-section to a YAML comment so it no longer spends agent prompt budget. The
  no-`Co-Authored-By` commit-trailer policy now has a single tracked home — the
  `ai-hats-maintainer` trait — removing the contradictory-precedence risk of the
  former 3-way duplication. Role injection is now header + intro + `## Guardrails`
  (~0.5–1 KB/session saved). The three HATS-452 prompt-content e2e tests re-point
  their role-own-injection marker from `## Workflow` to the role intro string.
- **Skill bodies are no longer eager-loaded on every compose** (HATS-706, child
  of HATS-699 / HATS-698 audit). `Composer.compose` read each skill's full
  `SKILL.md` into `ResolvedComponent.injection` for every session and both
  providers, yet the *only* consumer of a skill's body is `ai-hats reflect`'s
  role-mirror (`_materialize_target_composition`) — the Gemini `AVAILABLE
  SKILLS` index reads its own single copy via `_extract_frontmatter_description`.
  The eager read is removed; reflect now reads the body on demand from the
  skill's `source_path`. Non-reflect sessions no longer pay one `SKILL.md` read
  per skill for a body they never use, and the per-skill double read on Gemini
  prompt builds collapses to one. No change to prompt output or reflect
  artefacts. (The card's other half — hoisting the identical `build_system_prompt`
  into the `Provider` base — was already delivered by HATS-701.)

### Removed

- **`ai-hats self clean` command** (HATS-709, child of HATS-699 / HATS-698
  audit — finding 2a-F3). A total no-op on v4: framework content is composed
  in memory (HATS-294), so the rules/skills mirrors it wiped are empty, the
  legacy `.agent/{skills,hooks}` it swept don't exist, and the `.ai-hats-managed`
  manifest its sweep read was never written (`_write_managed_manifest` had zero
  callers). The only materialized managed content (`library/hooks`) is owned by
  `_refresh`. The undocumented command and its dead helper chain
  (`Assembler._clean` / `_clean_non_local` / `_clean_managed_entries` /
  `_write_managed_manifest` + the unreachable `preserve_local` branch and
  `.library_rules` marker protocol) are removed (~90 LOC). Re-materialize a
  project's managed tree via `ai-hats self init` / `self update`.
  Migration: [`docs/migration-v0.9.0.md`](docs/migration-v0.9.0.md) §4 — the
  command was a no-op; drop any calls and use `self update` / `self init`.
- **Write-only `pipeline_metrics.json` telemetry** (HATS-736, child of
  HATS-699 / HATS-698 audit — dead-delivery class #5). `PipelineHarness.__exit__`
  wrote a per-run `pipeline_metrics.json` (terminal zero-output / timeout
  incident counters) into a namespace GC'd after `AI_HATS_PIPELINE_KEEP_N`
  (default 10) runs, with **zero readers** in `src/` — the data expired
  unread. Folding it into the session `metrics.json` (read by
  `session list --json`) was rejected: the harness has no map from its
  `session_id` to a spawned session dir, and the signal is already
  observable — per-session `timed_out` lives in session `metrics.json` and
  `HarnessReliabilityError` is routed to a meta-PROP by reflect-session. The
  writer, its dead imports, and its 5 unit tests are removed; `__exit__` is
  now a no-op (artefacts are still kept and GC'd at the next `__enter__`).

### Fixed

- **Two real-subprocess e2e files now run in the pre-push gate that protects
  master** (HATS-746, audit 4b-F4 of HATS-698). `tests/e2e/test_wave1_free_tier.py`
  (3 free-tier pilots) and `tests/e2e/test_wt_merge_ambiguity_guard.py` (2 tests —
  the HATS-502 `wt merge` ambiguity foot-gun guard) lacked
  `pytestmark = pytest.mark.integration`, so the gate's
  `-m "(integration or smoke) and not quarantine"` selection **deselected** them;
  they survived only by accident in CI Job 1's `not integration` pool. Adding the
  marker pulls all 5 into the gate (deliberate coverage increase) — a regression
  in the foot-gun guard no longer ships to master silently. Also deleted the
  dead `external_env` pytest marker (declared in `pyproject.toml`, zero uses
  repo-wide), and recorded on HATS-695 that the two quarantined `self update`
  pip tests have zero automated coverage (gate deselects via `quarantine`, CI
  Job 1 via `integration`, CI Job 2 via `--ignore=tests/e2e/`) until that task
  de-flakes and un-quarantines them.
- **Pipeline engine raises a typed `StepError` for a required ctx key absent at
  runtime, instead of a bare `KeyError`** (HATS-739, audit 2c-F8 of HATS-698).
  `_run_steps` projected `requires` (`kwargs = {k: state[k] for k in s.io.requires}`)
  *before* the per-step `try`, so when a producer legally omitted a *declared*
  `produces` key at runtime (None-filtered merge; `ComposeRole` emits `{}` for no
  role — ADR-0005 value contract), the missing-key lookup raised a context-free
  `KeyError` that bypassed both `failure_policy="continue"` and the `_emit` trace
  event. The projection is now non-raising and an explicit presence check raises a
  `StepError` naming the step + missing keys *inside* the `try`, so trace and
  continue-policy semantics apply. Latent (no shipped pipeline pairs an omittable
  produce with a downstream require) but the exact contract seam a custom-YAML
  pipeline author would hit.
- **`compute_usage` no longer skips `usage.json` in resume/continue sessions**
  (HATS-734, audit 2c-F3 of HATS-698). `ComputeUsage.run` passed
  `claude_session_id` — a `uuid4` — to `_discover_claude_jsonl`, which parses
  `session_id[:15]` with `strptime("%Y%m%d-%H%M%S")`; a uuid never parses, so the
  JSONL discovery fallback returned `None` and the step silently exited — in
  exactly the `--resume`/`--continue` scenario the fallback (HATS-272) exists
  for. It now passes the ai-hats `session_id`, converging on the `make_audit`
  sibling. The breakage stayed invisible because `usage.json` had no reader —
  see the matching Added entry.
- **`task transition --final-state` refuses non-review targets and persists
  atomically** (HATS-723, child of HATS-699 / HATS-698 audit) — the flag was
  applied only for the `review` target; on any other target it was parsed and
  silently dropped (option-parsed-then-ignored). It now exits 1 with a clear
  error. Separately, the summary was written in its own lock *before* the
  transition, so a transition that then raised (FSM guard, empty-plan, worktree
  errors) left a half-applied card; `final_state` now rides the transition's
  single lock window. The dead `TaskManager.set_final_state` (now without a
  production caller) was removed.
- **`_pty_spawn` no longer pollutes the parent process environment** (HATS-713,
  child of HATS-699 / HATS-698 audit) — it looped `os.environ[k] = v` over the
  per-session env, permanently leaking keys (`AI_HATS_SESSION_ID`,
  `AI_HATS_ROLE`, provider vars) into the parent. Those stale keys reached the
  finalize pipeline, `SESSION_END` hooks, and any later `WrapRunner.run` /
  in-process test in the same process. The per-session env is now passed to the
  child via `PtyProcess.spawn(..., env={**os.environ, **env})`; the child sees
  the same effective environment, the parent `os.environ` is left untouched.
- **`wt merge` no longer hangs forever on an unreachable remote** (HATS-711,
  audit 2a-F5 of HATS-698). `WorktreeManager._check_drift` runs a pre-merge
  `git fetch origin <base>` — a network call — while holding the per-branch
  lifecycle lock, but `_git` had no timeout. A hung fetch (dead VPN / DNS
  blackhole) wedged `merge()` unboundedly; concurrent `wt merge` / `wt discard`
  peers then timed out at the 60s lifecycle lock and failed with a
  `WorktreeLockError` blaming a phantom "concurrent `wt merge`/`wt discard`".
  The fetch is now bounded by `FETCH_TIMEOUT` (30s); a timeout is treated like
  the existing fetch-failure path (WARN + local-only drift check, merge
  proceeds) and named explicitly so triage starts at the network, not at
  phantom concurrency. Other (local) `_git` calls are unchanged.

## [0.8.0] - 2026-06-07

### Added

- **`compute_usage` step + `usage.json` per-session context-cost report**
  (HATS-664, first child of HATS-663 session-observability epic) — a transcript-
  first parser that turns one Claude Code JSONL session into a machine-readable
  `usage/v1` report: measured always-on budget (first `cache_creation` proxy), an
  ordered event timeline (skill-body loads via `Skill` tool_use, reference Reads
  of `*/references/*.md` + `SKILL.md`, tool calls with `is_error`, stop-hook
  firings), aggregates with tool success-rate, and sub-agent sidechain linkage
  (detect + link by `sessionId`/`sourceToolAssistantUUID`, no per-event token
  merge). The report also self-describes its ai-hats context — `role` /
  `provider` / `exit_code` copied from the session's `metrics.json` (so the
  comparison sibling pairs sessions by role and "what went wrong" debugging reads
  it in one place); when `role` resolves, a static `costs.py` per-component
  always-on breakdown is attached under `always_on.static` for a measured-vs-
  static cross-check. The pure `parse_session_usage` (`src/ai_hats/usage.py`) is
  transcript-only and fail-soft (malformed line / unknown entry type → `flags`,
  never a crash — verified over all ~550 historical transcripts with zero
  crashes) and
  doubles as a bash-composable primitive (`python -m ai_hats.usage <jsonl>`,
  JSON to stdout) for retroactive sweeps. The `ComputeUsage` step is the thin
  live driver — sibling of `make_audit`, same post-session JSONL, wired right
  after it in both `finalize-hitl` and `finalize-subagent` — so every new
  session writes `<session_dir>/usage.json` alongside `audit.md`/`metrics.json`.
  Reproduces the HATS-578 finding automatically (skill-BODY loads are rare —
  ~20% of sessions; `backlog-manager` + `self-retrospective` dominate). Per-event
  token attribution is a documented `reconstructed` heuristic (per-message usage
  is a per-turn total); unattributable events keep `tokens_delta = null`, never a
  magic `0` (honors `rule_composition_value_contract §3`).
- **`devils-advocate` skill + conditional "Approach & counter" plan section**
  (HATS-621, M3 of HATS-629) — the value-counter stage of the plan-gate. A new
  `required=False` `Approach & counter` section sits between `Requirements` and
  `Scope & Out-of-scope` (`PLAN_SECTIONS`); the engine never blocks `execute` on
  it (the "non-trivial plans fill it or write explicit `N/A`" norm is behavioural,
  carried by the skill + companion HYP). The `devils-advocate` skill ships the
  4-step skeptic method — steelman the value → name the unstated assumption →
  counter it (*needed? missed anything? another way?*) → assess impact — and is
  wired into `trait-agent`. `plan-gate` documents the
  `requirements-interview ⇄ devils-advocate → design-minimalism` flow, with
  cross-refs in both sibling stages. Catches "right scope, wrong direction" — the
  failure mode neither `requirements-interview` (WHAT) nor `design-minimalism`
  (HOW MUCH) catches.
- **`plan-discipline` skill** (HATS-643) — the named discipline for the plan-home
  invariant: a plan is always a task, authored directly into the canonical
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`, and never routed through
  `.claude/plans` (inert plan-mode scratch ≠ the plan). Carries the draft→tracker
  transfer procedure and hands off to `plan-gate` for section filling; the engine
  per-section gate (HATS-635) remains the enforcement backstop. Wired into
  `trait-agent`. Closes the plan-mode→`.claude/plans` salvage loophole left after
  HATS-637 at the discipline layer. `backlog-manager` and `rule_backlog_discipline`
  now point here instead of duplicating the flow. Covers the Claude Code plan-mode
  two-phase reality (HATS-644): plan mode is read-only, so the `.claude/plans`
  draft is expected Phase-1 scratch and the mandatory first post-approval action is
  to transfer it into the tracker `plan.md`; when plan mode isn't forced, plan
  directly in the tracker.
- **`ai-hats task hyp create --verification-protocol TEXT`** (HATS-623).
  `library-change-hypothesis-protocol` mandates a `verification_protocol`
  field on companion HYPs, but `hyp create` exposed no flag and
  `rule_backlog_discipline` forbids editing `hypotheses/*.yaml` directly —
  so HATS-616 had to fold the protocol text into `--success-criterion`. The
  `Hypothesis` model is already `extra="allow"` and persists via
  `model_dump(exclude_none=True)`, so the field round-trips with no model or
  storage change; the flag is dropped from the YAML when omitted. Consumed
  by `reflect`/session-reviewer handoff (HATS-534).
- **`dev-web` role — web/frontend development (JS/TS + React)** (HATS-616).
  Fills the one real library gap from the awesome-claude-skills review (no
  web role; Go had 40+ skills). Shape mirrors `dev-python` + `dev::python`:
  a single role `dev-web` over one gear `dev::web` (not a `go-dev`-style
  multi-gear split). The gear carries JS/TS + React + a11y + tooling
  conventions and bundles two seed skills: `ui-ux-review` (two-mode —
  guide + P0/P1/P2 review — cognitive UX rules, distilled from
  oil-oil/oiloil-ui-ux-guide [Apache-2.0] and wondelai/skills [MIT]) and
  `webapp-testing` (Playwright recon→act→assert browser verification,
  distilled from anthropics/skills [Apache-2.0]). `task_complete` gates:
  `npm run lint/test/build` + `npx playwright test`. Companion HYP-056
  tracks the expected behavior shift. Per-source licenses + attribution
  recorded in each skill's `metadata.yaml` `upstream:` block.
- **Skills can declare provider runtime hooks** (`runtime_hooks:` in a
  skill's `metadata.yaml`, HATS-597 / HATS-601). Mirrors the `git_hooks`
  open registry: a composed skill declares hooks keyed by Claude event
  (v1: `PreToolUse`, `PostToolUse`), each row `{matcher, script}`. On
  `self init` / `self update` the assembler materializes each declared
  script to `<ai_hats_dir>/library/hooks/<skill>-<basename>.sh` (`0o755`,
  manifest-tracked, swept when the skill leaves the role) and
  `ClaudeProvider` wires one managed `.claude/settings.json` entry per
  `(event, skill, matcher)`, tagged `ai-hats:<skill>:<event>:<matcher>`.
  A hook whose script cannot be resolved is skipped on both sides, so
  settings.json never points at a missing file. User-authored hook
  entries are never touched; Gemini is a no-op. The hard-coded HATS-437
  shared-state guard path is unchanged (its migration onto the registry
  is HATS-598).
- **Migration safety chain — backup-first + smoke-assert + user-hooks
  namespace** (HATS-549). Hardens `ai-hats self update` /
  non-greenfield `self init` against data-loss regressions of the
  class that produced the proxmox failure mode (user-authored
  `.agent/hooks/pre_bash_secret_guard.py` silently deleted by an
  older bump codepath, healer auto-rewriting the orphan ref in
  `.claude/settings.json`, every Bash tool call thereafter printing
  `/bin/sh: <path>: No such file or directory`). Four phases:
  - **Phase 1 — pre-bump snapshot** (`src/ai_hats/migration_backup.py`).
    Before any destructive step runs, snapshots the ai-hats-managed
    surface (`.agent/`, `.claude/settings*.json`, `ai-hats.yaml`,
    `CLAUDE.md` / `GEMINI.md`, `.githooks/`, `.gitignore`) to
    `/tmp/ai-hats/bump-backups/<utc-ts>-<slug>-<label>.tar.gz`.
    Path printed to stderr with `Recovery: tar -xzf <path> -C
    <project>` one-liner BEFORE any work starts. Retention sweep
    keeps last 10 per project-slug. Excludes `.venv` /
    `__pycache__` / `.cache` / `node_modules` / `*.pyc` / symlinks
    (regenerable / safety risks). Hard-fail on
    `BackupError`: proceeding without a snapshot defeats the
    safety guarantee. Env knobs: `AI_HATS_BUMP_BACKUP_DIR=<path>`
    overrides base dir; `AI_HATS_BUMP_BACKUP_DIR=-` hard-disables
    (one stderr WARN per call, for CI / sandbox).
  - **Phase 4 — `user-hooks/` namespace + disable-vs-rewrite**
    (`paths.user_hooks_dir`,
    `Assembler._migrate_layout_v4_hooks_partition`,
    `migration_healer._disable_user_hooks_in_settings`).
    Project-authored files under legacy `.agent/hooks/` (anything
    whose basename is NOT in `_ai_hats_owned_hook_basenames()`)
    relocate to `<ai_hats_dir>/user-hooks/` — disjoint from the
    managed `library/hooks/` namespace. The matching
    `.claude/settings.json` PreToolUse entry is REMOVED (not
    auto-rewritten); Stage B inventory carries a copy-paste JSON
    re-enable snippet. A second reconciliation pass walks
    `library/hooks/` for foreign content that landed there via a
    pre-HATS-549 auto-heal and relocates it to `user-hooks/` —
    next bump heals stuck states inherited from prior versions
    transparently.
- **Install diagnostics in `ai-hats config status` Health section**
  (HATS-497). `config status` now prints install-level fields
  alongside the existing project-side health checks: `Version`,
  `Interpreter` (Python executable + version), `Venv`, `Source`
  (editable / pinned / git, with ref and short SHA where applicable),
  `Library` path, `Resolved via` (heuristic over `AI_HATS_VENV` env >
  `ai-hats.yaml` `venv_path` > default), and `Repo HEAD` (editable
  installs only — short SHA + branch + clean/dirty). Pip-managed
  `direct_url.json` (PEP 610) is the source of truth for `Source`;
  HATS-496's `--revision` writes the ref that lights up the "pinned @"
  display. Refactor: the Health block now prints regardless of whether
  a role is active — install info is useful before init too (e.g.
  troubleshooting "what version am I on, where does it live" on a
  fresh checkout).
- **Docs: dev-vs-runtime venv discipline in CONTRIBUTING.md**
  (HATS-494). New `### Stable runtime vs editable dev install`
  subsection under `## Development setup` codifies the
  two-venv pattern (`AI_HATS_VENV` env override + `ai-hats self update
  --revision <REF>` to pin the stable venv to a known-good tag), with
  caveats about editable installs (frozen `pyproject.toml`, hardcoded
  repo path in the meta-path finder, generated `_version.py`,
  `direct_url.json` editable protection). Solves the dogfooding paradox
  where the harness driving a Claude Code session and the install
  under test are the same editable `.venv`.
- **`ai-hats self update --revision <REF>`** for pinned installs
  (HATS-496). Accepts a tag, branch, or commit SHA and installs ai-hats
  at exactly that ref instead of remote master. Unblocks reproducible
  QA, bisect, and "test against last known-good release" workflows
  without manual `pip install --force-reinstall git+...@<ref>`
  incantations. Bypasses the HATS-441 ahead/diverged downgrade guard
  with an explicit yellow WARN (the user asked for an arbitrary ref, so
  the guard would only obstruct). On an editable target venv — i.e. the
  `pip install -e .` dev loop inside the ai-hats repo itself — refuses
  unless `--force` is passed, with a message that points at the
  `AI_HATS_VENV` env override as the non-destructive alternative.
  Pre-flight `git ls-remote` validates the ref before any pip call so
  typos fail fast (~1s) instead of after a 30s pip clone. Pip-managed
  `direct_url.json` (PEP 610) records the literal ref and resolved SHA
  for later introspection — no custom marker files. New flags:
  `--revision REF`, `--force`.
- **e2e framework Wave 1 — `tmp_project` + `tmp_venv_project` fixtures**
  (HATS-478). Two reusable pytest fixtures plus a `tests/e2e/README.md`
  unlock 51 of the 69 Core e2e scenarios (32 free-tier CLI + 19
  venv-tier launcher) — contributors writing a new e2e test now pick a
  tier and write ≤10 LOC of body, instead of plumbing ad-hoc setup per
  PR. `tmp_project` is function-scoped, role-less, $0; `tmp_venv_project`
  is module-scoped and amortises the ~30-60s launcher install. New
  helper `tests/e2e/_helpers/venv.py` exposes `build_launcher_venv()`
  for callers that want raw access.
- **`safe_delete.replace(mode=...)` kwarg** (HATS-467) — optional
  octal permission bits applied to the temp file BEFORE the atomic
  rename, so executables (e.g. PreToolUse hooks) appear at the
  destination already with the right bits — no window where the file
  exists with default umask perms. Backward-compatible (`mode=None`
  keeps current behaviour). Bytes-identical no-op explicitly does NOT
  enforce the mode (skip path doesn't call `_write_atomic`).
- **Safe-delete trash bin** for all destructive ops under `src/ai_hats/`.
  New module `ai_hats.safe_delete` with `discard()` / `replace()`
  module-level API: instead of `path.unlink()` / `shutil.rmtree()` /
  in-place `path.write_text(new)`, victims are moved (or snapshotted,
  for overwrites) to `$TMPDIR/ai-hats/trash-<utc-ts>-<pid>/<relpath>/`
  before the original is touched. Recovery is `cp -r <session>/<rel>
  <project>/<rel>`. Each session writes a `MANIFEST.md` listing every
  op with timestamp + reason + original→trash mapping (HATS-470).
- **`AI_HATS_TRASH_DIR`** env var to override the trash base directory.
  Special sentinel `AI_HATS_TRASH_DIR=-` enables hard-delete mode (no
  snapshots, WARN to stderr per op) — intended for CI / ephemeral
  environments where snapshot value is zero. ENOSPC / read-only
  filesystem on snapshot raises `TrashFullError` and aborts the
  destructive operation rather than silently losing data (HATS-470).
- **Pre-commit hook** (`git-mastery` skill,
  `pre-commit-no-raw-destructive.sh`) forbidding raw `unlink` /
  `rmtree` / `rmdir` under `src/ai_hats/` outside `safe_delete.py`.
  Inline `# safe-delete: ok <reason>` markers on the offending line
  act as reviewer-visible bypasses for ephemeral / framework-state
  cases (git worktree state, session cache, empty-dir cleanup). No-op
  on projects without `src/ai_hats/`. Override:
  `AI_HATS_NO_RAW_DESTRUCTIVE_SKIP=1 git commit ...` (HATS-470).

### Changed

- **Pre-push e2e gate decoupled from the push connection** (HATS-686)
  — the maintainer master-push gate ran the ~27-min `pytest -m "(integration
  or smoke) and not quarantine" tests/e2e/ tests/smoke/` suite *inside* the
  pre-push hook. That is incompatible with pushing to GitHub over SSH: git
  holds the connection open across the hook, and GitHub closes it after ~30s,
  so the hook died with exit 141 before the suite finished (twice in HATS-684);
  client-side `ServerAliveInterval` (15 and 60) did not help. The hook is now
  **dual-mode**: `scripts/run-e2e-gate.sh` (or `… --run`) runs the suite *out
  of band* and, on pass + a clean working tree, writes a pass-marker keyed to
  HEAD's commit SHA under `<git-common-dir>/ai-hats/e2e-gate/`; the pre-push
  hook itself just checks—instantly—that a green marker exists for the master
  `local_sha` being pushed, and blocks otherwise. The HATS-550 "no-broken-
  master, no-bypass" contract is preserved (a forged marker is the moral
  equivalent of `git push --no-verify`). New maintainer flow:
  `scripts/run-e2e-gate.sh` then `git push origin master`.
- **`backlog-manager` skill split into a lean index + `references/`** (HATS-578,
  child of HATS-499). The 512-line SKILL.md — the longest in the library, bundled
  by `trait-agent` (reach 14 roles) — now violates nothing: a 133-line
  orchestrator/index (overview, core task CLI, FSM diagram, state→skill routing)
  with per-domain detail moved **verbatim** one level deep into `references/`
  (`lifecycle.md`, `hypotheses.md`, `proposals.md`, `attachments.md`,
  `relationships.md`), per the HATS-557 `>150` split policy. No behavior change:
  content relocated, the `description` frontmatter untouched. Motivation is
  policy-compliance + maintainability, **not** context savings — session-data
  analysis showed the skill *body* loads ~once per 10 sessions (backlog discipline
  rides the always-on `trait-agent` injection + `rule_backlog_discipline`, not the
  body). The real always-on lever — `trait-agent`'s standing footprint — is spun
  off to HATS-662.
- **38 skill `## When to Use` sections rewritten to boundary/disambiguation**
  (HATS-573, child of HATS-499) — applies the HATS-572 convention (the section
  loads *after* skill selection, so it must add what the one-line `description`
  can't) across the full restatement-only backlog the HATS-572 soft audit
  surfaced. Each section now names a concrete sibling to prefer or a concrete
  excluded case instead of re-listing the description's triggers — e.g.
  `audit-reviewer`↔`domain-reviewer`↔`review-role`,
  `incident-response`↔`systematic-debugging`, `backup-recovery` (data)↔
  `rollback-plan` (change), `ansible-ops` (config)↔`terraform-expert`
  (provisioning), `observability-setup`↔`reliability-checklist`. `gworkspace-cli`
  gained a section it previously lacked. Bodies-only — `description` frontmatter
  (owned by HATS-571) and `golang-*` left untouched; composition errors=0.
  Confirming evidence for HYP-038.
- **Skill-authoring discipline hardened from obra/superpowers (MIT)** (HATS-659,
  child of HATS-499). Three mechanics harvested into existing components — no new
  skill. `skill-template` + `skill-engineer` review checklist gain a **CSO
  anti-summary** rule (a `description` carries triggers + one capability phrase
  and never summarizes the procedure body — a body-summary is a shortcut the
  selector acts on *instead of* loading the skill) and a **validation-scenario**
  done-criterion (a skill is not done without one named RED baseline an agent
  fails without it; prose-level RED→GREEN→REFACTOR, no eval harness).
  `scope-guard` + `devils-advocate` gain a structural **rationalization red-flag
  table** (catch the talked-into-it thought before the action). Two-stage
  spec→quality review evaluated and skipped (no gain over `audit-reviewer`).
- **The init wizard now lists the live role catalog** instead of a
  hand-maintained list that drifted (HATS-625). The `initial-wizard`
  injection carries a new `<available_roles>` placeholder, expanded at
  prompt-build time (`build_session_prompt`, next to the HATS-380
  `<ai_hats_dir>` expansion) with the user-facing roles the resolver
  actually sees — so new roles (e.g. `dev-web`, `role-curator`) and
  project-local roles appear automatically, while engine-internal
  (`core`-layer) roles are filtered out. Layer is derived from the
  resolved role path; the per-role summary from its injection H1. The
  shared renderer is `ai_hats.role_catalog.render_role_catalog`.
- **Pipeline subsystem: `launch_provider` megastep split** (HATS-535).
  The single `launch_provider` step that pre-HATS-535 owned spawn + audit
  derivation + SESSION_END hooks + auto-retro was structurally honest in
  YAML only down to "one step does everything" — masking the asymmetry
  where SubAgent's `audit.md` was meta-only despite claude SDK persisting
  the same JSONL HITL used. Refactor:
  - Step renamed `launch_provider` → `provider` (id and class).
    `LaunchProvider` is retained as a deprecated class alias so external
    YAMLs referencing `id: launch_provider` keep loading.
  - New `make_audit` step (`src/ai_hats/pipeline/steps/make_audit.py`) —
    sole `AuditWriter` invocation surface; reads claude JSONL via
    `_claude_jsonl_path` + `_discover_claude_jsonl` mtime fallback.
  - New `run_session_end` step (`src/ai_hats/pipeline/steps/run_session_end.py`)
    — retro decision + `write_retro_log` + `_spawn_session_reviewer_background`
    - SESSION_END hooks + cyan retro reminder banner.
  - Two new sub-pipelines `library/core/pipelines/finalize-hitl.yaml`
    (`make_audit + run_session_end`) and `finalize-subagent.yaml`
    (`make_audit` only). Invoked by `WrapRunner.run` /
    `_finalize_sub_agent` from their `finally` blocks via
    `pipeline.run(..., initial={...})` — `claude_session_id` and
    `hooks_env` flow as initial state, NOT through the main pipeline
    funnel.
  - `runtime._finalize_session` shrunk to `_finalize_session_basic`
    (per-runner cleanup only: metrics.json + trace stats + smoke).
    `_print_session_end` stays in `WrapRunner.run`'s outer `finally`
    so the session-id is SIGINT-safe (HATS-086 preserved); the inline
    retro reminder banner moved to `RunSessionEnd._print_retro_banner`.
  - **SubAgent gains structured `audit.md`** — single-turn `_run_attempt`
    callers and multi-turn `_finalize_session_audit` thread `work_dir`
    through to `_finalize_sub_agent`, which invokes `finalize-subagent`
    when both `work_dir` and `claude_session_id` are known. Pre-HATS-535
    SubAgent `audit.md` was meta-only; post-HATS-535 it carries
    `👤`/`👾`/🔧/💭 markers like HITL. Mirror of HATS-523 (which brought
    `meta_prompt.txt` to HITL parity with SubAgent).
  - YAML pipeline `human` and `execute` updated to `id: provider`.
    Compatibility: `id: launch_provider` still resolves (via alias),
    but `step.io.name` returns `"provider"` regardless of which id was
    used in the YAML.
- **Unified `Assembler._refresh()` entry-point** (HATS-469). The
  historical `init` / `set_role` / `bump` triple-dispatch is gone:
  a single `_refresh(*, install_time, result)` method now drives
  registry replay (`install_time=True` only — init and `do_bump`),
  scaffold + canonical aggregator heal, provider runtime hooks
  (`.claude/settings.json` + `_materialize_pretooluse_hooks`,
  always-fire so first-session bootstrap on Claude works), and
  role-specific git hooks. State-condition diagnostics
  (orphan-skill warning, empty `.agent/` note) split into
  `_run_diagnostics()` which fires ONLY on user-initiated paths
  (`do_bump`, init re-init, `self update`) — runtime `set_role`
  stays silent (no per-session orphan-warning spam). Internal
  refactor: `Assembler.bump()` was removed; the `do_bump` CLI
  composes `_run_v07_migration` + `compose_for_role` + `_refresh`
  - `_run_diagnostics` inline. `cli/assembly.py`'s post-init
    auto-bump block was removed (init itself is the refresh path).
    Behaviour change on re-init: existing projects with
    `migration_step=0` (pre-HATS-471 shape) now replay the registry
    on `ai-hats self init` — same effect as the old `self bump`
    auto-trigger but via init directly.
- `self update`'s auto-bump now runs via `python -m
  ai_hats._bump_internal` (new hidden module entry-point) instead of
  `ai-hats self bump`. Behaviour is identical — same flags
  (`--migrate-force` / `--check-branches`), same fresh-interpreter
  semantics required by HATS-400 — but the entry-point is private and
  not surfaced in `--help` / `--tree` (HATS-470).

### Removed

- **The `.claude/plans → plan-sync` plan detour is gone** (HATS-637). A plan is
  always a task and always lives at the one canonical path
  `<ai_hats_dir>/tracker/backlog/tasks/<ID>/plan.md`. `transition <ID> plan` no
  longer imports `.claude/plans/<NN>-*.md` (a stray file there is now inert),
  and the `ai-hats task plan-sync` command is removed together with its engine
  internals (`_sync_plan_from_claude_plans`, `find_claude_plan_for_task`,
  `PlanSyncAmbiguousError`). Write plan content straight into the scaffold; the
  per-section gate (HATS-635) still blocks `transition execute` on an empty
  plan. Migration: replace `task plan-sync <id> --from-file <f>` with a direct
  Write/Edit of the tracker `plan.md` — see
  [`docs/migration-v0.8.0.md`](docs/migration-v0.8.0.md).
- **`ai-hats self bump` CLI command.** Bump functionality is preserved
  and now runs only via the auto-bump path inside `ai-hats self
  update` (fresh subprocess, HATS-400) and inline from `ai-hats self
  init`. Direct user invocation of bump is rare in practice and the
  new internal entry-point makes the "framework-internal" status
  honest. The hidden `python -m ai_hats._bump_internal` remains for
  the subprocess case (HATS-470). Migration: use `ai-hats self update`
  (or `self init`) — see
  [`docs/migration-v0.8.0.md`](docs/migration-v0.8.0.md).

### Fixed

- **e2e install tests are now hermetic against an inherited `PYTHONPATH`** (HATS-685)
  — `tests/e2e/` builds subprocess envs with `os.environ.copy()`. `src/ai_hats/`
  has no `library/` subdir (it maps to the `ai_hats.library` package only at
  build time), so an inherited `PYTHONPATH=<repo>/src` — the worktree test
  workaround, and exactly what `ai-hats wt exec` sets — leaked into the
  launcher's `self init` subprocess, redirecting its `ai_hats` import to the
  source tree where `files("ai_hats.library")` raises `ModuleNotFoundError` →
  built-in roles vanished → "Role 'assistant' not found". `test_e2e_fresh_init_heals`
  was false-RED from any worktree (the standard agent execute context) and ~15
  other real-install e2e tests were latently exposed. An autouse fixture in
  `tests/e2e/conftest.py` now strips a shared denylist (`PYTHONPATH` +
  `PYTHONHOME`/`PYTHONSTARTUP`/`VIRTUAL_ENV`/`AI_HATS_DIR`/`AI_HATS_USER_HOME`)
  from `os.environ` for every e2e test, so subprocess envs exercise the real
  installed package. Deliberate `PYTHONPATH=src` tests re-set it explicitly and
  are unaffected.
- **SDK sub-agent transcript folded into audit when JSONL is absent** (HATS-682)
  — `AuditWriter.build()` parses structured turns from claude's JSONL (or the
  trace-log fallback). SDK sub-agents run with `isolation=discard` leave a
  non-empty `transcript.txt` (the LLM's final stdout) but no reachable JSONL
  (tmp-worktree project_key mismatch) and no `trace.log`, so `build()` parsed
  zero turns and emitted a `turns:0` stub — the real work (e.g. a full
  hypothesis-intake draft) was silently dropped from `audit.md`. Re-measured
  over 158 live sessions: ~15 of the 31 tiny (<2 KB) audits were this case, not
  empty sessions. `build()` now folds `transcript.txt` into the audit body
  (`## Transcript` section) **only when no structured turns were parsed** — so it
  never duplicates content already rendered as turns. `metrics.json` counters
  stay honest (no synthesized turns); `reasoning.log` is excluded (noisy/large);
  oversize is still bounded downstream by `SessionReviewRunner._truncate_audit`
  (HATS-684). Verified on `session_20260531-193008-1`: 561 B stub → 2.6 KB with
  the recovered draft.
- **Content-aware reviewer audit delivery** (HATS-684, supersedes the HATS-424
  squeeze) — `SessionReviewRunner._truncate_audit` was a blunt 8 KB head+tail
  middle-drop that cut the real evidence (🔧 tool-calls / 👾 responses) reviewers
  cite, while keeping the redundant first-turn ingested-evidence echo. Generation
  is now lossless (HATS-681/666/683), so size is managed at delivery: the
  first-turn 👤 ingested block (`# PROJECT_STATE` backlog dump / `# Reflect-all`
  handoff — 64% of corpus bytes, redundant since the reviewer already has the
  target's real content) is head-keep-bounded to 2 KB, and **all signal is kept
  verbatim** — no tight budget (capping signal was itself the cause of "cannot
  cite evidence" → `n/a` verdicts). A 250 KB safety-valve (head/tail trim,
  HATS-424 tail invariant preserved) is the only hard ceiling and never fires on
  the live corpus. Re-measured over 155 audits: median 40.6 KB → 11.5 KB;
  session-reviewer −85%, judge −77%; interactive (maintainer/role-curator) signal
  preserved in full (−0%, zero signal-loss).
- **Judge reports no longer persist the literal `payload` stub** (HATS-671) —
  reports under `sessions/retros/judge/` were occasionally written as a 7-byte
  `payload` literal instead of the report body. Root cause was test pollution,
  not the production `reflect all` pipeline: `test_save_artifact_expands_ai_hats_dir_placeholder`
  escaped its `tmp_path` and wrote into the real (gitignored) `sessions/` dir via
  two vectors — an ambient `AI_HATS_DIR` (env precedence over `project_dir`) and
  a CWD-relative `<ai_hats_dir>` expansion. Fixed by an autouse
  `_isolate_ai_hats_dir` fixture that clears ambient `AI_HATS_DIR` for every
  test, and by anchoring a relative `<ai_hats_dir>` expansion to `project_dir`
  in `SaveArtifact` so the write is CWD-independent (out-of-tree absolute
  `AI_HATS_DIR` is untouched; production was already correct).
- **`self update` rebuilds a python-broken versioned venv instead of skipping it**
  (HATS-657). After a host python upgrade a `versions/<sha>/` venv is left
  *complete* (`.complete` sentinel + `bin/ai-hats`) yet *unrunnable* — its
  `bin/python` symlink dangles. The launcher already falls back to `.venv` in that
  case (HATS-656), but `read_current_sha` and the `self update` reuse gate still
  treated the broken venv as usable: the update saw `already_current` and skipped
  the rebuild (the versioned install stayed broken), and the HATS-655 dormancy
  advisory false-fired ("your launcher predates the versioned layout" — wrong; the
  launcher is current and correctly skipping a broken venv). A single shared
  `is_usable_version` predicate (sentinel **and** `bin/ai-hats` **and**
  `bin/python` on disk) — mirroring the launcher's `-x bin/ai-hats && -x bin/python`
  — now gates both `read_current_sha` and the reuse path, so a python-broken
  versioned install resolves to "not current", routes to `.venv`, and the managed
  `self update` rebuilds it (rmtree+reinstall) with the advisory silent. Covered by
  an extended real-pip e2e (`test_e2e_install_init_break_heal`).
- **git-hook dispatcher forwards STDIN to every `.d/` hook** (HATS-654). The
  `<event>` dispatcher ran each `<event>.d/*` script in a shared-stdin loop, so
  the first stdin-consuming hook drained the git protocol and every later hook
  read EOF. For `pre-push` this silently no-opped the e2e+smoke master gate
  (HATS-550): its `empty stdin → exit 0` fast-path fired because the
  lexicographically-earlier shared-state hook had already consumed the ref list,
  leaving master pushes ungated. The dispatcher now captures STDIN once for
  events with a documented stdin protocol (`pre-push`/`pre-receive`/
  `post-receive`/`post-rewrite`/`proc-receive`/`reference-transaction`) and
  replays a fresh copy into each hook. Scoped by event name (not a runtime
  `[[ -t 0 ]]` probe) so stdin-less events (`pre-commit`/`post-*`) never `cat`
  and cannot hang on a tty/open pipe. Covered by a real-push e2e gate
  (`tests/e2e/test_prepush_dispatcher_stdin_fanout.py`).
- **SKILL.md lint findings closed (agnix Phase 1)** (HATS-626 / HYP-059).
  Three error classes the HATS-617 agnix PoC surfaced are now green:
  (a) 5 protocol skills shipped with **no YAML frontmatter** — added
  `name`+`description` to `judge-auditor-protocol`, `judge-protocol`,
  `judge-role-protocol`, `review-role`, `maintainer-quality-gate` (the 3
  with a `metadata.yaml` reuse its description verbatim). This also
  un-degrades their Claude Code skill-catalog entries, which
  `providers._extract_frontmatter_description` had been falling back to the
  bare skill name for. (b) **34 broken `assets/*` links** across 5 golang
  skills — re-vendored the 32 referenced asset files from the upstream
  commit each skill's `metadata.yaml` already records (`b29499a`,
  `samber/cc-skills-golang`), so the assets match the SKILL.md bodies.
  (c) **`metadata.openclaw` nested-map** parse error in all 33 golang
  frontmatters — stripped (kept `metadata.{author,version}`,
  `user-invocable`, `license`, `compatibility`, `allowed-tools`). Removing
  the openclaw parse error also unmasked a latent name/dir mismatch in
  `golang-linter` (frontmatter `name: golang-lint` vs dir `golang-linter`),
  now fixed. agnix reports 0 errors across all 89 library skills.
- **Managed PreToolUse hook command resolves from any cwd**
  (HATS-615). `ClaudeProvider._desired_runtime_entries` wired the
  HATS-437 shared-state guard (and skill-declared runtime hooks) into
  `.claude/settings.json` with a **bare relative** command path
  (`.agent/ai-hats/library/hooks/pre_bash_shared_state_guard.sh`).
  Claude Code resolves a relative hook `command` against the agent's
  **cwd**, not the project root, so a session / sub-agent starting in a
  subdirectory invoked a path that did not exist — `/bin/sh` exited 127
  and the shared-state safety net was silently disabled (reproduced:
  cwd=root → exit 0; cwd=subdir → exit 127). The emitted command is now
  prefixed with `$CLAUDE_PROJECT_DIR/` (the existing
  `paths.CLAUDE_PROJECT_DIR_VAR` single-source-of-truth, which Claude
  Code expands at hook-execution time), so it resolves regardless of
  cwd. The migration healer / asserter already strip the prefix, and
  `_upsert_managed_entry` overwrites the stale bare entry in place, so
  existing projects self-heal on `ai-hats self update`.
- **Session-reviewer no longer crashes on dict-shaped observations**
  (HATS-610). The reviewer LLM occasionally emits an `observations`
  bullet as a single-key mapping (`{'<title>': '<detail>'}`) instead of
  a string. `SessionReviewV1.observations` is `list[str]` (`extra=
  "forbid"`), so such an entry passed the lenient IS-A-LIST shape check
  in `_validate_analysis_shape`, returned from `_run_and_validate`, then
  crashed terminally in `_merge` — *outside* the retry loop, so retries
  could never recover it (`SessionReviewError`, reviewer exit≠0;
  model-agnostic, the root cause of the flaky e2e
  `test_role_session_retro_vertical`). `observations` are non-critical
  narrative, so `_run_and_validate` now coerces non-string entries
  (`{title: detail}` → `'title: detail'`) at a single point before the
  strict merge, rather than crash; HYP/PROP refs stay strict. The e2e
  test's deterministic PROP-wiring is asserted via `_render_open_proposals`
  injection (over the prior soft "reviewer acted on the seed" assertion).
  A separate residual reviewer-retry-stacking timeout flake is tracked by
  HATS-614 / HYP-055.
- **`ai-hats self init` provider menu marks every detected provider**
  (HATS-613). The wizard menu marked only the dict-first provider whose
  `~/.<name>` config dir existed (gemini) as `(recommended)` and
  pre-selected it. A user with both `~/.claude` and `~/.gemini` saw gemini
  recommended and claude unmarked — as if claude were undetected — and
  pressing Enter silently picked gemini. `_detect_provider_default()` (one
  string) is replaced by `_detected_providers()` (the full list); the menu
  now marks **every** detected provider `(detected — found ~/.<name>)`, and
  pre-selects a click default ONLY when exactly one provider is detected —
  zero or several is ambiguous, so the user picks explicitly instead of
  inheriting the dict-first bias.
- **`ai-hats self init` works as the first command in a fresh project**
  (HATS-612). The host launcher only healed the per-project venv for
  `self update`, so a fresh-project `ai-hats self init` was rejected with
  `Run: ai-hats self update` — the user trying to *init* was told to
  *update*. `heal_if_needed` now also fires on `self init`, so a single
  command creates the default venv and configures the project. The
  venv-missing hint is context-aware: a fresh project (no `ai-hats.yaml`)
  is pointed at `self init`; an already-initialized project whose venv
  broke (e.g. a host python upgrade) keeps `self update` for heal-recovery.
  `install-launcher.sh`'s post-install "Next" hint leads with `self init`.
- **Typo'd lifecycle hook event keys are now rejected at YAML load**
  (HATS-515). `composition.hooks` previously inherited `_YamlModel`'s
  `extra="ignore"`, so a misspelled event (e.g. `sesion_start:`) was
  silently dropped and the hook never ran. `HooksConfig` now carries a
  `model_validator(mode="before")` that fails fast with
  `unknown hook event(s): <name>; allowed: …`. `_merge_hooks` derives its
  event list from the `LifecycleEvent` enum instead of a hardcoded
  6-string tuple, removing the parallel-list drift vector. Direct kwarg
  construction is unaffected — the validator only triggers on dict (YAML)
  input. Silent-key sibling of HATS-452's silent-None; ADR-0005 gains the
  invariant for future model authors.
- **master CI was red on four independent counts** (HATS-600). None were a
  regression from a single change — source had moved on without its tests,
  plus two latent gate failures:
  - **6 stale tests** realigned with intentional, reviewed source changes:
    HATS-510 dropped `integration::google` from the `assistant` role and
    moved `rule_core_vs_usage_split` ownership to the `library-curator`
    trait; HATS-501/505/507 added a `RoleNotFoundError` pre-check in
    `ComposeRole.run`; HATS-549 Phase 4 routes non-owned hooks
    (`pre-commit.sh`) to `user-hooks/` instead of `library/hooks/`.
  - **py3.11 collection `SyntaxError`** — two `tests/e2e/` files reused
    double quotes inside double-quoted f-strings (PEP-701, 3.12+ only);
    switched the inner `env[...]` subscript to single quotes.
  - **`lint (ruff)` job** — 20 pre-existing violations (F401/E401/F541)
    cleared via `ruff check --fix` (behaviour-preserving).
  - **coverage gate (76% < 78%)** — the unit suite (`-m 'not integration'`)
    barely touches `worktree.py`/`state.py`, which are exercised by
    integration tests CI deselected. A new `coverage` job runs unit +
    non-e2e real-git integration tests and owns the gate (`worktree.py`
    26%→90%, total ~83%); the `test` matrix now validates the unit suite
    across versions without a gate. The e2e tier (per-session venv builds)
    stays out of CI's hot path.
- **Done-guard / `wt merge` refused an already-merged task when the main
  checkout HEAD had wandered** (HATS-596). `Worktree.merge()` decided
  "is this merged?" from the main checkout's current HEAD branch
  (HATS-533 guard), so finalizing a task whose branch was already merged
  into its base — while the main checkout sat on a concurrent feature
  branch — emitted a false "base branch mismatch" and refused, even
  though the work was fully in `master`. Now merge-verification is
  **checkout-independent**: when the task branch tip is already an
  ancestor of the recorded base ref (`git merge-base --is-ancestor`,
  local base only), no `git merge` runs — the worktree is torn down
  cleanly regardless of where the main checkout points. `_check_clean`
  is still honored (force-bypassable). Two adjacent fixes:
  - `transition done` now plumbs `--force` into the merge so a corrective
    override reaches the merge guards (previously `--force` only relaxed
    the FSM state guard, never the git-integration check). `--force` does
    **not** relax the HEAD-mismatch guard — that stays a correctness gate
    against wrong-branch merges.
  - The HEAD-mismatch guard is gated on base-branch existence, reconciling
    HATS-533 with HATS-253: a deleted base now falls through to the
    `OriginalBranchMissing` path (which preserves the worktree branch for
    manual rebase) instead of a misleading mismatch refusal.
- **`self update` forward-compat deadlock on a newer-written
  `ai-hats.yaml`** (HATS-581). An older installed binary hard-crashed on
  a config field a newer binary had written (e.g. `migration_step`, added
  without a `schema_version` bump): `ProjectConfig`'s `extra="forbid"`
  raised at the pre-install config read, blocking the very `self update`
  that would have delivered code able to parse it. Two layers:
  - `ProjectConfig.from_yaml` now strips unknown top-level keys with a
    stderr WARN (`dropping unknown field '<key>'`) instead of raising —
    forward-compat, mirroring the existing deprecated-field strip.
    `extra="forbid"` stays as a backstop for nested models.
  - `ai-hats self update` tolerates an unparseable config: it degrades
    (prints a graceful "not parseable by the installed version" message,
    skips the composition snapshot) and forces the fresh-interpreter bump
    so the newly installed code heals the config — rather than aborting
    with a traceback before the package install.
- **Migration healer auto-rewrites to missing destinations** (HATS-549
  Phase 2). `migration_healer` Stage A1 / A2 substitutions previously
  rewrote legacy `.agent/<stem>/` refs in user files to the new layout
  without checking the new path existed on disk — masking historical
  data-loss as "successful self-heal". Per-file gate now refuses to
  heal a file if ANY ref inside it has BOTH legacy source and new
  destination missing; the ref lands in Stage B inventory with
  `reason="dst-missing"` and a `tar -xzf` recovery hint pointing at
  the Phase 1 backup. Empty legacy + empty destination is the data-loss
  signal; leaving the legacy path in place preserves the user's
  visibility into the failure.
- **`ai-hats self update` end-of-bump smoke-assert** (HATS-549
  Phase 3, `src/ai_hats/migration_assert.py`). Walks
  `.claude/settings.json{,.local}` PreToolUse / PostToolUse /
  SessionStart / SessionEnd / UserPromptSubmit / Stop / SubagentStop /
  Notification / PreCompact hook commands; for each path-like
  `command` value (expands `$CLAUDE_PROJECT_DIR/`) verifies on-disk
  existence. On any broken ref → `AssemblyError` with per-entry
  diagnosis + recovery one-liner pointing at the Phase 1 backup.
  Wired at three CLI sites: `cli/assembly.py::do_bump` (direct +
  `_bump_internal` subprocess path), `cli/assembly.py::self_init`
  (re-init only), `cli/maintenance.py` self-update in-process branch.
  The in-process branch additionally surfaces bump failure as a
  non-zero exit via a new flag — pre-fix this path swallowed
  `AssemblyError` and reported failure on stdout but exited 0,
  silently violating the safety contract.
- **`ai-hats wt merge` / `ai-hats task transition <ID> done` refuse
  when main-repo HEAD has wandered off the worktree's merge target**
  (HATS-533). `WorktreeManager._fast_forward_merge` and `_squash_merge`
  ran `git merge` in the main-repo cwd without first verifying main-repo
  HEAD was still on `self._original_branch` (the branch captured at
  `wt create` time). If HEAD moved between create and merge — manual
  `git checkout`, an IDE branch-switch, a peer agent operating directly
  in the main repo without a linked worktree — the merge silently landed
  on whatever branch was currently checked out. Same silent-wrong-branch
  class as HATS-486; bug existed since the original `feat(runtime): add
  git worktree isolation` (2026-03-27, present in v0.3.0 through v0.7.0).
  Live trigger: HATS-509's own session — worktree from master, peer
  agent committed directly on `task/hats-514` in main repo, `transition
  done` merged into the wrong branch; recovered via `git cherry-pick`.
  New `WorktreeBaseBranchMismatchError` raised BEFORE any mutation; CLI
  handlers on both surfaces emit a copy-pasteable recipe (`cd <main-repo>;
  git checkout <expected>; ai-hats wt merge` for direct callers; same
  shape with `task transition <ID> done` for the transition surface).
  Symmetric with HATS-518 (create-time twin). `--force` / `--accept-drift`
  do NOT bypass — those override different safety contracts.
- **`ai-hats task transition <ID> done` no longer leaks a misleading
  `--accept-drift` hint** (HATS-509). When the internal `wt merge`
  failed on drift, the `WorktreeDriftError` body ended with "re-run
  with `--accept-drift`" — but that flag exists on `wt merge`, not on
  `task transition`. Users copy-pasted the suggestion and hit
  `No such option`. The recipe now lives in CLI handlers, not in the
  exception body: `task transition done` catches the error and emits a
  copy-pasteable two-step path (`cd <main-repo>; ai-hats wt merge
  --accept-drift; ai-hats task transition <ID> done`) with an explicit
  note that the flag belongs to `wt merge`. Direct `ai-hats wt merge`
  callers keep equivalent UX — their CLI handler appends the recipe
  with the full command form. Origin: HATS-505 retrospective.
- **`ai-hats wt create` / `ai-hats task transition <ID> execute` now
  refuse when main-repo HEAD is not on a canonical base branch
  (`master` / `main`)** (HATS-518). Previously `WorktreeManager.create()`
  silently captured whatever branch was current as the worktree's merge
  target — if the operator parked the main repo on a feature branch
  (e.g. `task/hats-510`) before invoking `transition execute`, subsequent
  `ai-hats wt merge --accept-drift` happily merged INTO that feature
  branch instead of master. The CLI reported "merged" while master
  stayed untouched (live incident: HATS-486 session, recovered manually
  via `git checkout master && git merge --ff-only task/hats-510`). New
  guard `assert_head_is_canonical_base()` fires at both call sites
  before any `git worktree add` runs. Recovery: `git checkout <base>`
  in the main repo, then re-run. No-op on detached HEAD, non-git dirs,
  and exotic repos that have neither `master` nor `main`.
- **`task transition <ID> execute` no longer fails when the target
  branch already exists** (HATS-517). Three-way classifier inside
  `WorktreeManager.create()` under the HATS-479 create-lock:
  (A) branch exists, no worktree owns it → attach to a fresh linked
  worktree via positional `git worktree add <path> <branch>`, normal
  lifecycle proceeds; (B) branch is checked out in the MAIN worktree
  (`project_dir`) → refuse with an actionable hint pointing at
  `git switch` or `ai-hats task close` (CLI exit 2). At the CLI
  boundary, the HATS-518 canonical-base guard fires earlier and
  reports its own `WorktreeBaseBranchError` (exit 1) — the
  classifier's Case B stays as defense-in-depth for direct Python-API
  callers (`WorktreeManager().create()` in tests / external scripts);
  (C) branch is already a linked worktree but its ai-hats state JSON
  was lost (manual delete, backup restore) → adopt the existing path
  and re-persist state. Pre-fix workaround was `task close` which
  skipped the `document → review` walk. `--force --reason` only
  bypassed the FSM guard, not the worktree side-effect.
- **PreToolUse hook safety net restored** (HATS-437 + HATS-467). Post
  HATS-294 `.claude/settings.json`'s PreToolUse entry pointed at
  `<ai_hats_dir>/library/hooks/pre_bash_shared_state_guard.sh` but
  the file was never materialized → every Bash invocation died with
  "No such file or directory" and the shared-state guard was silently
  a no-op in every session. New `Assembler._materialize_pretooluse_hooks()`
  copies `*.sh` from package data (`ai_hats.library/hooks/`) to
  `<ai_hats_dir>/library/hooks/` with mode 0o755 via
  `safe_delete.replace()`. Wired into `init` / `set_role` / `bump`
  alongside the existing settings.json wiring. Idempotent
  (bytes-compare), stale files swept via `safe_delete.discard()`,
  manifest at `<target>/.manifest` tracks managed names.
- `self update` / `self init` bump path warns when an orphan
  `.ai-hats-managed` marker is detected under `~/.claude/skills/`
  (typically left by a manual `cp -r .claude/skills/
  ~/.claude/skills/` performed pre-v0.7). ai-hats has never written to
  that location — user-level Claude skills are not managed and the dir
  drifts forever without a refresh path. The WARN prints a safe-remove
  hint (`rm -rf ~/.claude/skills/`) and re-fires until the user clears
  it; ai-hats does not delete the dir itself (HATS-465).
- Closes long-standing data-loss windows where `ai-hats self update` /
  `self init` would `shutil.rmtree` or blind-`write_text` over
  user-owned files (`.claude/role.md`, `.claude/settings.json`,
  `.gitignore` writes, CLAUDE.md no-markers branch,
  `migration_healer.heal_text_file` on non-git projects). All such
  operations now snapshot to the trash bin first (HATS-470).

## [0.7.0] - 2026-05-23

Composition-and-customization release. **MAJOR** bump driven by three shifts:

1. **v0.6 → v0.7 layout migration** is now folded into `self update` /
   `self bump`; the standalone `self migrate-v07` verb is retired
   (`Migration:` under *Removed*).
2. **User-level overlays** at `~/.ai-hats/customizations.yaml` ship as a
   first-class layer; `personal-workflow` migrates there
   (`Migration:` in the ✨ BREAKING section).
3. **Role architecture splits** — `assistant` = opinionated default
   (Google Workspace + personal-workflow bundled); `dev-python` = clean
   Python baseline; `maintainer` = new role for ai-hats-codebase work.

Also: composition is now an immutable contract (ADR-0005, HATS-452),
two-level defence against autonomous shared-state writes (HATS-437),
banner reads real git state (HATS-432) + fires on non-editable installs
(HATS-458), `self update` refuses silent downgrades (HATS-441) and
short-circuits pip on a no-op, `wt merge` has a pre-merge drift guard
(HATS-457).

### 🎭 v0.7 role architecture — `maintainer` + `dev-python` extraction (HATS-381 + HATS-392)

**`maintainer` extracted (HATS-381).** Codebase work on ai-hats itself
moves out of `assistant` into a dedicated role. New shipped content:

- `core/skills/design-minimalism` — every primitive at plan stage needs
  a concrete use case; speculative additions → Out of scope.
- `core/skills/predictive-accounting` — for shrink/refactor tasks,
  present baseline + delta *before* implementation.
- `usage/skills/doc-protocol` — plan-stage style forks + scope triage
  - pre-commit artifact verification (folds three prior memory-only
    patterns).
- `core/rules/rule_core_vs_usage_split` — universal-vs-project-specific
  decision tree for library content (sourced from PROP-037).
- `core/traits/ai-hats-framework` — wraps the rule + layered-library
  injection.

The `ai-hats-maintainer` trait injection grew from ~10 to ~90 lines:
Conventional Commits, what-NOT-to-commit, canonical CLI, glossary-first,
numbered-refs, d2 practical gotchas, release flow, 8 architectural
defaults, 3 anti-patterns. Replaces the last per-project memory
references.

**`dev-python` extracted (HATS-392).** `assistant` (8 traits) is
reframed as *opinionated all-in-one* — bundled Google Workspace +
personal-workflow; not a clean baseline. New **`dev-python`** (6 traits)
is the clean Python + Shell starter. Wizard Step 3 maps `pyproject.toml`
/ `setup.py` → `dev-python`; empty / non-Python projects still →
`assistant`.

### ✨ Bring your own traits/skills — user-level overlays (HATS-421 + HATS-433, **BREAKING**)

**The mechanism (HATS-421).** A second customization layer lives at
`~/.ai-hats/customizations.yaml` — same schema as project-level,
applied to every project. No more repeating `ai-hats config customize`
across N projects; personal content no longer leaks into the package.

```bash
mkdir -p ~/.ai-hats/traits/<your-trait>
$EDITOR ~/.ai-hats/traits/<your-trait>/config.yaml
ai-hats config customize <role> --add-trait <your-trait> --global
ai-hats config status   # full tree with (built-in) / (global) / (project) source-tags
```

Compose order: built-in → global → project (project wins on conflict).
`config status` annotates every component with a source-tag.

**Migration: HATS-433, BREAKING.** `personal-workflow` trait —
TEMPORARY in v0.6 — leaves the package and moves to user-scope. Affects
`maintainer` (10 → 9 traits) and `assistant` (8 → 7 traits). Trait body
unchanged.

```bash
mkdir -p ~/.ai-hats/traits/personal-workflow
# Recover content from the previous tag, then:
ai-hats config customize maintainer --add-trait personal-workflow --global
ai-hats config customize assistant  --add-trait personal-workflow --global
# In each project:
ai-hats self bump
```

Worked example: `docs/how-to-extend.md` §"Migrating from a removed
built-in component".

### Added

- **HATS-445** — `ai-hats execute --prompt <name>` resolves
  `initial_injections/<name>.md` through the full `library_paths` chain.
  Unlocks **shell-alias custom verbs**: plugin authors ship a role +
  injection and wrap `ai-hats execute` in a shell function — custom verb
  with zero ai-hats core changes. New section in
  `docs/how-to-extend.md`: "Custom verbs via shell aliases".
- **HATS-444** — `docs/INDEX.md` is the single source of truth for the
  wizard's companion-docs catalog. Mechanical enforcement via new git
  pre-commit hook (`pre-commit-docs-index.sh`) blocks commits that
  stage structural docs/ changes without staging `INDEX.md`. Override:
  `AI_HATS_DOCS_INDEX_ACK=1`.
- **HATS-437** — Two-level defence against autonomous shared-state
  writes (HYP-026 + HYP-027). Always-on rule
  `rule_pause_before_shared_state_write` forbids `gh pr
  create/close/merge`, `gh issue comment`, `gh release create`,
  `git push`, `TaskCreate` without per-command pause + user confirmation,
  and bans chaining them in one Bash invocation. Two hook scripts back
  the rule with deterministic blocks on the **irreversible** subset
  (`gh pr merge`, `git push --force`). Per-command ack via
  `AI_HATS_SHARED_STATE_ACK=1`. Gemini sessions get the rule +
  pre-push hook only (no PreToolUse equivalent in Gemini CLI).
- **HATS-442** — Session audit records the **effective role composition
  snapshot** (traits + rules + skills with source-tags) at session
  start. `session-reviewer` cites source-tags when filing proposals
  (framework vs user vs project). Closes the observability gap created
  by HATS-421.
- **HATS-408** — `ai-hats self migrate-v07` one-shot safe migration from
  v0.6 to v0.7. Inspects on-disk artefacts, diffs each vs composition
  baseline, refuses on user edits (`--force` bypasses). Atomic single
  git commit; idempotent. *(Superseded by HATS-415 — see Removed.)*
- **HATS-401** — Session-end **Update banner** in `execute` / `human`
  pipelines. When installed SHA lags upstream, surfaces short SHAs +
  `ai-hats self update` hint under `✨ Session summary`. Non-blocking
  detached probe writes to `<ai_hats_dir>/.cache/update-check.json`
  (24h TTL). Opt-out: `AI_HATS_NO_UPDATE_CHECK=1`.

### Changed

- **HATS-415** — `ai-hats self update` and `self bump` self-heal
  v0.6 → v0.7 layouts inline. Safe-to-delete v0.6 files (bytes match
  baseline) are swept transparently; user-edited files raise
  `AssemblyError` with per-file guidance. New flags: `--migrate-force`
  (bypass refusal) and `--check-branches` (warn on local branches
  modifying paths slated for deletion). No auto-commit — user owns the
  commit decision.
- **HATS-294** — Composition is now per-session in memory; canonical
  layer no longer materialises `priorities.md` / `role.md` /
  `traits/*.md` / `rules/*.md` / `skills_index.md`. `write_canonical`
  emits only the `imports.md` aggregator. Providers' `build_override`
  renamed to `build_session_prompt`.
- **Migration: HATS-407** — `ai-hats role set <name>` is yaml-only
  (writes `default_role:` to `ai-hats.yaml`). **Removed
  `ai-hats self rollback`** — yaml-only config means `git checkout
  ai-hats.yaml` is the recovery path. Users scripting `self rollback`
  should switch to `git checkout`.

### Removed

- **Migration: HATS-415** — `ai-hats self migrate-v07` CLI command
  removed. Its logic lives inline in `Assembler.bump()` and surfaces on
  `self update` / `self bump`. Flags re-homed: `--force` →
  `--migrate-force`, `--check-branches` kept. `--no-commit` has no
  analog. Migration: drop the `self migrate-v07` invocation, run
  `ai-hats self update` — sweep auto-applies on a v0.6-shape project.

### Fixed

- **`.gitignore` legacy block sweep** — `ai-hats self bump` / `self
  update` now removes the pre-HATS-317 `# AI-HATS:START..END` managed
  block from user `.gitignore` files. HATS-317 retired the dynamic
  generator in favour of a single static line at init, but never
  shipped the one-shot cleanup — every project initialized before
  HATS-317 carried 50–90 stale per-component entries
  (`.agent/ai-hats/rules/X.md`, `traits/Y.md`, etc.), many pointing at
  v0.7-vanished paths after HATS-294 stopped materialising the
  canonical layer. Doubly stale: redundant (the bare `.agent/`
  user-init line covers the subtree) AND broken (paths no longer
  exist). New `Assembler._strip_legacy_managed_block()` strips the
  block + one preceding blank-line separator, idempotent, respects
  `manage_gitignore = False`. Delivery pattern matches HATS-413:
  persisted on `self bump` only, no rewrite-on-read. Dogfooded on
  ai-hats's own `.gitignore` (121 → 48 lines).
- **`ai-hats self update`** — short-circuit `pip install` when installed
  SHA already matches remote `master`. Saved 10-15 s per no-op update
  (60s+ on slow links — users mistook for hang). Reuses the
  HATS-432/441 ahead/behind probe; bump still runs in-process so
  migrations apply. Bump path gained a Rich spinner so the
  `heal_external_refs` walk no longer looks like a hang.
- **HATS-457** — `ai-hats wt merge` drift guard (HYP-017). Between
  `wt create` and `wt merge` the base branch could advance — another
  agent's merge into local `master`, or `origin/<base>` pulled in
  commits — and the pre-merge `grep-verify` became silently stale.
  `WorktreeManager.create` snapshots base SHA; `wt merge` does a
  best-effort `git fetch` and refuses with `WorktreeDriftError` on
  divergence. New `--accept-drift` flag (separate from `--force` —
  two checks, two flags). Legacy state files gracefully skip.
- **HATS-452** — composition / pipeline value contract. Bare `ai-hats`
  was writing a `prompt.md` missing the merged role/trait injection —
  16k chars of behavioral guidance never reached the agent. Root cause:
  `compose_role` returned `{"system_prompt": ""}` for missing role;
  `WrapRunner.run_session` accepted the empty string and replaced the
  freshly-composed list with `[""]`. Four-layer fix per
  [ADR-0005](docs/adr/0005-composition-and-pipeline-value-contract.md):
  immutable `CompositionResult`, funnel drops `None` at merge boundary,
  `compose_role` emits `{}` for no-role, `WrapRunner.run` lost
  `system_prompt_override` (HITL has no override channel). New rule
  `rule_composition_value_contract` (always-on via `trait-agent`)
  documents the four invariants.
- **HATS-432** — Update-banner false-positive suppressed when installed
  HEAD is *ahead of* or *diverged from* cached upstream. New semantics:
  `has_update` is True only when installed is *strictly behind*
  (`behind > 0 and ahead == 0`). Probe runs `git fetch <url> master` +
  `rev-list --left-right --count`; banner prefers `git describe` labels
  (e.g. `v0.6.0 → v0.6.0-19-g…`).
- **HATS-432** — Update-banner hint corrected: `ai-hats self update`
  (actual CLI verb) instead of nonexistent top-level `ai-hats update`.
  Swept through the `ai-hats-maintainer` trait, README, and
  `docs/glossary.md`.
- **HATS-458** — Update banner fires for **non-editable installs**.
  HATS-441 lost ahead/behind detection for the majority install layout
  (`pip install`, not `-e`); axes stayed `None`, banner silent. New
  fallback: bare git mirror at `<ai_hats_dir>/.cache/probe-mirror/`,
  master fetched into it, `rev-list` resolves the baked installed SHA
  (`__commit_id__` from `_version.py`) against the freshly-fetched
  object graph. `run_check` tries the editable fast path first, falls
  back to the mirror.
- **HATS-441** — `self update` refuses silent downgrades when installed
  HEAD is ahead of remote master. Reuses the HATS-432 probe; new exit
  code `3` for refusal. `--force-downgrade` opts back into the
  destructive `pip install` for callers who know what they're doing.
- **HATS-416** — `migration_healer` skips `CHANGELOG.md`. The HATS-397
  auto-rewriter had been rewriting literal `.agent/hooks/` strings
  *inside* the HATS-412 entry that described the legacy-path bug —
  collapsed «canonical X instead of legacy X» prose to «canonical X
  instead of canonical X». CHANGELOG is historical record by
  convention. Whole-file skip, filename-specific.
- **HATS-413** — `self bump` persists yaml hardening so heals stick
  across CLI invocations. HATS-408 regression: `from_yaml` healed
  `default_role := active_role` in memory only, so every invocation
  re-logged `WARN: healed default_role` until the user explicitly ran
  `migrate-v07`. New `_normalize_yaml()` persists when deprecated
  fields remain in raw yaml. Idempotent. Read-only commands still
  don't persist (no-rewrite-on-read).
- **HATS-404** — `ai-hats hyp create` / `proposal create` surface
  duplicate-id collisions as a clean `Error:` line + exit 1 instead of
  a raw `FileExistsError` traceback.
- **HATS-403** — `ai-hats task create --id N` no longer silently
  overwrites an existing task. `TaskManager.create_task` raises
  `ValueError` before `mkdir` when the path exists. Closes a
  silent-data-loss seam.
- **HATS-424** — Session-reviewer audit truncation keeps both ends of
  the session, not just the head. Old `audit_text[:8000]` head-cut made
  end-of-session events (self-retrospective Skill calls, judge-report
  writes) invisible to the reviewer for audit > 8 KB. New
  `_truncate_audit` keeps 4 KB head + 4 KB tail with a marker.
- **HATS-418** — Session-retro pipeline dispatch restored. Since
  2026-05-13 every threshold-trigger session wrote the runtime decision
  line but no `session-reviewer spawn` followed — pipeline was
  0-output for ~30 sessions. Root cause: HATS-294 dropped the v0.6
  hook-copy side-effect; `HooksRunner._find_scripts` swept an empty
  dir. Fix: `WrapRunner._finalize_session` calls
  `auto_retro._spawn_session_reviewer_background` in-process, gated on
  `action == "run"` and `HATS_SKIP_RETRO != "1"`.
- **HATS-419** — `session-reviewer` retro pipeline no longer dies on
  markdown-fenced YAML. Model frequently wraps the body in
  `` ```yaml ... ``` `` inside the `BEGIN_REFLECT_SESSION_RETRO` markers;
  `_extract_yaml` passed the fence verbatim to `safe_load`. New
  `_strip_code_fence` helper. Unblocks ~30+ stranded sessions.
- **HATS-411** — PTY shutdown is bounded. `_pty_spawn` used to call
  blocking `ptyprocess.wait()`, which hung when a Claude/libuv child
  got stuck in macOS exit-pending state. Field repro 2026-05-20: 7
  simultaneously-stuck panes. New `pty_shutdown` module escalates
  grace → SIGTERM-pgroup → SIGKILL → `WNOHANG` reap. Returns exit
  code `124` (GNU `timeout` convention) when reap can't confirm exit.
  Timings overridable via `AI_HATS_PTY_GRACE_S` / `AI_HATS_PTY_TERM_S`.
- **HATS-412** — `HooksRunner` reads from canonical
  `<ai_hats_dir>/library/hooks/` instead of legacy `.agent/hooks/`.
  Latent since HATS-314's layout migration — skill-contributed
  `session_start` / `session_end` hooks silently never fired since.
- **HATS-400** — `ai-hats self update` re-execs auto-bump in a fresh
  Python interpreter when version changed. Old in-process call kept
  executing OLD in-memory code from the running update, so
  newly-delivered migrations didn't activate until a second
  `self bump`.
- **HATS-399** — Cleaned two stale legacy-path refs from bundled
  `library/` source. Without this, `bump`'s publish step kept
  re-injecting old paths into consumer mirrors, forcing the HATS-397
  healer to repeat work non-idempotently.
- **HATS-398** — `ai-hats self update` no longer pollutes "Recent
  changes" with `Merge branch 'task/hats-NNN'` titles. `git log` now
  passes `--no-merges`.
- **HATS-397** — `self bump` / `self update` self-heals stale
  legacy-path refs left in user-managed files after the v4 layout
  migration. JSON integration files (`.claude/settings.json{,.local}`)
  are always auto-rewritten; markdown / shell / template files are
  rewritten only when git-clean and otherwise listed in a session audit.

### Internal

- **HATS-456** — materialization facade (Phase 2 closure of HATS-452 /
  ADR-0005). New module `src/ai_hats/materialize.py` exposes
  `compose_for_role(assembler, role) -> CompositionResult` as the sole
  entry point; eight+ inlined `composer.compose(role, overlays=...)`
  sites now route through it. Drift-guard test fails on direct calls
  outside the facade. ADR-0005 appended with a "Phase 2" section.

## [0.6.0] - 2026-05-18

User-extensibility and reliability release. New CLI ergonomics on the
backlog (`task close`, `task link`/`unlink`, `task transition --force`)
shrink the brainstorm-to-done loop for work shipped on master. Harness
reliability lands: reporting pipeline steps can opt into zero-output
guards and timeout retry/escalation via a new `harness:` block. A new
E2E test gate (`dev_rule_e2e_gate`) requires real-subprocess coverage
for any CLI / shell / pip surface change, backed by the
`assert_command_exists` helper and a per-session plugin-dir refactor
that fixes a cluster of sub-agent skill-loading bugs while shaving
~4.5K composition tokens. Docs polish completes the 1.0 narrative
track: new `how-to-advanced.md` and `how-to-backlog.md`, a
numbered-refs convention sweep across all docs, and the glossary
extended with system roles, traits, and core skills.

### Fixed

- **`<ai_hats_dir>` placeholder leak in pipeline `save_artifact`** (HATS-395).
  HATS-380 fixed four writer surfaces (canonical writer, provider skill
  export, Claude/Gemini overrides, `SubAgentRunner._build_meta_prompt`)
  but missed the pipeline step
  `ai_hats.pipeline.steps.save.SaveArtifact`, which formatted templates
  like `<ai_hats_dir>/sessions/retros/judge/{ts}-report.md`
  (from `library/core/pipelines/reflect-all.yaml`) directly into
  `Path(...)` without expansion. Result: a recurring 0-byte file at the
  literal path `/<project>/<ai_hats_dir>/sessions/retros/judge/...`,
  reproduced on 2026-05-18 after the HATS-380 final fix had landed. The
  step now auto-adds `project_dir` to its `io.requires` whenever the
  template embeds `<ai_hats_dir>`, and expands the placeholder via
  `expand_path_placeholders` before the `.format()` call. Two new
  tests in `tests/test_pipeline_steps.py` lock both the
  expansion path (fails-under-revert) and backwards-compat for
  placeholder-free templates. The `placeholders.py` module docstring
  now lists all four writer gates.

### Changed

- **Judge pain-extraction protocol strengthened** (HATS-390). Two skills
  updated to make contrast-first reporting the default output of a
  judge sweep rather than a result of user push-back:
  - `library/core/skills/judge-protocol/SKILL.md` — new **Step 1.5
    "Inventory deliverables since prior report"** (window derived from
    prior report's ISO timestamp; first-run-ever falls back to last 7
    days) and **Step 3.5 "Counter-claims pass"** (devil's advocate
    gates: count-check, variance-vs-failure, shipped-vs-in-flight,
    survivor-bias). The report template now requires
    `## Deliverables since prior report` (before `## Hypotheses`) and
    `## Counter-claims` (before `## Notes`); section order is
    load-bearing. Step 3.5 ships with 3 few-shot examples that mirror
    the failure modes from session `20260518-140617-1` (over-stated
    cadence, mis-framed `inconclusive`, in-flight conflated with
    shipped regression) — the format trains behaviour rather than
    asserting a rule. Step 3 (PROP triage) gains a cost-citation
    heuristic: patience for cost-cited PROPs, faster `defer`/`reject`
    for uncited pain claims open ≥ 1 sweep cycle.
  - `library/core/skills/review-proposal/SKILL.md` — `--rationale`
    cost-citation rule formalised across Step 2b (create) and Step 3
    (triage). Field reference row updated; two new examples (✓ Good
    cost-cited PROP-036 with `9-test breakage + 1 plan pivot`, ✗ Bad
    uncited pain claim) document the precedent and anti-pattern.
    Out of scope: reuse of `self-retrospective` inside judge sweep (M4 —
    tracked separately via HYP-020) and any runtime/harness changes.
    Regression tracking is filed as a new HYP post-merge.

### Added

- **`assert_command_exists` test helper** (HATS-374). New
  `tests/_cli_helpers.py:assert_command_exists(*path)` shells out to
  `ai-hats <path> --help` and asserts exit 0. Lightweight catch for the
  "command moved between groups" bug class (HATS-333 bug B: bootstrap.sh
  kept referencing `ai-hats init` after HATS-242 nested it under `self`,
  and the unit test asserted only what bootstrap output — the missing
  command was invisible to the suite). Real subprocess → callers must
  carry `@pytest.mark.integration`. Sourced from PROP-032; signature
  generalised to variadic `*path` so 3-level paths (`task hyp create`)
  work without a None special case for top-level. Applied in
  `tests/test_bootstrap_sh.py` as a pre-flight check.

- **E2E test gate for CLI/shell/pip changes** (HATS-373). New rule
  `dev_rule_e2e_gate` requires any task that touches `src/ai_hats/cli/`,
  `scripts/*.sh`, `_bootstrap.py`, `cli/maintenance.py`, or
  `[project.scripts]` to include an e2e test under `tests/e2e/` (real
  bash + real pip + real `ai-hats` binary, `@pytest.mark.integration`)
  before transitioning to `done`. Pipeline-integration tests and
  in-process `CliRunner` tests do not satisfy the gate. Sourced from
  PROP-031; motivated by HATS-333, which shipped two production bugs
  (PEP 508 rejection of local-path `ai-hats @ /path`, click
  command-nesting drift) past a green unit suite that stubbed the very
  contracts the change broke.
- **`ai-hats-maintainer` trait** (HATS-373). New project-specific trait
  in `library/usage/traits/` bundling `dev_rule_e2e_gate` plus a
  plan-stage injection that names the gate. Attached to the `assistant`
  role. Reusable: any CLI-shipping ai-hats project can pin the same
  rule via its own maintainer trait without inheriting it globally.

- **Harness reliability** (HATS-378). Pipeline steps can opt into
  post-run validation via a new `harness:` block on the step YAML:
  ```yaml
  - id: run_session_review
    harness:
      reporting: true
      on_zero_output: harness_incident
      on_timeout: { retry: 1, budget_multiplier: 2, then: harness_incident }
  ```
  - **Zero-output guard** (HATS-323) — a reporting step whose sub-agent
    exited cleanly but emitted zero tokens AND zero tool calls now
    raises `HarnessZeroOutputError` instead of silently succeeding.
    Targets the failure mode in session `20260512-074105-1`
    (judge-for-role, 4 s, 0/0/0).
  - **Timeout retry + escalation** (HATS-321) — when `on_timeout` is
    set, `SubAgentRunner` retries a timed-out subprocess at the
    configured budget multiplier and raises `HarnessTimeoutError` only
    after retries are exhausted. Without a policy, the legacy behaviour
    (return session with `timed_out=True`) is preserved.
  - **New meta-PROP target `harness-incident`** — `reflect_session_main`
    routes `HarnessReliabilityError` (timeout, zero-output) to
    `target=harness-incident`, distinct from `target=session-reviewer`.
    Both targets dedup per failed session but coexist if both arise.
  - **Pipeline metrics** — `PipelineHarness.__exit__` writes
    `<run_dir>/pipeline_metrics.json` with `silent_zero_output_incidents`
    and `harness_timeout_incidents` counters.
  - Bundled pipelines `reflect-session`, `reflect-role`, `reflect-all`
    are opted-in. User pipelines without a `harness:` block keep
    pre-v0.6 behaviour.
- `ai-hats task close <id> --resolution "..."` — fast-close a task from
  `brainstorm`/`plan` straight to `done` for work shipped on master,
  without the worktree theatre. Subsumes the original "fast-close"
  request from HATS-172. (HATS-371)
- `ai-hats task link <FROM> <TO> [--type related|see-also|fold]` and
  `ai-hats task unlink ...` — cross-reference task cards. `related` /
  `see-also` are bidirectional; `fold` is directional and sets
  `folded_into` on the source. `ai-hats task show` renders outbound
  links plus inbound "Subsumed" backlinks. (HATS-371)
- `ai-hats task transition --force --reason "..."` — bypass the FSM
  guard for corrective overrides (e.g. undo an accidental
  `brainstorm → plan`); records the override in `work_log`. (HATS-371)
- `TaskCard` fields `related: []`, `see_also: []`, `folded_into: ""`.
  Round-trip is byte-clean: empty fields are not serialized. (HATS-371)
- E2E test `tests/e2e/test_task_cli.py` covering the HATS-371 task CLI
  surface (`task close`, `task link`/`unlink`, `task transition --force`)
  with real-subprocess assertions on state transitions, link rendering,
  and error exit codes. Closes the `dev_rule_e2e_gate` retroactively for
  HATS-371 during the v0.6 release cut. (HATS-370)

### Changed

- Task FSM diagram (`docs/assets/diagrams/backlog-task-fsm.d2`) refreshed
  to show the new `close` shortcuts and the `--force` override. (HATS-371)
- `ai-hats task list --search` now also matches against `related`,
  `see_also`, and `folded_into`. (HATS-371)

### Fixed

- `<ai_hats_dir>` placeholder is now expanded before skill/role/rule
  bodies reach the agent. Previously the LLM occasionally obeyed the
  literal token and wrote artefacts to `./<ai_hats_dir>/...` in the
  project root. Substitution happens at the writer layer
  (`Assembler._write_canonical_dir` and `BaseProvider.export_skills`);
  library source files keep the placeholder as canonical reference.
  (HATS-380)

## [0.5.0] - 2026-05-17

Bootstrap experience overhaul: `ai-hats self init` now opens an
interactive wizard that walks first-run users from provider pick to a
fully composed project, with advanced workspace setup (ai_hats_dir,
venv ownership, gitignore management) handled in-session by the
`initial-wizard` role. The built-in library splits into
`library/core/` (engine) and `library/usage/` (content) to document the
engine/content boundary. New narrative docs (`how-to-configure.md`,
`how-to-extend.md`, `glossary.md`) plus five d2-rendered process
diagrams replace ad-hoc references. Legacy Russian README and historical
migration guides retired.

### Changed

- Built-in library moved from `src/ai_hats/libraries/` to a root-level
  `library/` directory and split into two layers: `library/core/`
  (engine fundament — system roles, base traits, global rules,
  foundational skills, all pipelines + injections, provider
  templates) and `library/usage/` (curated content catalog —
  opinionated roles, domain traits, opt-in skills). Both ship inside
  the installed `ai_hats.library` sub-package; no runtime behavior
  change for users. The split documents the engine/content boundary
  and unblocks future content-package extraction. (HATS-363)
- Python module `ai_hats.library` (containing `LibraryResolver`) was
  renamed to `ai_hats.resolver` to free up `ai_hats.library` as a
  data sub-package. Import updates: `from ai_hats.resolver import
  LibraryResolver`. (HATS-363)
- Bootstrap wizard's advanced setup (project dir, venv ownership,
  `.gitignore` management) moved from three hardcoded `click.prompt`s
  into Step 2 of the in-session `initial-wizard` flow; the LLM
  explains trade-offs and applies values via new `ai-hats config set`
  flags: `--ai-hats-dir`, `--venv` / `--no-venv`,
  `--manage-gitignore` / `--no-manage-gitignore`. The
  `--ai-hats-dir` form invokes a new `Assembler.relocate()` path
  that moves `library/`, `tracker/`, `sessions/`, `STATE.md`,
  recreates the managed venv, and updates `ai-hats.yaml` +
  `.gitignore` atomically (refuses on collisions, idempotent on
  retry). Closes a footgun where manual yaml edits to `ai_hats_dir`
  silently broke projects. The three `self init` flags
  (`--ai-hats-dir`, `--venv`, `--no-manage-gitignore`) remain
  unchanged for scripted use. (HATS-366)
- All user-facing docs (`docs/ARCHITECTURE.md`, the `docs/how-to-*.md`
  set, and the diagram-preview pages) are now English-only. The
  README hints that browser auto-translate handles other languages
  cleanly. (HATS-352)
- `docs/how-to-feedback-loop.md` synced with HATS-252 reality (role
  names, schema versions, harness validation flow); the "Concept
  minimum" section was thinned to point at `docs/glossary.md` for
  core terms, retaining only loop-specific Verdict and Reflect-all
  handoff definitions. (HATS-354, HATS-361)
- `initial-wizard` role injection rewritten: session-opener template
  moved into the system prompt to fix POV-confusion (the wizard
  no longer fumbles single-word language replies); companion docs
  catalog now baked in so the model can pull `docs/how-to-configure.md`
  / `glossary.md` / `how-to.md` / `how-to-feedback-loop.md` /
  `how-to-extend.md` on demand. Step 1 hardened against echo-questions.
  Advanced-setup CLI prompt no longer leaks raw `[dim]…[/]` markup.
  (HATS-355)
- Documentation now uses `<ai_hats_dir>/*` instead of the legacy
  `.agent/*` path notation, matching projects that override the
  bootstrap directory. (HATS-362)

### Added

- `ai-hats self init` defaults to an interactive bootstrap wizard:
  CLI step picks a provider (smart-default by `~/.<provider>`) and
  writes a minimal `ai-hats.yaml`, then an in-session
  `initial-wizard` role takes over — detects the stack, asks for the
  conversation language, recommends a base role, runs an in-session
  `config customize` walk, sets the task prefix, and selects the
  reflection policy. Backwards-compatible: `-p X -r Y`,
  `--no-wizard`, or non-TTY stdin falls back to the flag-only
  scripted path; non-TTY without flags fails fast with guidance.
  The wizard path self-updates ai-hats from GitHub before starting
  so first-run users land on the freshest framework version
  (`--no-update` skips). Slow `pip install` invocations in both the
  wizard self-update and `ai-hats self update` now show a
  `console.status()` dots spinner. New `ai-hats config set
  --task-prefix` overrides an existing prefix (unlike
  `self init --task-prefix`, which errors on conflict). (HATS-347)
- Five d2-rendered process diagrams in `docs/ARCHITECTURE.md`
  covering session lifecycle, auto reflect-session, manual
  reflect-all, backlog state machines (task / HYP / PROP), and the
  composition flow. Brand-light palette finalized; `docs/diagrams-preview.md`
  ships theme + sketch + custom-palette galleries, and the README
  links architecture-page thumbnails for browse-friendly navigation.
  (HATS-348)
- `docs/how-to-extend.md` — new guide for authoring own roles / traits /
  rules / skills, override-precedence chain, and replacing system
  roles. (HATS-363)
- `docs/how-to-configure.md` — single narrative walkthrough for
  first-time project setup: `ai-hats.yaml` fields, wizard vs scripted
  init (six wizard steps documented inline), role pick, customization,
  feedback policy, venv ownership, verify and pitfalls. Becomes the
  recommended entry-point after README. Also absorbs the venv-ownership
  section formerly under `docs/how-to.md` §9. (HATS-355)
- `docs/glossary.md` — naming source-of-truth for ai-hats core concepts
  (Provider, Session, Role, Trait, Rule/Skill, Backlog, Reflect,
  Worktree, Artifacts). Linked from README and CONTRIBUTING; new docs
  reference it instead of redefining terms. (HATS-361)

### Removed

- `docs/README.ru.md`. Browser translation replaces the maintained
  Russian mirror. (HATS-352)
- `docs/migration.md` (pipx → launcher) and `docs/migration-311.md`
  (v3 → v4 layout). Migration tooling is no longer a project flow:
  breaking changes ship cleanly per release, and both legacy
  migrations are long past. References in README and `docs/how-to.md`
  removed. (HATS-356)

## [0.4.0] - 2026-05-16

First public release. The repository, its history, and its docs have
been audited for sensitive data; an English-first landing page has
been added; the public surface (CLI, `ai-hats.yaml` schema, tracker
format, skill format) is documented and SemVer-protected. CI gates
every PR.

### Added

- `LICENSE` — MIT license.
- `SECURITY.md` — disclosure channel and supported-version policy.
- `CONTRIBUTING.md` — dev setup, commit conventions, and a "what not to
  commit" section that complements the privacy pre-commit hook.
- `docs/RELEASING.md` — SemVer policy, breaking-change protocol, and the
  manual release checklist.
- `docs/ARCHITECTURE.md` — internal model (components, composition,
  task state machine, project structure, library layout, skill format).
- `docs/how-to-orchestration.md` — fan-out scenarios, session tags,
  `--json` output, exit-code contract.
- `docs/README.ru.md` — Russian README for native-language readers.
- `docs/assets/` — logo (multiple sizes + SVG silhouette), social-card
  PNG (1280×640), demo GIF/MP4, and a `README.md` with the regeneration
  pipeline (Gemini prompt, ImageMagick post-processing, vhs invocation).
- `scripts/demo.tape` — vhs script that records the README hero demo
  from real ai-hats state (config status → session list → active hyps).
- `.github/ISSUE_TEMPLATE/` (bug report + feature request) and
  `.github/PULL_REQUEST_TEMPLATE.md`.
- `.github/workflows/ci.yml` — GitHub Actions pipeline: ruff lint, test
  matrix (Python 3.11 / 3.12 / 3.13) with a **78% coverage gate**,
  bandit (`-ll`) + pip-audit security scan, install-smoke that runs
  `scripts/install-launcher.sh` on a clean runner.
- `.github/dependabot.yml` — weekly pip + github-actions update PRs.
- `pyproject.toml`: `license = "MIT"`, `license-files`, `authors`,
  PyPI classifiers; `setuptools` build requirement bumped to ≥77 so the
  PEP 639 `license-files` key resolves; `[dev]` extras now include
  `bandit>=1.7` and `pip-audit>=2.7`; `[tool.coverage.*]` +
  `[tool.bandit]` configs.
- Privacy hook: new patterns for Claude session markers
  (`sessionId` / `requestId`, `"cwd": "/...`, structural JSONL keys like
  `parentUuid` / `toolUseResult`) plus a lower size threshold for new
  fixtures under `tests/fixtures/`.
- README CI status badge.

### Changed

- `README.md` is now English-first; the Russian version moves to
  `docs/README.ru.md` with a language switch in both. README trimmed
  from 554 lines to ~125 — most reference-grade content moved to
  dedicated `docs/*.md` files.
- `docs/migration-333.md` renamed to `docs/migration.md`; this is the
  canonical migration guide. References in `README.md`, `docs/how-to.md`,
  and `docs/migration-311.md` updated.

### Removed

- Internal-ticket references in user-facing prose (`HATS-NNN` tags in
  README, the "repo is still private" warning).
- The duplicated "how to update ai-hats in a project" block in README
  (already covered by the Quick-start step 3).

### Security

- **Fix CWE-377 / bandit B306 in `providers.py`:** switched from
  `tempfile.mktemp` (TOCTOU race — between `mktemp` returning the path
  and `write_text` creating the file an attacker on the same host
  could pre-create it) to `tempfile.mkstemp`, which atomically opens
  an fd at mode 0600.
- Purged `tests/fixtures/real_conversation.jsonl` from working tree and
  from the entire git history. The fixture carried a real Claude Code
  session: absolute `cwd`, `sessionId`, `requestId`, subscription tier,
  and unredacted user prompts.
- Stopped tracking `tests/fixtures/real_conversation.jsonl` and
  `tests/fixtures/real_trace.log` via `.gitignore` so debug captures
  cannot land again.
- Rewrote git history with `git filter-repo`:
  - dropped the fixture from every commit reachable from any ref,
  - replaced `/Users/<dev>/dev/...` paths with `/path/to/...` in blob
    diffs and commit messages,
  - rewrote author / committer email to `f@muratovv.me` (438 commits
    re-hashed).
- Pre-commit privacy hook hardened with Claude-session detection
  (`sessionId` / `requestId` / `cwd` / structural JSONL keys) and a
  lower soft-warn threshold for new files in `tests/fixtures/`.

## [0.3.0] — 2026-04 / pre-public

The state of the project before the public-release sweep. Tracked
in detail in the git log and the on-disk `tracker/` backlog (HATS-001
through HATS-340). Headline themes:

- Venv-first launcher architecture (HATS-333..340).
- Pipelines and composer subsystem (HATS-261..287).
- Reflection / feedback loop and the session-reviewer role.
- Multi-provider injection (Claude and Gemini).
- Worktree isolation for sub-agents.
- Tracker primitives: tasks with a state machine, hypotheses (HYP),
  proposals (PROP).

This entry is intentionally terse — versions before the public release
were maintained in a private repository and documented in commit
messages rather than this changelog. The Unreleased section above is
where the public changelog history starts.

[Unreleased]: https://github.com/muratovv/ai-hats/compare/v0.15.0...HEAD
[0.15.0]: https://github.com/muratovv/ai-hats/compare/v0.14.0...v0.15.0
[0.14.0]: https://github.com/muratovv/ai-hats/compare/v0.13.2...v0.14.0
[0.13.2]: https://github.com/muratovv/ai-hats/compare/v0.13.1...v0.13.2
[0.13.1]: https://github.com/muratovv/ai-hats/compare/v0.13.0...v0.13.1
[0.13.0]: https://github.com/muratovv/ai-hats/compare/v0.12.0...v0.13.0
[0.12.0]: https://github.com/muratovv/ai-hats/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/muratovv/ai-hats/compare/v0.10.0...v0.11.0
[0.10.0]: https://github.com/muratovv/ai-hats/compare/v0.9.0...v0.10.0
[0.9.0]: https://github.com/muratovv/ai-hats/compare/v0.8.0...v0.9.0
[0.8.0]: https://github.com/muratovv/ai-hats/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/muratovv/ai-hats/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/muratovv/ai-hats/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/muratovv/ai-hats/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/muratovv/ai-hats/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/muratovv/ai-hats/releases/tag/v0.3.0
