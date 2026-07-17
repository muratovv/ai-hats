"""Typed CLI error surface (HATS-1033).

Pins the two dispatch tables that replaced the isinstance / if-elif chains:
- every ``RackError`` subclass resolves to a handler (a new one fails CI);
- every op kind has an echo renderer;
- each handler still yields the byte-identical (code, details) it always did.
"""

from __future__ import annotations

import importlib
import json
import pkgutil
from pathlib import Path

import pytest
from click.testing import CliRunner

import ai_hats_rack
from ai_hats_rack import cli, ops
from ai_hats_rack.cli import main
from ai_hats_rack.cli_common import _ERROR_HANDLERS, lookup_error_handler
from ai_hats_rack.dispatch import OperationAborted
from ai_hats_rack.docstore import (
    DocumentNameError,
    FrozenDocumentError,
    FrozenPinDriftError,
    UnknownDocumentError,
)
from ai_hats_rack.errors import RackError
from ai_hats_rack.extensions.sections import SectionCatalogError
from ai_hats_rack.fsm import InvalidTransitionError, TopologyError, UnknownStateError
from ai_hats_rack.kernel import (
    ForceRequiresReasonError,
    LockTimeoutError,
    TaskExistsError,
    UnknownTaskError,
)
from ai_hats_rack.linked import SelfLinkError
from ai_hats_rack.ops import AttachSourceError, OpParseError
from ai_hats_rack.registry import (
    DerivedLinkKindError,
    LinksRegistryError,
    UnknownLinkKindError,
)
from ai_hats_rack.resolver import NoProjectRootError


# ----- exhaustiveness pins ----------------------------------------------------


def _import_all_rack_modules() -> None:
    for info in pkgutil.walk_packages(ai_hats_rack.__path__, ai_hats_rack.__name__ + "."):
        importlib.import_module(info.name)


def _all_subclasses(base: type) -> set[type]:
    seen: set[type] = set()
    stack = list(base.__subclasses__())
    while stack:
        cls = stack.pop()
        if cls not in seen:
            seen.add(cls)
            stack.extend(cls.__subclasses__())
    return seen


def test_every_rack_error_subclass_has_a_handler():
    # Introspection over the WHOLE package: a new RackError with no reachable
    # handler must fail here rather than fall silently into the generic branch.
    _import_all_rack_modules()
    missing = sorted(
        cls.__qualname__
        for cls in _all_subclasses(RackError)
        if lookup_error_handler(cls) is None
    )
    assert not missing, f"RackError subclasses with no CLI handler: {missing}"


def test_error_table_has_no_stale_keys():
    assert all(issubclass(key, RackError) for key in _ERROR_HANDLERS)


def test_op_renderers_cover_op_kinds():
    assert set(cli._OP_RENDERERS) == ops.OP_KINDS


# ----- (code, details) byte-parity with the pre-refactor chain ----------------

_CASES = [
    (
        InvalidTransitionError("HATS-1", "plan", "done", ("execute",)),
        "invalid_transition",
        {"task_id": "HATS-1", "from_state": "plan", "to_state": "done", "legal_edges": ["execute"]},
    ),
    (UnknownStateError("nope", ("brainstorm", "plan")), "unknown_state",
     {"known_states": ["brainstorm", "plan"]}),
    (OperationAborted("edge:x", "sub", "because"), "aborted",
     {"subscriber": "sub", "reason": "because"}),
    (UnknownTaskError("HATS-9"), "unknown_task", {"task_id": "HATS-9"}),
    (TaskExistsError("HATS-9"), "task_exists", {"task_id": "HATS-9"}),
    (OpParseError("bad"), "invalid_ops", {}),
    (AttachSourceError("/x"), "attach_source", {"src": "/x"}),
    (DocumentNameError("../x", "escapes"), "invalid_document_name", {"name": "../x"}),
    (UnknownDocumentError("HATS-1", "plan.md"), "unknown_document",
     {"task_id": "HATS-1", "name": "plan.md"}),
    (FrozenDocumentError("HATS-1", "plan.md"), "frozen_document",
     {"task_id": "HATS-1", "name": "plan.md"}),
    (
        FrozenPinDriftError("HATS-1", "plan.md", "sha256:aa", "sha256:bb"),
        "frozen_pin_drift",
        {"task_id": "HATS-1", "name": "plan.md", "pinned_digest": "sha256:aa",
         "current_digest": "sha256:bb"},
    ),
    (SelfLinkError("HATS-1"), "self_link", {"task_id": "HATS-1"}),
    (UnknownLinkKindError("wat", ["blocks", "related"]), "unknown_link_kind",
     {"kind": "wat", "configured": ["blocks", "related"]}),
    (DerivedLinkKindError("children", "parent"), "derived_link_kind",
     {"kind": "children", "inverse": "parent"}),
    (NoProjectRootError(Path("/no/such/root")), "no_project_root", {}),
    (ForceRequiresReasonError(), "invalid_request", {}),
    (LockTimeoutError(Path("/no/such/lock"), "task lock", 30.0), "lock_timeout", {}),
    # RackConfigError subtree → one internal marker.
    (TopologyError("bad fsm"), "internal", {}),
    (LinksRegistryError("bad links"), "internal", {}),
    (SectionCatalogError("bad catalog"), "internal", {}),
]


@pytest.mark.parametrize("exc, code, details", _CASES, ids=lambda v: type(v).__name__)
def test_handler_yields_exact_code_and_details(exc, code, details):
    handler = lookup_error_handler(type(exc))
    assert handler is not None
    got_code, got_details = handler(exc)
    assert got_code == code
    assert got_details == details


# ----- CLI-level regression for the shared-handler wiring ---------------------


def _tasks(tmp_path):
    return ["--tasks-dir", str(tmp_path / "tasks")]


def test_value_error_maps_to_invalid_request(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["create", "t", *_tasks(tmp_path), "--json"])
    # empty op stream → kernel raises a plain ValueError (not a RackError).
    out = runner.invoke(main, ["transition", "HATS-001", *_tasks(tmp_path), "--json"])
    assert out.exit_code == 1
    assert json.loads(out.output)["error"]["code"] == "invalid_request"


def test_task_exists_maps_via_shared_handler(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["create", "t", "--id", "HATS-001", *_tasks(tmp_path), "--json"])
    dup = runner.invoke(main, ["create", "t2", "--id", "HATS-001", *_tasks(tmp_path), "--json"])
    assert dup.exit_code == 1
    assert json.loads(dup.output)["error"]["code"] == "task_exists"


def test_invalid_ops_maps_via_shared_handler(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["create", "t", *_tasks(tmp_path), "--json"])
    bad = runner.invoke(main, ["transition", "HATS-001", "--bogus", "x", *_tasks(tmp_path), "--json"])
    assert bad.exit_code == 1
    assert json.loads(bad.output)["error"]["code"] == "invalid_ops"


def test_echo_ops_plain_output_unchanged(tmp_path):
    runner = CliRunner()
    runner.invoke(main, ["create", "t", *_tasks(tmp_path)])
    out = runner.invoke(main, ["transition", "HATS-001", "plan", "--log", "did it", *_tasks(tmp_path)])
    assert out.exit_code == 0, out.output
    assert "Transitioned: HATS-001 brainstorm → plan" in out.output
    assert "Logged: did it" in out.output
