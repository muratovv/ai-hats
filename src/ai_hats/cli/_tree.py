"""`ai-hats --tree` — render the full command graph in man-style.

Each command shows its short summary, its full help (docstring), and its
options. Groups recurse into subcommands. Hidden commands are skipped.
"""

from __future__ import annotations

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


def print_full_tree(root: click.Command, console: Console) -> None:
    """Render the entire command tree under `root` to `console`."""
    name = "ai-hats"
    ctx = click.Context(root, info_name=name)
    body = _full_help(root)
    headline = _first_line(body)
    # Avoid `ai-hats — ai-hats — ...` when the docstring already opens with
    # the program name.
    if headline.lower().startswith(f"{name} — "):
        headline = headline[len(name) + 3:].lstrip()
    elif headline.lower().startswith(name):
        headline = headline[len(name):].lstrip(" —-")
    label = f"[bold]{name}[/bold]"
    if headline:
        label = f"{label} — [italic]{headline}[/italic]"
    tree = Tree(label)

    for line in _body_after_first_line(body):
        tree.add(line)

    for line in _option_lines(root, ctx):
        tree.add(line)

    if isinstance(root, click.Group):
        for name in sorted(root.commands):
            _attach(tree, root.commands[name], ctx)

    console.print(tree)
