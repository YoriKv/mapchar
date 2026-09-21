"""A block's own records: its configuration line, the strings it saved, its
text box, and the slot of a block with its own compression.

A file's ``session.config`` is the same grammar, but it is only a view setting:
a line that does not read costs the setting and never the entry, so what drops
a block is a warning there.
"""

from __future__ import annotations

from mapchar_lint.config import read_config
from mapchar_lint.context import Context, EntryView
from mapchar_lint.diagnostics import Severity
from mapchar_lint.reading import int_reading
from mapchar_lint.schema import (
    BOX_KEYS,
    DEFAULT_SPARE_ROOM,
    EFFECTS,
    ROOM_SOURCES,
    SPARE_ROOM,
    STATUSES,
    STRING_KEYS,
)

_LEVEL = {"error": Severity.ERROR, "warning": Severity.WARNING, "info": Severity.INFO}


def check(ctx: Context) -> None:
    for view in ctx.entries:
        if view.kind is None:
            continue
        if view.kind == "block":
            _config(ctx, view)
            if not view.causes:
                _strings(ctx, view)
                _box(ctx, view)
                _slice(ctx, view)
                _room(ctx, view)
        else:
            _session_config(ctx, view)


def _config(ctx: Context, view: EntryView) -> None:
    reading = view.config
    if reading is None:
        ctx.warn(
            "W601",
            "the block has no config",
            pointer=view.pointer,
            entry=view,
            detail="Without a source and a table it reads nothing.",
        )
        return
    # The one finding that drops the block is already reported with the entry's
    # drop causes; everything else the line says is reported here.
    first_fatal = next(
        (f for f in reading.findings if reading.fatal and f.severity == "error"), None
    )
    for finding in reading.findings:
        if finding is first_fatal:
            continue
        ctx.emit(
            finding.code,
            _LEVEL[finding.severity],
            finding.message,
            pointer=view.at("config"),
            entry=view,
            detail=finding.detail,
        )


def _session_config(ctx: Context, view: EntryView) -> None:
    session = view.raw.get("session")
    if not isinstance(session, dict):
        return
    spec = session.get("config")
    if not isinstance(spec, str) or not spec:
        return
    reading = read_config(spec)
    for finding in reading.findings:
        if finding.code in ("E620", "W619"):
            # A file's reading may leave its table to the Table list, and its
            # range is where New Block starts from, empty until one is picked:
            # the app writes `source=range start=$0 stop=$0` itself.
            continue
        severity = _LEVEL[finding.severity]
        detail = finding.detail
        if reading.fatal and finding.severity == "error":
            severity = Severity.WARNING
            detail = "The file's reading does not load; it opens on the default one."
        ctx.emit(
            finding.code,
            severity,
            f"session.config: {finding.message}",
            pointer=view.at("session", "config"),
            entry=view,
            detail=detail,
        )


def _strings(ctx: Context, view: EntryView) -> None:
    strings = view.raw.get("strings")
    if not isinstance(strings, list):
        for mark in ("fixed_ends_shown", "runs_joined"):
            if view.raw.get(mark):
                ctx.info(
                    "I645",
                    f"{mark} is set on a block with no saved strings",
                    pointer=view.at(mark),
                    entry=view,
                    detail="Ignored: the mark is about respelling saved strings.",
                )
        return
    seen: dict[int, int] = {}
    for at, record in enumerate(strings):
        if not isinstance(record, dict):
            continue  # a drop cause
        pointer = view.at("strings", at)
        if "i" not in record:
            ctx.error(
                "E641",
                "a string record needs `i`, its index in the block",
                pointer=pointer,
                entry=view,
                detail="The record is skipped: its original and notes are lost at "
                "the next save.",
            )
            continue
        reads, index = int_reading(record["i"])
        if not reads:
            ctx.error(
                "E641",
                f"i is {record['i']!r}, not an index",
                pointer=f"{pointer}/i",
                entry=view,
                detail="The record is skipped: its original and notes are lost at "
                "the next save.",
            )
            continue
        status = record.get("s", "untouched")
        if status not in STATUSES:
            ctx.error(
                "E642",
                f"status {status!r} is not one of {', '.join(STATUSES)}",
                pointer=f"{pointer}/s",
                entry=view,
                detail="The record is skipped: its original and notes are lost at "
                "the next save.",
            )
            continue
        if index in seen:
            ctx.warn(
                "W643",
                f"string {index} is also saved at strings[{seen[index]}]",
                pointer=pointer,
                entry=view,
                detail="The later record replaces the earlier one.",
            )
        seen[index] = at
        for key in record:
            if key not in STRING_KEYS:
                ctx.error(
                    "E646",
                    f"`{key}` is not a string-record key (i, o, h, t, s, n, u)",
                    pointer=f"{pointer}/{key}",
                    entry=view,
                    detail="Ignored, and dropped by the next save.",
                )
        if "t" in record:
            ctx.info(
                "I644",
                f"string {index} carries a translation (`t`)",
                pointer=f"{pointer}/t",
                entry=view,
                detail="An older project's form: the first extraction writes it into "
                "the ROM's bytes, and saves stop carrying it.",
            )


def _box(ctx: Context, view: EntryView) -> None:
    box = view.raw.get("box")
    if box is None:
        return
    if not isinstance(box, dict):
        ctx.error(
            "E647",
            "box is not an object",
            pointer=view.at("box"),
            entry=view,
            detail="Ignored: the block previews in the default box.",
        )
        return
    for key in box:
        if key not in BOX_KEYS:
            ctx.error(
                "E648",
                f"`box.{key}` is not a text-box key",
                pointer=view.at("box", key),
                entry=view,
                detail="Ignored, and dropped by the next save.",
            )
    effects = box.get("effects")
    if not isinstance(effects, dict):
        return
    for label, pair in effects.items():
        pointer = view.at("box", "effects", label)
        if (
            not isinstance(pair, list)
            or len(pair) < 2
            or pair[0] not in EFFECTS
            or not int_reading(pair[1])[0]
        ):
            ctx.warn(
                "W649",
                f"the effect for [{label}] is not [effect, value]"
                f" (effects: {', '.join(EFFECTS)})",
                pointer=pointer,
                entry=view,
                detail="Skipped: the code has no effect in the preview.",
            )


def _room(ctx: Context, view: EntryView) -> None:
    """``room`` is where the block's text ended before a write shortened it:
    an address, and one only a pointer block ever records. Beside a ``bound``
    it is merely unused — a bound set after a shortening leaves both."""
    if "room" not in view.raw:
        return
    room = view.raw["room"]
    if not int_reading(room)[0]:
        ctx.warn(
            "W652",
            f"room is {room!r}, which is not an address",
            pointer=view.at("room"),
            entry=view,
            detail="Read as none: the block's bound is then where its text ends.",
        )
        return
    reading = view.config
    if reading is None or reading.fatal:
        return
    if reading.source not in ROOM_SOURCES:
        ctx.warn(
            "W653",
            "`room` is only read for a pointer table or a pointer list",
            pointer=view.at("room"),
            entry=view,
            detail="Kept, but this source says where the block's room ends.",
        )


def _slice(ctx: Context, view: EntryView) -> None:
    compressed = bool(view.raw.get("compression_id"))
    for key in ("slice_offset", "slice_length", "spare_room"):
        if key in view.raw and not compressed:
            ctx.warn(
                "W650",
                f"`{key}` is only kept for a block with its own compression_id",
                pointer=view.at(key),
                entry=view,
                detail="Read, but meaningless without one, and dropped by the next "
                "save.",
            )
    spare = view.raw.get("spare_room", DEFAULT_SPARE_ROOM)
    if spare not in SPARE_ROOM:
        ctx.warn(
            "W651",
            f"spare_room is {spare!r}, not {' or '.join(SPARE_ROOM)}",
            pointer=view.at("spare_room"),
            entry=view,
            detail="Anything but `keep` fills the room a shorter re-compression "
            "leaves.",
        )
