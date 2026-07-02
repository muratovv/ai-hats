# ai-hats-wt

A hook-agnostic **git-worktree engine**: create, merge, and discard linked git
worktrees with a layered file-locking concurrency model — from a bare `git`
repository, with no configuration.

`ai-hats-wt` is the worktree core extracted from the
[ai-hats](https://github.com/muratovv/ai-hats) framework. It has no dependency on
ai-hats: everything below runs against a plain `git init` with no `ai-hats.yaml`,
no composition, and no tracker. Its only runtime dependencies are
[`ai-hats-core`](https://pypi.org/project/ai-hats-core/) (dependency-free
filesystem primitives) and [`filelock`](https://pypi.org/project/filelock/).

## Install

```sh
pip install ai-hats-wt
```

Requires Python 3.11+.

## Quickstart

Drive the full create → merge (or → discard) lifecycle on any git repo:

```python
from pathlib import Path
from ai_hats_wt import WorktreeManager

project = Path("/path/to/a/git/repo")

# A manager for one linked worktree on its own branch.
mgr = WorktreeManager(project, branch_name="feature/x")

wt_path = mgr.create()   # linked worktree checked out on feature/x
mgr.save_state()         # persist state under <project>/.wt

# ... make commits inside wt_path ...

mgr.merge()              # land the branch on the base, remove the worktree
# or:
# mgr.discard()          # throw the worktree + branch away, land nothing
```

That is the entire happy path. `WorktreeManager` defaults to the **no-op**
lifecycle and a **project-local** state directory (`<project>/.wt`), so a bare
consumer needs nothing else. The same flow is exercised end-to-end by the
package's `test_wt_standalone.py`.

## Public API

The supported surface is `ai_hats_wt.__all__`:

| Symbol                                                               | Purpose                                                            |
| -------------------------------------------------------------------- | ------------------------------------------------------------------ |
| `WorktreeManager`                                                    | Create / merge / discard a linked worktree; the git probes.        |
| `IsolationMode`                                                      | Isolation / teardown mode (`DISCARD`, `SQUASH`, `BRANCH`, `NONE`). |
| `assert_head_is_canonical_base`                                      | Guard: refuse to branch off a non-canonical HEAD.                  |
| `WorktreeLifecycle`, `LifecycleContext`, `NOOP_LIFECYCLE`            | The lifecycle extension-point (see below).                         |
| `WorktreeDirtyError`, `WorktreeCreateError`, `WorktreeDriftError`, … | Typed exceptions — the clean failure seam.                         |

The full exception set (`WorktreePartialCleanupError`, `WorktreeRemoveError`,
`OriginalBranchMissingError`, `WorktreeStateLostError`,
`WorktreeStateIncompleteError`, `WorktreeBaseBranchError`,
`WorktreeBaseBranchMismatchError`, `WorktreeMainRepoMidMergeError`,
`WorktreeTeardownAborted`, `WorktreeLockError`) lets a host catch precise
failures instead of parsing git stderr. Per-method behaviour is documented on
the code — import a symbol and read its docstring; this README is the entry
point, not the reference.

## Lifecycle hooks (extension-point)

`WorktreeManager` runs no hooks by default (`NOOP_LIFECYCLE`). A host can inject
behaviour at the create/merge/discard boundaries by passing its own
`WorktreeLifecycle` bundle:

```python
from ai_hats_wt import WorktreeManager, WorktreeLifecycle, LifecycleContext

class MyLifecycle(WorktreeLifecycle):
    def on_created(self, ctx: LifecycleContext) -> None:
        ...  # e.g. seed files into ctx.worktree_path

    def before_teardown(self, event: str, ctx: LifecycleContext) -> None:
        ...  # event is "merge" / "discard" / "cleanup"

mgr = WorktreeManager(project, branch_name="feature/x", lifecycle=MyLifecycle())
```

This is the seam ai-hats uses to run its own carry/hook layer; a standalone
consumer typically leaves the default no-op in place.

## Concurrency

Worktree operations are serialized by a layered file-locking model (state locks,
git-index-lock retry, ref-lock waits) so that concurrent `create` / `merge`
calls on the same repo do not corrupt each other. It is on by default; there is
nothing to configure.

## Dependencies

- [`ai-hats-core`](https://pypi.org/project/ai-hats-core/) — atomic filesystem I/O.
- [`filelock`](https://pypi.org/project/filelock/) — the cross-process lock backend.

Everything else is the Python standard library.

## Versioning

[SemVer](https://semver.org/). The public API is `ai_hats_wt.__all__`; breaking
changes to it bump the major version. Submodule internals (anything not in
`__all__`) are not part of the contract.

## License

MIT. See the [ai-hats repository](https://github.com/muratovv/ai-hats) for the
full license and contribution guide.
