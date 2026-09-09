"""Session command middleware for role-declared consent operations."""

from __future__ import annotations

import json
import os
import shutil
import string
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from ai_hats_core.layout import ProjectLayout

from ai_hats_library.hooks.consent_gate import Operation, Outcome, Verdict, operations
from ai_hats_library.hooks.consent_gate.issue import DEFAULT_WINDOW_MINUTES


REFUSED = 2
_TICKET_ENV = "AI_HATS_CONSENT_TICKET"
_GLOBAL_ACK = "AI_HATS_CONSENT_ACK"
CONFIG_ENV = "AI_HATS_CONSENT_WRAPPER_CONFIG"
REQUEST_ENV = "AI_HATS_CONSENT_REQUEST_ID"
_ARROW = "->"
_STATE_CHARS = frozenset(string.ascii_letters + string.digits + "_.-")
_SOURCE_QUERY_TIMEOUT_S = 30


@dataclass(frozen=True)
class WrapperConfig:
    project_dir: Path
    originals: Mapping[str, str]
    policy: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class MatchedOperation:
    operation: Operation
    ticket_subject: str | None
    legacy_flags: tuple[str, ...]


class ConsentPolicyError(ValueError):
    """A role declared consent middleware the session cannot enforce."""


def policy_from(points: Sequence[object], *, registry=None) -> dict[str, tuple[str, ...]]:
    grouped: dict[str, list[str]] = {}
    for point in points:
        if getattr(point, "app", "") != "consent_gate":
            raise ConsentPolicyError(
                f"{getattr(point, 'declared_by', '<unknown>')!r}: consent may only be "
                "declared under apps.consent_gate"
            )
        path = tuple(getattr(point, "path", ()))
        selector = str(getattr(point, "selector", ""))
        declared_by = str(getattr(point, "declared_by", "<unknown>"))
        if len(path) != 1:
            raise ConsentPolicyError(
                f"{declared_by!r}: apps.consent_gate requires exactly one operation key"
            )
        operation = str(path[0])
        spec = operations.spec_for(operation, registry=registry)
        if spec is None:
            raise ConsentPolicyError(f"unsupported consent operation {operation!r}")
        reason = spec.selector_reason(selector)
        if reason is not None:
            raise ConsentPolicyError(
                f"{declared_by!r}: invalid {operation} selector {selector!r} — {reason}"
            )
        selectors = grouped.setdefault(operation, [])
        if selector not in selectors:
            selectors.append(selector)
    return {operation: tuple(selectors) for operation, selectors in grouped.items()}


def _source_required(selectors: Sequence[str], target: str) -> bool:
    matching_sources = [
        selector.partition(_ARROW)[0]
        for selector in selectors
        if selector.partition(_ARROW)[2] == target
    ]
    return bool(matching_sources) and "" not in matching_sources


def _tasks_dir_args(argv: Sequence[str]) -> list[str]:
    for index, argument in enumerate(argv):
        if argument == "--tasks-dir" and index + 1 < len(argv):
            return [argument, argv[index + 1]]
        if argument.startswith("--tasks-dir="):
            return [argument]
    return []


def _resolve_transition_source(
    original: str,
    task_id: str,
    argv: Sequence[str],
    environ: Mapping[str, str],
) -> str:
    command = [original, "context", task_id, *_tasks_dir_args(argv), "--json"]
    try:
        result = subprocess.run(  # noqa: S603
            command,
            env=dict(environ),
            check=False,
            capture_output=True,
            text=True,
            timeout=_SOURCE_QUERY_TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConsentPolicyError(f"cannot resolve {task_id} source state: {exc}") from exc
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}"
        raise ConsentPolicyError(f"cannot resolve {task_id} source state: {detail}")
    try:
        payload = json.loads(result.stdout)
        state = payload["task"]["state"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise ConsentPolicyError(
            f"cannot resolve {task_id} source state: invalid context JSON"
        ) from exc
    if not isinstance(state, str) or not state:
        raise ConsentPolicyError(f"cannot resolve {task_id} source state: invalid state")
    return state


def match_operation(
    surface: str,
    argv: Sequence[str],
    policy: Mapping[str, tuple[str, ...]],
    *,
    source_state: str | None = None,
    registry: dict | None = None,
) -> MatchedOperation | None:
    """The declared operation ``argv`` invokes on ``surface``, or ``None``.

    The verb is read by the registry, never here: this half and the PreToolUse
    gate had drifted into two grammars, and three shapes already disagreed
    (HATS-1816). A branch that parsed argv locally would restore the drift.
    """
    for operation, selectors in policy.items():
        spec = operations.spec_for(operation, registry=registry)
        if spec is None:
            continue
        reading = operations.read(operation, surface, argv)
        if reading is None or not spec.admits(selectors, source_state, reading):
            continue
        return MatchedOperation(
            operation=Operation(operation, subject=reading.subject, label=reading.label),
            ticket_subject=reading.subject if spec.binds_ticket else None,
            legacy_flags=spec.legacy_flags(reading),
        )
    return None


def _spawn(command: list[str], environ: Mapping[str, str]) -> int:
    # The executable was resolved before the session PATH was wrapped.
    return subprocess.run(command, env=dict(environ), check=False).returncode  # noqa: S603


# One spelling of the wrapper's layout. The predicate below recognises what
# `materialize_consent_wrappers` writes, so the two must never drift: a rename
# on one side alone silently disarms BOTH recursion barriers.
_WRAPPER_DIR_NAME = "consent-wrapper"
_WRAPPER_BIN_NAME = "bin"


def _is_consent_wrapper_path(path: str | Path) -> bool:
    resolved = Path(path).resolve()
    # the bin dir itself, or an executable sitting in it
    return any(
        candidate.name == _WRAPPER_BIN_NAME and candidate.parent.name == _WRAPPER_DIR_NAME
        for candidate in (resolved, resolved.parent)
    )


def _original_lookup_path(search_path: str) -> str:
    return os.pathsep.join(
        entry for entry in search_path.split(os.pathsep) if not _is_consent_wrapper_path(entry)
    )


def _record_grant(answer: Verdict, operation: Operation, project_dir: Path) -> bool:
    from .consent_port import record_use

    return record_use(answer, operation, hook="consent_wrapper.py", project_dir=project_dir)


def _record_legacy(flag: str, operation: Operation, project_dir: Path) -> bool:
    from ai_hats_library.hooks.bypass_journal import journal_bypass

    return journal_bypass(
        "hatch",
        f"{flag} ({operation.type})",
        hook="consent_wrapper.py",
        cmd=" ".join(sys.argv),
        cwd=project_dir,
    )


def _record_request(request_id: str, operation: Operation, project_dir: Path) -> bool:
    from ai_hats_library.hooks.bypass_journal import journal_bypass

    return journal_bypass(
        "consent",
        json.dumps(
            {"request_id": request_id, "operation": operation.type, "phase": "ticket_consumed"}
        ),
        hook="consent_wrapper.py",
        cwd=project_dir,
    )


def run_wrapped(
    surface: str,
    argv: Sequence[str],
    config: WrapperConfig,
    *,
    environ: Mapping[str, str] | None = None,
    check_grant: Callable[[Operation, Path], Verdict],
    peek_ticket: Callable[[str | None, Sequence[str]], bool],
    consume_ticket: Callable[[str | None, Sequence[str]], bool],
    record_grant: Callable[[Verdict, Operation, Path], bool] = _record_grant,
    record_legacy: Callable[[str, Operation, Path], bool] = _record_legacy,
    record_request: Callable[[str, Operation, Path], bool] = _record_request,
    resolve_transition_source: Callable[
        [str, str, Sequence[str], Mapping[str, str]], str
    ] = _resolve_transition_source,
    spawn: Callable[[list[str], Mapping[str, str]], int] = _spawn,
) -> int:
    env = dict(os.environ if environ is None else environ)
    original = config.originals[surface]
    if _is_consent_wrapper_path(original):
        print(
            f"consent: refusing recursive wrapper target for {surface!r}: {original}",
            file=sys.stderr,
        )
        return REFUSED
    source_state = None
    if "rack.transition" in config.policy:
        # The registry reads the verb here too: a local parse would be the third
        # grammar in this file, which is the defect HATS-1816 removed.
        reading = operations.read("rack.transition", surface, argv)
        if reading is not None:
            task_id = reading.subject
            if _source_required(config.policy["rack.transition"], reading.target):
                try:
                    source_state = resolve_transition_source(original, task_id, argv, env)
                except ConsentPolicyError as exc:
                    print(f"consent: {exc}", file=sys.stderr)
                    return REFUSED
    matched = match_operation(surface, argv, config.policy, source_state=source_state)
    if matched is None:
        return spawn([original, *argv], env)

    answer = check_grant(matched.operation, config.project_dir)
    ticket = False
    legacy_flag = next((flag for flag in matched.legacy_flags if env.get(flag) == "1"), None)
    if answer.outcome is not Outcome.GRANTED:
        if legacy_flag is None:
            ticket = peek_ticket(matched.ticket_subject, argv)
            if not ticket:
                detail = answer.reason or "no live authorization covers this operation"
                print(
                    f"consent: requires supervisor approval; {detail}. Human: run "
                    f"`consent {matched.operation.type} {DEFAULT_WINDOW_MINUTES}` in this session",
                    file=sys.stderr,
                )
                return REFUSED

    child_env = dict(env)
    for flag in (*matched.legacy_flags, _TICKET_ENV, REQUEST_ENV):
        child_env.pop(flag, None)
    if ticket and not consume_ticket(matched.ticket_subject, argv):
        print("consent: authorization ticket could not be consumed", file=sys.stderr)
        return REFUSED
    recorded = True
    if answer.outcome is Outcome.GRANTED:
        recorded = record_grant(answer, matched.operation, config.project_dir)
    elif legacy_flag is not None:
        recorded = record_legacy(legacy_flag, matched.operation, config.project_dir)
    elif ticket and env.get(REQUEST_ENV):
        recorded = record_request(env[REQUEST_ENV], matched.operation, config.project_dir)
    if not recorded:
        print("consent: authorization use could not be recorded", file=sys.stderr)
        return REFUSED
    return spawn([original, *argv], child_env)


def _wrapper_script(surface: str) -> str:
    return (
        f"#!{sys.executable}\n"
        "from ai_hats.consent_wrapper import main\n"
        f"raise SystemExit(main({surface!r}))\n"
    )


def _consent_script() -> str:
    return (
        f"#!{sys.executable}\n"
        "from ai_hats_library.hooks.consent_gate.cli import main\n"
        "raise SystemExit(main())\n"
    )


def materialize_consent_wrappers(
    layout: ProjectLayout,
    result,
    session_id: str,
    provider,
    artifacts,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[..., str | None] = shutil.which,
) -> None:
    """Put role-declared command middleware first on this HITL session's PATH."""
    project_dir = layout.root
    policy = policy_from(result.consent)
    if not policy:
        return
    unknown = sorted(op for op in policy if operations.spec_for(op) is None)
    if unknown:
        raise RuntimeError(f"unsupported consent operations: {', '.join(unknown)}")
    if not provider.supports_session_command_wrappers():
        raise RuntimeError(
            f"provider {provider.name!r} cannot enforce role-declared command consent"
        )

    env = os.environ if environ is None else environ
    effective_path = artifacts.extra_env.get("PATH", env.get("PATH", ""))
    lookup_path = _original_lookup_path(effective_path)
    surfaces = operations.wrapped_surfaces(policy)
    originals: dict[str, str] = {}
    for surface in surfaces:
        original = which(surface, path=lookup_path)
        if not original:
            raise RuntimeError(f"cannot wrap {surface!r}: executable not found on PATH")
        if _is_consent_wrapper_path(original):
            raise RuntimeError(
                f"cannot wrap {surface!r}: resolved executable is a consent wrapper: {original}"
            )
        originals[surface] = original

    root = layout.cache.session(session_id) / _WRAPPER_DIR_NAME
    config_path = root / "config.json"
    artifacts.port.write_text(
        config_path,
        json.dumps(
            {
                "project_dir": str(project_dir.resolve()),
                "originals": originals,
                "policy": {key: list(value) for key, value in policy.items()},
            },
            indent=2,
        )
        + "\n",
    )
    bin_dir = root / _WRAPPER_BIN_NAME
    consent = bin_dir / "consent"
    artifacts.port.write_executable(consent, _consent_script())
    artifacts.materialized.append(consent)
    for surface in surfaces:
        wrapper = bin_dir / surface
        artifacts.port.write_executable(wrapper, _wrapper_script(surface))
        artifacts.materialized.append(wrapper)
    artifacts.materialized.append(config_path)
    artifacts.extra_env["PATH"] = os.pathsep.join(filter(None, (str(bin_dir), effective_path)))
    artifacts.extra_env[CONFIG_ENV] = str(config_path)

    from .consent_mcp.registration import register_server

    register_server(project_dir, policy, provider, artifacts)


def load_config(path: Path) -> WrapperConfig:
    """Load the strict, session-frozen wrapper configuration."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("consent wrapper config must be an object")
    project_dir = data.get("project_dir")
    originals = data.get("originals")
    policy = data.get("policy")
    if not isinstance(project_dir, str) or not project_dir:
        raise ValueError("consent wrapper config has no project_dir")
    if not isinstance(originals, dict) or not all(
        isinstance(key, str) and isinstance(value, str) and value
        for key, value in originals.items()
    ):
        raise ValueError("consent wrapper config has invalid originals")
    if not isinstance(policy, dict) or not all(
        isinstance(key, str)
        and isinstance(value, list)
        and all(isinstance(item, str) and item for item in value)
        for key, value in policy.items()
    ):
        raise ValueError("consent wrapper config has invalid policy")
    return WrapperConfig(
        project_dir=Path(project_dir),
        originals=dict(originals),
        policy={key: tuple(value) for key, value in policy.items()},
    )


def main(surface: str) -> int:
    """Run one materialized command wrapper."""
    config_path = os.environ.get(CONFIG_ENV)
    if not config_path:
        print(f"consent: {CONFIG_ENV} is missing", file=sys.stderr)
        return REFUSED
    try:
        config = load_config(Path(config_path))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"consent: wrapper configuration is unreadable: {exc}", file=sys.stderr)
        return REFUSED

    from ai_hats_library.hooks import consent_ticket

    from .consent_port import verdict

    argv = sys.argv[1:]
    return run_wrapped(
        surface,
        argv,
        config,
        check_grant=lambda operation, target: verdict(operation, target_dir=target),
        peek_ticket=lambda subject, command: consent_ticket.peek(
            subject, start=config.project_dir, argv=command
        ),
        consume_ticket=lambda subject, command: consent_ticket.consume(
            subject, start=config.project_dir, argv=command
        ),
    )


__all__ = [
    "CONFIG_ENV",
    "ConsentPolicyError",
    "MatchedOperation",
    "WrapperConfig",
    "load_config",
    "main",
    "match_operation",
    "materialize_consent_wrappers",
    "policy_from",
    "run_wrapped",
]
