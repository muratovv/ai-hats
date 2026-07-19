"""HATS-1042 §3: named-edge alias event keys.

A declared edge ``name`` adds a stable alias key ``edge:<name>`` the dispatcher
matches IN ADDITION to the canonical ``edge:<from>--<to>`` — one event, two
match keys. The canonical key and every existing subscription are untouched;
an unnamed edge (and a kernel with no edge-name map) produces no alias key, so
the default behavior is unchanged.
"""

from __future__ import annotations

from rack_testkit import StubSubscriber, in_lock, make_kernel, post_lock, walk

# The packaged tasks defaults: the only two named edges.
EDGE_NAMES = {("execute", "execute"): "reclaim", ("done", "execute"): "reopen"}


def _create(kernel, cwd, task_id="T-1"):
    kernel.create(actor="t", caller_cwd=cwd, task_id=task_id, title="probe")


def test_alias_subscriber_fires_on_named_self_loop(tasks_dir, cwd):
    alias = StubSubscriber("alias", [in_lock("edge:reclaim")])
    canonical = StubSubscriber("canonical", [in_lock("edge:execute--execute")])
    kernel = make_kernel(tasks_dir, subscribers=[alias, canonical], edge_names=EDGE_NAMES)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", "execute", cwd=cwd)
    kernel.transition("T-1", "execute", actor="t", caller_cwd=cwd)  # reclaim self-loop

    assert len(alias.contexts) == 1  # alias key matched additively
    assert len(canonical.contexts) == 1  # canonical subscriber untouched
    event = alias.contexts[0].event
    assert event.key == "edge:execute--execute"  # canonical key unchanged
    assert event.alias_key == "edge:reclaim"


def test_alias_matches_post_lock_reactions(tasks_dir, cwd):
    alias = StubSubscriber("alias", [post_lock("edge:reopen")])
    kernel = make_kernel(tasks_dir, subscribers=[alias], edge_names=EDGE_NAMES)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", "execute", "document", "review", "done", cwd=cwd)
    kernel.transition("T-1", "execute", actor="t", caller_cwd=cwd)  # reopen done->execute

    assert len(alias.contexts) == 1
    assert alias.contexts[0].event.alias_key == "edge:reopen"


def test_unnamed_edge_produces_no_alias_key(tasks_dir, cwd):
    alias = StubSubscriber("alias", [in_lock("edge:reclaim")])
    canonical = StubSubscriber("canonical", [in_lock("edge:plan--execute")])
    kernel = make_kernel(tasks_dir, subscribers=[alias, canonical], edge_names=EDGE_NAMES)
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", cwd=cwd)
    kernel.transition("T-1", "execute", actor="t", caller_cwd=cwd)  # plan->execute, unnamed

    assert len(canonical.contexts) == 1
    assert alias.contexts == []  # unnamed edge → no alias key, alias subscriber silent
    assert canonical.contexts[0].event.alias_key is None


def test_default_kernel_has_no_alias_keys(tasks_dir, cwd):
    # Zero behavior change: no edge-name map → no alias key on any edge.
    canonical = StubSubscriber("canonical", [in_lock("edge:plan--execute")])
    kernel = make_kernel(tasks_dir, subscribers=[canonical])
    _create(kernel, cwd)
    walk(kernel, "T-1", "plan", cwd=cwd)
    kernel.transition("T-1", "execute", actor="t", caller_cwd=cwd)

    assert len(canonical.contexts) == 1
    assert canonical.contexts[0].event.alias_key is None
