"""A table entry's own records: the in-app edits it carries over its file, and
the ``@include`` list it gives in place of the file's."""

from __future__ import annotations

import re

from mapchar_lint.context import Context, EntryView

_BITS = re.compile(r"^[01]+$")


def check(ctx: Context) -> None:
    for view in ctx.entries:
        if view.kind == "table" and not view.dropped:
            _overlay(ctx, view)
            _includes(ctx, view)
            _pathless(ctx, view)


def _overlay(ctx: Context, view: EntryView) -> None:
    overlay = view.raw.get("overlay")
    if overlay is None:
        return
    if not isinstance(overlay, dict):
        ctx.error(
            "E701",
            "overlay is not an object of entry keys",
            pointer=view.at("overlay"),
            entry=view,
            detail="Ignored: the table's in-app edits are lost, and the next save "
            "writes none.",
        )
        return
    for key, line in overlay.items():
        pointer = view.at("overlay", key)
        if not _BITS.match(key):
            ctx.error(
                "E702",
                f"overlay key {key!r} is not an entry's bits (0s and 1s)",
                pointer=pointer,
                entry=view,
                detail="No entry has that key, so the edit lands on nothing.",
            )
        if line is not None and not isinstance(line, str):
            ctx.warn(
                "W703",
                f"overlay value for {key} is {line!r}, not a table line or null",
                pointer=pointer,
                entry=view,
                detail=f"It reads as the line {str(line)!r}.",
            )


def _includes(ctx: Context, view: EntryView) -> None:
    includes = view.raw.get("includes")
    if includes is None:
        return
    if not isinstance(includes, list):
        ctx.error(
            "E704",
            "includes is not a list of table ids",
            pointer=view.at("includes"),
            entry=view,
            detail="Ignored: the file's own @include lines apply.",
        )
        return
    if view.raw.get("table") is not None and str(view.raw["table"]) in map(
        str, includes
    ):
        ctx.error(
            "E705",
            "the table includes itself",
            pointer=view.at("includes"),
            entry=view,
            detail="A table cannot start from itself; it does not build.",
        )


def _pathless(ctx: Context, view: EntryView) -> None:
    if view.raw.get("path"):
        return
    if not view.raw.get("overlay"):
        ctx.warn(
            "W706",
            "a table with no file and no overlay is empty",
            pointer=view.pointer,
            entry=view,
            detail="Everything a table with no file holds is its overlay.",
        )
