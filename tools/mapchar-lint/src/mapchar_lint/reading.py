"""What makes the reader drop an entry, restated from ``projectfile._entry_from``.

The reader wraps each entry in one ``try``: whatever raises inside it — a kind
that is not a kind, an ``int()`` of ``"$10"``, a ``split()`` of a number — drops
the entry with one line in the load notice, and everything that entry held
(a block's saved originals, a table's in-app edits) is gone at the next save.
These are the causes, found the way the reader would trip on them, so each can
be reported where it is rather than as "entry 7 dropped".

:func:`int_reading` is the other half: what ``int()`` makes of a value that
does *not* raise. ``int(3.7)`` is 3 and ``int(True)`` is 1, and the reader
takes both without a word.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from mapchar_lint.config import read_config
from mapchar_lint.schema import KINDS, RETIRED_KINDS


@dataclass(frozen=True)
class Cause:
    """One reason the reader drops an entry."""

    code: str
    #: The keys under the entry the cause is about, for its JSON Pointer.
    keys: tuple
    message: str
    detail: str = ""


def int_reading(value: object) -> tuple[bool, int | None]:
    """``(reads, value)``: whether ``int(value)`` succeeds, and to what."""
    if isinstance(value, bool):
        return True, int(value)
    if isinstance(value, int):
        return True, value
    if isinstance(value, float):
        return (True, int(value)) if math.isfinite(value) else (False, None)
    if isinstance(value, str):
        try:
            return True, int(value)
        except ValueError:
            return False, None
    return False, None


def drop_causes(raw: object) -> list[Cause]:
    """Why the reader would drop ``raw``, in the order it would find out; empty
    when it reads. Only the first cause is what the reader reports, but every
    one of them has to be fixed before the entry loads."""
    if not isinstance(raw, dict):
        return [Cause("E201", (), f"the entry is a JSON {_type(raw)}, not an object")]
    kind = raw.get("kind")
    if str(kind) in RETIRED_KINDS:
        return [
            Cause(
                "E203", ("kind",), f"kind {kind!r} is retired", RETIRED_KINDS[str(kind)]
            )
        ]
    if "kind" not in raw:
        return [Cause("E202", (), "the entry has no kind")]
    if str(kind) not in KINDS:
        return [
            Cause("E202", ("kind",), f"{kind!r} is not a kind ({', '.join(KINDS)})")
        ]
    kind = str(kind)
    causes: list[Cause] = []
    path = raw.get("path")
    if path is not None and not isinstance(path, str):
        causes.append(Cause("E204", ("path",), f"path is a JSON {_type(path)}"))
    # Each item is joined to the project's folder; one that is not a string
    # raises, and so does a value that cannot be iterated at all.
    extra = raw.get("extra_paths", [])
    try:
        items = list(extra)
    except TypeError:
        items = None
    if items is None or not all(isinstance(p, str) for p in items):
        causes.append(
            Cause("E205", ("extra_paths",), "extra_paths is not a list of paths")
        )
    session = raw.get("session", {})
    if session and not isinstance(session, dict):
        causes.append(
            Cause("E206", ("session",), f"session is a JSON {_type(session)}")
        )
        session = {}
    session = session or {}
    _integer(causes, session.get("offset", 0), ("session", "offset"))
    config = session.get("config")
    if config and not isinstance(config, str):
        causes.append(
            Cause(
                "E210",
                ("session", "config"),
                f"session.config is a JSON {_type(config)}, not a line",
            )
        )
    compression = raw.get("compression_id")
    if compression and not isinstance(compression, str):
        causes.append(
            Cause(
                "E209",
                ("compression_id",),
                f"compression_id is a JSON {_type(compression)}",
            )
        )
    if kind == "block":
        if raw.get("config"):
            reading = read_config(raw["config"])
            if reading.fatal:
                first = next(f for f in reading.findings if f.severity == "error")
                causes.append(Cause(first.code, ("config",), first.message))
        _integer(causes, raw.get("slice_offset", 0), ("slice_offset",))
        if raw.get("slice_length"):
            _integer(causes, raw["slice_length"], ("slice_length",))
        _box(causes, raw.get("box"))
        _strings(causes, raw.get("strings"))
    if kind == "bookmark":
        _integer(causes, raw.get("offset", 0), ("offset",))
    return causes


def _integer(causes: list, value: object, keys: tuple) -> None:
    if not int_reading(value)[0]:
        causes.append(
            Cause(
                "E207", keys, f"{'.'.join(keys)} is {value!r}, which is not an integer"
            )
        )


def _box(causes: list, box: object) -> None:
    if not isinstance(box, dict):
        return  # anything but an object is ignored
    for key in ("width", "height", "line_height", "letter_spacing", "lines_per_page"):
        if key in box:
            _integer(causes, box[key], ("box", key))
    if "chars_per_line" in box:
        _integer(causes, box["chars_per_line"], ("box", "chars_per_line"))
    effects = box.get("effects")
    if effects and not isinstance(effects, dict):
        causes.append(Cause("E212", ("box", "effects"), "box.effects is not an object"))
    origin = box.get("origin", [0, 0])
    try:
        pair = (origin[0], origin[1])
    except (TypeError, IndexError, KeyError):
        causes.append(
            Cause("E212", ("box", "origin"), "box.origin is not an [x, y] pair")
        )
        return
    for at, value in enumerate(pair):
        _integer(causes, value, ("box", "origin", at))


def _strings(causes: list, strings: object) -> None:
    """A record the reader indexes raises unless it is an object; an ``i`` it
    cannot ``int()`` only skips the record, unless it is not even a scalar."""
    if not strings:
        return
    # An object or a string iterates as strings, each of which raises when it
    # is indexed; anything else that is not a list does not iterate at all.
    if not isinstance(strings, list):
        causes.append(Cause("E211", ("strings",), "strings is not a list of records"))
        return
    for at, record in enumerate(strings):
        if not isinstance(record, dict):
            causes.append(
                Cause(
                    "E211", ("strings", at), f"string record is a JSON {_type(record)}"
                )
            )
        elif "i" in record and isinstance(record["i"], list | dict | None):
            # int() of these raises TypeError, which the record's own guard
            # (KeyError, ValueError) does not catch.
            causes.append(Cause("E211", ("strings", at, "i"), "i is not a number"))


def _type(value: object) -> str:
    return {
        dict: "object",
        list: "array",
        str: "string",
        bool: "boolean",
        int: "number",
        float: "number",
        type(None): "null",
    }.get(type(value), type(value).__name__)
