"""Checks against the files on disk: do the references resolve, and do the
addresses land inside them.

These are the findings the JSON alone cannot give, and they are the ones that
bite: a block past the end of its ROM is a well-formed project that opens on an
empty list. Paths resolve as mapchar resolves them — relative to the project
file, case-insensitively — so a project written on Windows is not reported as
broken under WSL.

An address is into a file's **payload**, what its container leaves once a
header is stripped, so the payload is never longer than the file: an address
past the end of the file is past the end of any payload. A file or block with
its own compression is addressed in the decompressed bytes, whose length only
the codec knows, so its addresses are not measured here.
"""

from __future__ import annotations

from mapchar_lint.context import Context, EntryView, parent_of, region_size
from mapchar_lint.reading import int_reading


def check(ctx: Context) -> None:
    if not ctx.check_files:
        return
    for view in ctx.entries:
        if view.kind is None or view.dropped:
            continue
        _references(ctx, view)
    _duplicates(ctx)
    for view in ctx.entries:
        if view.kind in ("block", "bookmark") and not view.dropped:
            _bounds(ctx, view)


def _references(ctx: Context, view: EntryView) -> None:
    # A row under a file names the file's path again; the file entry is where
    # a missing one is reported.
    if view.kind not in ("file", "table"):
        return
    path = view.raw.get("path")
    if isinstance(path, str) and path:
        _one(ctx, view, path, view.at("path"))
    extra = view.raw.get("extra_paths")
    if isinstance(extra, list):
        for at, item in enumerate(extra):
            if isinstance(item, str) and item:
                _one(ctx, view, item, view.at("extra_paths", at))


def _one(ctx: Context, view: EntryView, stored: str, pointer: str) -> None:
    if not ctx.doc.exists(stored):
        detail = (
            "The table reads as missing, and every block reading through it "
            "reads nothing until it is found."
            if view.kind == "table"
            else "The entry stays listed; mapchar offers to locate it, and its "
            "blocks read nothing until it is found."
        )
        ctx.error(
            "E301",
            f"{stored!r} does not exist",
            pointer=pointer,
            entry=view,
            detail=detail,
        )
    elif ctx.doc.is_dir(stored):
        ctx.error(
            "E302", f"{stored!r} is a folder, not a file", pointer=pointer, entry=view
        )
    elif ctx.doc.size_of(stored) == 0:
        ctx.warn(
            "W303",
            f"{stored!r} is empty",
            pointer=pointer,
            entry=view,
            detail="There are no bytes to read.",
        )


def _duplicates(ctx: Context) -> None:
    """Two file entries over one file: each holds its own edits to the same
    bytes, and the last one written wins."""
    seen: dict[str, EntryView] = {}
    for view in ctx.entries:
        path = view.raw.get("path")
        if view.kind != "file" or view.dropped or not (isinstance(path, str) and path):
            continue
        if not ctx.doc.exists(path):
            continue
        key = ctx.doc.identity(path)
        first = seen.setdefault(key, view)
        if first is not view:
            ctx.warn(
                "W305",
                f"{path!r} is also opened by entries[{first.index}]",
                pointer=view.at("path"),
                entry=view,
                detail="Two file entries over one file each keep their own edits to "
                "its bytes, and whichever is written last wins.",
            )


def _bounds(ctx: Context, view: EntryView) -> None:
    parent = parent_of(ctx.entries, view)
    if parent is None or parent.kind != "file" or parent.dropped:
        return
    if parent.raw.get("compression_id"):
        return
    size = region_size(ctx.doc, parent)
    if size is None:
        return
    if view.kind == "bookmark":
        reads, offset = int_reading(view.raw.get("offset", 0))
        if reads and not 0 <= offset <= size:
            ctx.error(
                "E312",
                f"offset ${offset:X} is past the end of the file (${size:X} bytes)",
                pointer=view.at("offset"),
                entry=view,
                detail="The bookmark opens on nothing.",
            )
        return
    if view.raw.get("compression_id"):
        _slice(ctx, view, size)
        return
    reading = view.config
    if reading is None or reading.fatal:
        return
    for word, address in reading.addresses:
        if not 0 <= address <= size:
            ctx.error(
                "E311",
                f"{word}=${address:X} is past the end of the file (${size:X} bytes)",
                pointer=view.at("config"),
                entry=view,
                detail="Nothing is there to read: the strings it would reach come "
                "back empty or cut short.",
            )


def _slice(ctx: Context, view: EntryView, size: int) -> None:
    reads, offset = int_reading(view.raw.get("slice_offset", 0))
    if not reads:
        return
    if not 0 <= offset < size:
        ctx.error(
            "E313",
            f"slice_offset ${offset:X} is past the end of the file (${size:X} bytes)",
            pointer=view.at("slice_offset"),
            entry=view,
            detail="There is nothing there to decompress.",
        )
        return
    reads, length = int_reading(view.raw.get("slice_length") or 0)
    if reads and length and offset + length > size:
        ctx.error(
            "E313",
            f"slice_offset + slice_length ends at ${offset + length:X}, past the end "
            f"of the file (${size:X} bytes)",
            pointer=view.at("slice_length"),
            entry=view,
            detail="A write-back would be bounded by room the file does not have.",
        )
