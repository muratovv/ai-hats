"""The human rendering of a session record — never a second derivation.

The dry-run and ``role_materialization.json`` print one dict, so the table and
``--json`` cannot disagree. Env is reported by key name only: values carry
secrets and ``--json`` output ends up in fixtures.
"""

from __future__ import annotations

from pathlib import Path


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def consent_row(hook) -> dict:
    """One consent declaration as the guard reads it, from the plan's external
    hook (ADR-0036 D1): the operation is the hook's object, the selector its point.

    Public because it is the ONE writer of this shape: the guard on the other
    side reads fields, and a second place building them by hand is how the two
    drift until the question quietly stops being asked. The ends travel ALREADY
    PARSED — the guard is stdlib-only and cannot import the rack's parser.
    """
    path = (hook.object,) if hook.object is not None else ()
    return _consent_dict(hook.app, path, hook.at, hook.declared_by)


def _consent_dict(app: str, path: tuple[str, ...], selector: str, declared_by: str) -> dict:
    from .check_points import selector_ends

    source, target = selector_ends(app, selector, path)
    return {
        "app": app,
        "path": list(path),
        "selector": selector,
        "from": source,
        "to": target,
        "declared_by": declared_by,
    }


def _where(check: dict) -> str:
    """The app and point a row binds, as one column of the launch report."""
    return f"{check['app']}:{check.get('at') or '-'}"


def _which_script(check: dict) -> str:
    """``skill/script``; the script's own path where no composed skill holds it."""
    return f"{check['skill']}/{check['script']}" if check["skill"] else check["script"]


def _consent_where(consent: dict) -> str:
    """The address a consent row sits at. An empty path drops the column rather
    than rendering a bare separator (the ``rack doctor`` precedent)."""
    return ".".join([consent["app"], *consent["path"]])


def _consent_key(consent: dict) -> str:
    """What the guard reading this row keys on — never assumed to be the selector.

    Three grammars ride one list and each is found by a different field: rack
    rows match on the parsed ``to`` ALONE, so printing the selector for all
    three would read as a promise the rack half does not keep.
    """
    from .check_points import CONSENT_GATE_APP, WT_APP, point_owner

    # A parsed `to` is by construction a rack row: `selector_ends` answers
    # (None, None) for every other app, so this asks the data rather than
    # holding a second copy of the app name.
    if consent.get("to"):
        return f"entering {consent['to']!r}"
    # The owner, not the key the row stands under: every row
    # stands under the gate, so asking `app` alone answered "operation type" for
    # the wt point too.
    if point_owner(consent["app"], consent["path"]) == WT_APP:
        return "wt point"
    if consent["app"] == CONSENT_GATE_APP:
        return "operation type"
    return "-"  # declared under an app no guard of this build reads


def _under_any(path: Path, roots: list[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _render_composition(c: dict) -> list[str]:
    """The composition half by kind — the hooks and points the prompt never shows."""
    lines = [
        f"composition  {c['identity']}  digest={c['digest'][:12]}",
        "  prompt    "
        + ", ".join(f"{len(b['members'])} {b['name'] or '(prose)'}" for b in c["prompt"]["blocks"]),
        f"  skills    {len(c['skills'])}: " + " ".join(s["name"] for s in c["skills"]),
        "  hooks",
    ]
    hooks = c["hooks"]
    if not hooks["runtime"] and not hooks["external"]:
        lines.append("    (none)")
    lines += [
        f"    runtime   {h['at']}  {h['matcher']}  {h['run']['path']}" for h in hooks["runtime"]
    ]
    for h in hooks["external"]:
        where = h["app"] if h["object"] is None else f"{h['app']}.{h['object']}"
        run = "" if h["run"] is None else f"  {h['run']['path']}"
        policy = "" if h["on_error"] is None else f"  on_error={h['on_error']}"
        lines.append(f"    external  {where} {h['at']!r}{run}{policy}  by {h['declared_by']}")
    removed = [t for t in c["trace"] if t["removed_by"] is not None]
    if removed:
        lines.append("  removed")
        lines += [
            f"    {t['term']}  brought by {t['brought_by']}, removed by {t['removed_by']}"
            for t in removed
        ]
    return lines


def render_report(d: dict, *, full: bool = False, prompt_text: str | None = None) -> str:
    """The human rendering of a record — the same dict ``--json`` prints, so
    the two cannot disagree. Sections a record does not carry are not drawn."""
    lines = [
        f"role      {d['role']} -> {d['provider']}   run_mode={d['run_mode']}",
        "policy    " + " ".join(f"{k}={'on' if v else 'off'}" for k, v in d["policy"].items()),
    ]
    if d["cwd"]:
        lines.append(f"cwd       {d['cwd']}")
    # Both surfaces pass the whole role text as one argv token (claude's
    # system_prompt, agy's -p) — unreadable inline, verbatim in --json.
    shown = [
        t if len(t) <= 160 else f"{t[:80]}… <{_human_size(len(t.encode()))} total>"
        for t in d["launch"]
    ]
    lines += [
        "",
        "launch    " + " ".join(shown),
        "env       " + (", ".join(d["env_keys"]) or "(none)"),
    ]

    if d["prompt"]:
        lines += ["", f"prompt    {d['prompt']}"]
        if full:
            body = Path(d["prompt"])
            lines.append(
                prompt_text
                if prompt_text is not None
                else (body.read_text() if body.is_file() else "(not written)")
            )

    lines += ["", "materialized"]
    if not d["materialized"]:
        lines.append("  (nothing)")
    composed = [Path(s["path"]) for s in d.get("composition", {}).get("skills", ())]
    for e in d["materialized"]:
        line = f"  {e['kind']:<11} {e['target']}"
        if e["size"]:
            line += f"  {_human_size(e['size'])}"
        if e.get("files") is not None:
            line += f"  ({e['files']} files)"
        if e["source"]:
            # A source under no composed skill is a planning input from the
            # person's own environment — the reader must see it as such.
            line += f"  <- {e['source']}"
            if "composition" in d and not _under_any(Path(e["source"]), composed):
                line += "  (outside the composition)"
        if e.get("outcome") is not None:
            line += f"  [{e['outcome']}]"
        lines.append(line)

    if "checks" in d:
        lines += ["", "checks"]
        if not d["checks"]:
            lines.append("  (none bound)")
        for c in d["checks"]:
            lines.append(
                f"  {_where(c):<20} {_which_script(c)}"
                f"  on_error={c['on_error']}  by {c['declared_by']}"
            )
            # An armed gate is the quiet case; anything else is what the operator
            # came for, so only the unhappy branches get a second line.
            if c["runs_from"] is None:
                lines.append("    ! UNRESOLVED — this gate has no bytes to run (see notes)")
            elif not c["planned"]:
                lines.append(f"    ! {c['runs_from']} is NOT written by this launch")

    lines += ["", "consent"]
    # Printed even when empty: "this role asks about nothing" and "the
    # section did not render" are different facts, and only one is fine.
    if not d["consent"]:
        lines.append("  (none declared)")
    for c in d["consent"]:
        lines.append(
            f"  {_consent_where(c):<14} {c['selector']!r:<20}"
            f" -> {_consent_key(c):<22} by {c['declared_by']}"
        )

    if "composition" in d:
        lines += ["", *_render_composition(d["composition"])]

    if d["notes"]:
        lines.append("")
        lines += [f"note      {n}" for n in d["notes"]]

    return "\n".join(lines) + "\n"
