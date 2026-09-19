"""Entry shape: what drops an entry, keys no kind reads, values the reader
coerces, and the session every entry carries."""

from __future__ import annotations

import difflib

from mapchar_lint.context import Context, EntryView
from mapchar_lint.reading import int_reading
from mapchar_lint.schema import (
    ALL_ENTRY_KEYS,
    DEFAULT_VIEW,
    KEYS_BY_KIND,
    SESSION_KEYS,
    SESSION_KEYS_NOT_FOR,
    VIEWS,
)

_DROPPED = (
    "The reader drops the entry with one line in its notice; everything it held "
    "is gone at the next save."
)


def check(ctx: Context) -> None:
    for view in ctx.entries:
        for cause in view.causes:
            ctx.error(
                cause.code,
                cause.message,
                pointer=view.at(*cause.keys),
                entry=view,
                detail=" ".join(filter(None, (cause.detail, _DROPPED))),
            )
        if view.kind is None:
            continue
        _keys(ctx, view)
        _name_and_path(ctx, view)
        _coerced(ctx, view)
        _session(ctx, view)


def _keys(ctx: Context, view: EntryView) -> None:
    own = KEYS_BY_KIND[view.kind]
    for key in view.raw:
        if key in own:
            continue
        if key in ALL_ENTRY_KEYS:
            ctx.warn(
                "W215",
                f"`{key}` is not read for a {view.kind}",
                pointer=view.at(key),
                entry=view,
                detail="Ignored, and dropped by the next save.",
            )
            continue
        close = difflib.get_close_matches(key, sorted(own), n=1)
        ctx.error(
            "E214",
            f"`{key}` is not an entry key"
            + (f" — did you mean `{close[0]}`?" if close else ""),
            pointer=view.at(key),
            entry=view,
            detail="The reader passes over it, so what it says is not in the project, "
            "and the next save drops it.",
        )


def _name_and_path(ctx: Context, view: EntryView) -> None:
    name = view.raw.get("name")
    if name is not None and not isinstance(name, str):
        ctx.warn(
            "W216",
            f"name is {name!r}, not a string",
            pointer=view.at("name"),
            entry=view,
            detail=f"It reads as {str(name)!r}.",
        )
    path = view.raw.get("path")
    if view.kind == "file" and not path:
        ctx.error(
            "E217",
            "a file entry needs a path",
            pointer=view.at("path") if "path" in view.raw else view.pointer,
            entry=view,
            detail="It has no bytes to read, and every row under it reads none.",
        )
    extra = view.raw.get("extra_paths")
    if isinstance(extra, str):
        ctx.error(
            "E218",
            "extra_paths is a string, not a list",
            pointer=view.at("extra_paths"),
            entry=view,
            detail="Each of its characters is read as a path of its own.",
        )


def _coerced(ctx: Context, view: EntryView) -> None:
    """Integers the reader takes through ``int()`` without a word: ``3.7``
    reads as 3, ``true`` as 1, ``"12"`` as 12."""
    keys = {"block": ("slice_offset", "slice_length"), "bookmark": ("offset",)}
    for key in keys.get(view.kind, ()):
        if key in view.raw:
            _soft_int(ctx, view, view.raw[key], (key,))
    session = view.raw.get("session")
    if isinstance(session, dict) and "offset" in session:
        _soft_int(ctx, view, session["offset"], ("session", "offset"))
    if not view.is_child:
        return
    for key in ("parent", "folder"):
        value = view.raw.get(key)
        if isinstance(value, bool) or isinstance(value, float | str):
            # ``parent`` is taken when it is an int, which a boolean is;
            # ``folder`` refuses booleans.
            names_one = isinstance(value, bool) and key == "parent"
            ctx.error(
                "E219",
                f"{key} is {value!r}, not an index",
                pointer=view.at(key),
                entry=view,
                detail="true reads as 1, and names entries[1]."
                if names_one and value
                else "false reads as 0, and names entries[0]."
                if names_one
                else "Only a whole number is read as an index; this one is ignored.",
            )


def _soft_int(ctx: Context, view: EntryView, value: object, keys: tuple) -> None:
    reads, number = int_reading(value)
    if reads and not (isinstance(value, int) and not isinstance(value, bool)):
        ctx.warn(
            "W213",
            f"{'.'.join(keys)} is {value!r}, not a whole number",
            pointer=view.at(*keys),
            entry=view,
            detail=f"It reads as {number}.",
        )


def _session(ctx: Context, view: EntryView) -> None:
    session = view.raw.get("session")
    if not isinstance(session, dict):
        return
    for key in session:
        if key not in SESSION_KEYS:
            close = difflib.get_close_matches(key, SESSION_KEYS, n=1)
            ctx.error(
                "E261",
                f"`session.{key}` is not a session key"
                + (f" — did you mean `{close[0]}`?" if close else ""),
                pointer=view.at("session", key),
                entry=view,
                detail="Ignored, and dropped by the next save.",
            )
        elif view.kind in SESSION_KEYS_NOT_FOR.get(key, ()):
            ctx.info(
                "I262",
                f"`session.{key}` is not kept for a {view.kind}",
                pointer=view.at("session", key),
                entry=view,
                detail="A block's reading is its own config; the next save drops "
                "this one.",
            )
    view_name = session.get("view", DEFAULT_VIEW)
    if view_name not in VIEWS:
        ctx.warn(
            "W263",
            f"session.view is {view_name!r}, not {', '.join(VIEWS)}",
            pointer=view.at("session", "view"),
            entry=view,
            detail="The entry opens on the Hex tab.",
        )
    resolve = session.get("resolve_pointers")
    if resolve is not None and not isinstance(resolve, bool):
        ctx.warn(
            "W264",
            f"session.resolve_pointers is {resolve!r}, not true or false",
            pointer=view.at("session", "resolve_pointers"),
            entry=view,
            detail=f"It reads as {bool(resolve)}.",
        )
