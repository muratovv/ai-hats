# ai-hats-tracker

A standalone **task-card schema + worktree-free task state machine**: create a
task, walk it through the `brainstorm → plan → execute → document → review →
done` lifecycle, link cards, log work, and render `STATE.md` — on a bare
directory, with no configuration.

`ai-hats-tracker` is the tracker core extracted from the
[ai-hats](https://github.com/muratovv/ai-hats) framework. It has no dependency on
the `ai-hats` integrator: everything below runs against a plain directory with no
`ai-hats.yaml`, no composition, and no worktree engine. Its only runtime
dependencies are [`ai-hats-core`](https://pypi.org/project/ai-hats-core/)
(dependency-free filesystem primitives), [`pydantic`](https://pypi.org/project/pydantic/),
[`PyYAML`](https://pypi.org/project/PyYAML/), and
[`filelock`](https://pypi.org/project/filelock/).

## Install

```sh
pip install ai-hats-tracker
```

Requires Python 3.11+.

## Status

Version `0.1.0`: the TaskCard schema and the worktree-free task FSM. The
standalone backlog CLI (0.2.0) and hypotheses/proposals (0.3.0) follow in later
increments.

## License

MIT. See the [ai-hats repository](https://github.com/muratovv/ai-hats) for the
full license and contribution guide.
