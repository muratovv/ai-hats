"""`ai-hats --tree` — render the full command graph in man-style.

Each command shows its short summary, its full help (docstring), and its
options. Groups recurse into subcommands. Hidden commands are skipped.
"""

from __future__ import annotations

import sys

import click
from rich.console import Console
from rich.tree import Tree


def _make_metavar(p: click.Parameter, ctx: click.Context) -> str:
    """Wrap `Parameter.make_metavar` for click 8/9 compatibility.

    Click 9 requires a ctx argument; click 8 takes none. Fall back to the
    parameter's name when neither call shape works.
    """
    try:
        return p.make_metavar(ctx) or ""
    except TypeError:
        try:
            return p.make_metavar() or ""  # type: ignore[call-arg]
        except Exception:
            return (p.name or "").upper()


def _signature(cmd: click.Command, ctx: click.Context, name: str | None = None) -> str:
    """`name <ARG> [OPTIONS]`-style signature line."""
    parts = [name or cmd.name or ""]
    for p in cmd.params:
        if isinstance(p, click.Argument):
            metavar = _make_metavar(p, ctx).strip("[] ")
            metavar = metavar or (p.name or "").upper()
            if not metavar:
                continue
            parts.append(metavar if p.required else f"[{metavar}]")
    if any(isinstance(p, click.Option) and not _is_help_option(p) for p in cmd.params):
        parts.append("[OPTIONS]")
    return " ".join(parts)


def _is_help_option(opt: click.Option) -> bool:
    return "--help" in (opt.opts or [])


def _option_lines(cmd: click.Command, ctx: click.Context) -> list[str]:
    """One line per non-help option: `--long, -short  HELP`."""
    out: list[str] = []
    for p in cmd.params:
        if not isinstance(p, click.Option):
            continue
        if _is_help_option(p):
            continue
        flags = ", ".join(p.opts) if p.opts else (p.name or "")
        if p.metavar:
            flags = f"{flags} {p.metavar}"
        elif p.type and not isinstance(p.type, click.types.BoolParamType):
            metavar = _make_metavar(p, ctx)
            if metavar:
                flags = f"{flags} {metavar}"
        help_text = (p.help or "").strip()
        if help_text:
            out.append(f"[dim]{flags}[/dim] — {help_text}")
        else:
            out.append(f"[dim]{flags}[/dim]")
    return out


def _full_help(cmd: click.Command) -> str:
    """Return the command's full help body (docstring), trimmed."""
    text = (cmd.help or "").strip()
    if not text:
        text = (cmd.short_help or "").strip()
    return text


def _first_line(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            return stripped
    return ""


def _body_after_first_line(text: str) -> list[str]:
    """Return non-empty lines from `text`, skipping the first non-empty line."""
    lines = text.splitlines()
    seen_first = False
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if not seen_first:
            seen_first = True
            continue
        out.append(stripped)
    return out


def _attach(node: Tree, cmd: click.Command, ctx: click.Context) -> None:
    """Attach `cmd` (and its subcommands, if a Group) under `node`."""
    if getattr(cmd, "hidden", False):
        return
    sub_ctx = click.Context(cmd, info_name=cmd.name or "", parent=ctx)
    body = _full_help(cmd)
    headline = _first_line(body)
    sig = _signature(cmd, sub_ctx)
    label = f"[bold cyan]{sig}[/bold cyan]"
    if headline:
        label = f"{label} — [italic]{headline}[/italic]"
    branch = node.add(label)

    for line in _body_after_first_line(body):
        branch.add(line)

    for line in _option_lines(cmd, sub_ctx):
        branch.add(line)

    if isinstance(cmd, click.Group):
        for name in sorted(cmd.commands):
            sub = cmd.commands[name]
            _attach(branch, sub, sub_ctx)


def _root_signature(cmd: click.Command, ctx: click.Context, name: str) -> str:
    """`name <ARG>`-style header for a tree's top node (no `[OPTIONS]`,
    since options are rendered as child rows below)."""
    parts = [name]
    for p in cmd.params:
        if isinstance(p, click.Argument):
            metavar = _make_metavar(p, ctx).strip("[] ")
            metavar = metavar or (p.name or "").upper()
            if not metavar:
                continue
            parts.append(metavar if p.required else f"[{metavar}]")
    return " ".join(parts)


def _render_as_root(cmd: click.Command, display_name: str, console: Console) -> None:
    """Render `cmd` as if it were the top of the tree, labeled `display_name`."""
    ctx = click.Context(cmd, info_name=display_name)
    body = _full_help(cmd)
    headline = _first_line(body)
    # Avoid duplicating the program name when the docstring already opens
    # with it (e.g. `ai-hats — AI agent ...`).
    lname = display_name.lower()
    if headline.lower().startswith(f"{lname} — "):
        headline = headline[len(display_name) + 3:].lstrip()
    elif headline.lower().startswith(lname):
        headline = headline[len(display_name):].lstrip(" —-")

    sig = _root_signature(cmd, ctx, display_name)
    label = f"[bold]{sig}[/bold]"
    if headline:
        label = f"{label} — [italic]{headline}[/italic]"
    tree = Tree(label)

    for line in _body_after_first_line(body):
        tree.add(line)

    for line in _option_lines(cmd, ctx):
        tree.add(line)

    if isinstance(cmd, click.Group):
        for child_name in sorted(cmd.commands):
            _attach(tree, cmd.commands[child_name], ctx)

    console.print(tree)


def print_full_tree(root: click.Command, console: Console) -> None:
    """Render the entire command tree under `root` to `console`."""
    _render_as_root(root, "ai-hats", console)


def print_subtree(root: click.Command, path: list[str], console: Console) -> None:
    """Render the subtree rooted at `path` (sequence of command names).

    Empty `path` is equivalent to `print_full_tree`. If a token cannot be
    resolved, prints an error to `console` and exits with code 2.
    """
    if not path:
        print_full_tree(root, console)
        return

    cmd: click.Command = root
    walked: list[str] = []
    for token in path:
        is_group = isinstance(cmd, click.Group)
        available = sorted(cmd.commands) if is_group else []
        if not is_group or token not in available:
            scope = " ".join(walked) if walked else "ai-hats"
            msg = f"[red]ai-hats --tree:[/red] unknown subcommand '{token}' under '{scope}'."
            if available:
                msg += f"\n  Available: {', '.join(available)}"
            console.print(msg)
            sys.exit(2)
        cmd = cmd.commands[token]  # type: ignore[union-attr]
        walked.append(token)

    display_name = "ai-hats " + " ".join(walked)
    _render_as_root(cmd, display_name, console)
