"""Consumer add-ons for the rack kernel (HATS-1023, epic HATS-1014 K4).

:class:`HookRunnerExtension` executes the materialized consumer bash hooks
(``<ai_hats_dir>/tracker/lifecycle-hooks/<from>--<to>.d/*``) on every FSM
edge — the in-process replica of the git ``dispatcher.sh`` loop, with its
fail-OPEN on a missing event dir INVERTED to fail-CLOSED (fix #2, HATS-593
backstop semantics). It lives on the integrator side, not in the rack: the
K1 import-hygiene pin forbids ``subprocess`` in the whole rack package (the
kernel never shells out), the K3 precedent for effectful extensions. Wire it
through ``build_rack_kernel(extra_subscribers=consumer_subscribers(...))``.
"""  # comment-length: allow — placement decision (rack hygiene pin) is load-bearing

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Sequence

from ai_hats_rack.dispatch import AbortOperation, Phase, Subscription
from ai_hats_rack.events import EdgeEvent
from ai_hats_rack.extensions import (
    DEFAULT_PLAN_SECTIONS,
    Section,
    load_sections,
    merge_sections,
)
from ai_hats_rack.fsm import Topology, load_topology

from .lifecycle_hooks import (
    MANIFEST_NAME,
    PLAN_SECTIONS_FILENAME,
    lifecycle_hooks_dir,
)
from .sweeper import read_marker_names

# Single loud-fail timeout pattern (epic HATS-1014 §2.2 rule 5): an in-lock
# hook holds the task lock — a hang must abort, never wait forever.
HOOK_TIMEOUT = 30.0

_REASON_LIMIT = 2000  # tail of hook output carried into the abort reason

_REPAIR = "Run 'ai-hats self init' to repair"


class HookRunnerExtension:
    """Executes consumer lifecycle hooks on every FSM edge (in-lock).

    Priority 15 — after the stock plan-gate (10), BEFORE ownership claim (20)
    and worktree setup (30): a consumer abort leaves zero resource side
    effects (fix #1). Scripts run in lexicographic order; the first rc≠0
    aborts the transition with the hook's output as the reason (the reason
    channel). Manifest-expected-but-broken is a hard abort (fail-CLOSED);
    an event with no managed entries and no dir is legitimately empty.
    """  # comment-length: allow — ordering + fail-closed contract

    name = "hook-runner"

    def __init__(
        self,
        hooks_dir: Path,
        tasks_dir: Path,
        *,
        project_dir: Path,
        topology: Topology | None = None,
        priority: int = 15,
        timeout: float = HOOK_TIMEOUT,
    ) -> None:
        self.hooks_dir = hooks_dir
        self.tasks_dir = tasks_dir
        self.project_dir = project_dir
        self._topology = topology if topology is not None else load_topology()
        self._priority = priority
        self.timeout = timeout

    def subscriptions(self) -> Sequence[Subscription]:
        """The full ``edge:`` product (not just legal edges): forced
        transitions fire real non-topology keys — mirror of the K3 safety
        subscribers in ``rack_wiring``."""
        states = self._topology.states
        return [
            Subscription(f"edge:{src}--{dst}", Phase.IN_LOCK, self._priority)
            for src in states
            for dst in states
            if src != dst or src == "execute"  # + reclaim self-loop (HATS-955)
        ]

    def on_event(self, ctx) -> None:
        if not isinstance(ctx.event, EdgeEvent):
            return None
        event = f"{ctx.event.from_state}--{ctx.event.to_state}"
        self._assert_manifest_intact(event)
        event_dir = self.hooks_dir / f"{event}.d"
        if not event_dir.is_dir():
            return None  # nothing expected (manifest checked) and nothing present
        for script in sorted(event_dir.iterdir()):
            if script.name.startswith(".") or not script.is_file():
                continue
            if not os.access(script, os.X_OK):
                # The dispatcher.sh silent `continue` is exactly the HYP-078
                # fail-open hole — a hook that cannot run fails the gate loud.
                raise AbortOperation(
                    f"lifecycle hook '{script.name}' for '{event}' is not "
                    f"executable — refusing to skip it silently. {_REPAIR}"
                )
            self._run_script(script, event, ctx)
        return None

    def _assert_manifest_intact(self, event: str) -> None:
        """Fail-CLOSED backstop (fix #2): every manifest-listed hook of THIS
        event must exist and be executable — a missing event dir or swept
        script means a degraded gate, never a silent pass (inversion of
        dispatcher.sh's fail-open on a missing ``<event>.d``)."""
        expected = {
            rel
            for rel in read_marker_names(self.hooks_dir / MANIFEST_NAME)
            if rel.startswith(f"{event}.d/")
        }
        for rel in sorted(expected):
            target = self.hooks_dir / rel
            if not target.is_file() or not os.access(target, os.X_OK):
                raise AbortOperation(
                    f"lifecycle hooks corrupt — manifest expects managed hook "
                    f"'{rel}' but it is missing or non-executable; refusing to "
                    f"run a degraded '{event}' gate. {_REPAIR}"
                )

    def _run_script(self, script: Path, event: str, ctx) -> None:
        env = dict(os.environ)
        env.update(
            {
                "AI_HATS_HOOK_EVENT": event,
                "AI_HATS_HOOK_TASK_FILE": str(self.tasks_dir / ctx.task.id / "task.yaml"),
                "AI_HATS_HOOK_FROM": ctx.event.from_state,
                "AI_HATS_HOOK_TO": ctx.event.to_state,
                "AI_HATS_HOOK_IS_EPIC": "1" if ctx.is_epic else "0",
                "AI_HATS_HOOK_FORCE": "1" if ctx.force else "0",
                "AI_HATS_HOOK_REASON": ctx.reason,
                "AI_HATS_HOOK_ACTOR": ctx.actor,
            }
        )
        try:
            proc = subprocess.run(  # noqa: S603 — materialized managed hook, argv is its path
                [str(script)],
                cwd=str(self.project_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
        except subprocess.TimeoutExpired:
            raise AbortOperation(
                f"lifecycle hook '{script.name}' timed out after "
                f"{self.timeout:.0f}s on '{event}' — a hung hook holds the "
                f"task lock; fix the hook or remove its declaration and "
                f"re-run 'ai-hats self init'"
            ) from None
        except OSError as exc:
            raise AbortOperation(
                f"lifecycle hook '{script.name}' failed to start on '{event}': "
                f"{exc}. {_REPAIR}"
            ) from exc
        if proc.returncode != 0:
            detail = "\n".join(
                part for part in (proc.stdout.strip(), proc.stderr.strip()) if part
            )[-_REASON_LIMIT:] or "(hook produced no output)"
            raise AbortOperation(
                f"lifecycle hook '{script.name}' rejected '{event}' "
                f"(exit {proc.returncode}): {detail}"
            )


def consumer_plan_sections(project_dir: Path) -> tuple[Section, ...]:
    """The stock section catalog extended by the materialized consumer config
    (``plan-sections.yaml``); absent config → stock only. Base-wins merge —
    a consumer cannot weaken a stock section (see ``merge_sections``)."""
    path = lifecycle_hooks_dir(project_dir) / PLAN_SECTIONS_FILENAME
    if not path.is_file():
        return DEFAULT_PLAN_SECTIONS
    return merge_sections(DEFAULT_PLAN_SECTIONS, load_sections(path))


def consumer_subscribers(
    project_dir: Path,
    *,
    tasks_dir: Path | None = None,
    topology: Topology | None = None,
    timeout: float = HOOK_TIMEOUT,
) -> list:
    """The consumer add-on pack for ``build_rack_kernel(extra_subscribers=…)``."""
    if tasks_dir is None:
        from .tracker_wiring import tracker_paths

        tasks_dir = tracker_paths(project_dir).tasks_dir
    return [
        HookRunnerExtension(
            lifecycle_hooks_dir(project_dir),
            tasks_dir,
            project_dir=project_dir,
            topology=topology,
            timeout=timeout,
        )
    ]


__all__ = [
    "HOOK_TIMEOUT",
    "HookRunnerExtension",
    "consumer_plan_sections",
    "consumer_subscribers",
]
