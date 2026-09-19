"""References between entries: a row's file and folder, the table a block reads
through, and the names two rows must not share.

``parent``, ``folder`` and ``current`` are positions in ``entries``, so
inserting or moving a record slides every reference past it onto its
neighbour. One that ends up out of range, or naming something it may not name,
is reported here; one that lands on another perfectly good file is
indistinguishable from the one that was meant, and opens quietly wrong. The
answer is upstream — generate the file in one pass, or rearrange in the app.
"""

from __future__ import annotations

from mapchar_lint.context import Context, EntryView, parent_of
from mapchar_lint.schema import NAMED_UNIQUELY
from mapchar_lint.tables import ID_PATTERN, table_ids


def resolve(ctx: Context) -> None:
    """Mark the rows the reader drops for want of a file to sit under — before
    any pass asks whether an entry loads."""
    for view in ctx.entries:
        if not view.is_child or view.causes:
            continue
        parent = parent_of(ctx.entries, view)
        view.orphaned = parent is None or bool(parent.causes)


def check(ctx: Context) -> None:
    for view in ctx.entries:
        if view.kind is None or view.causes:
            continue
        if view.is_child:
            _parent(ctx, view)
            if not view.orphaned:
                _folder(ctx, view)
    _names(ctx)
    _tables(ctx)


def _parent(ctx: Context, view: EntryView) -> None:
    raw = view.raw.get("parent")
    dropped = "The row is dropped: it has no file to belong to."
    if raw is None:
        ctx.error(
            "E501",
            f"a {view.kind} needs `parent`, the index of its file",
            pointer=view.pointer,
            entry=view,
            detail=dropped,
        )
        return
    if not isinstance(raw, int):
        return  # E219 has said what it reads as
    parent = parent_of(ctx.entries, view)
    if parent is None:
        ctx.error(
            "E502",
            f"parent is {raw}, but there are {len(ctx.entries)} entries",
            pointer=view.at("parent"),
            entry=view,
            detail=dropped,
        )
    elif parent.causes:
        ctx.error(
            "E502",
            f"parent names entries[{raw}], which does not load",
            pointer=view.at("parent"),
            entry=view,
            detail=dropped,
        )
    elif parent.kind != "file":
        ctx.error(
            "E503",
            f"parent names a {parent.kind}, not a file",
            pointer=view.at("parent"),
            entry=view,
            detail="The row is kept under something that has no bytes of its own, "
            "and reads nothing.",
        )


def _folder(ctx: Context, view: EntryView) -> None:
    raw = view.raw.get("folder")
    if raw is None or isinstance(raw, bool) or not isinstance(raw, int):
        return
    shown = "The row is shown directly under its file instead."
    pointer = view.at("folder")
    if not 0 <= raw < len(ctx.entries):
        ctx.warn(
            "W511",
            f"folder is {raw}, but there are {len(ctx.entries)} entries",
            pointer=pointer,
            entry=view,
            detail=shown,
        )
        return
    folder = ctx.entries[raw]
    if folder.dropped:
        ctx.warn(
            "W511",
            f"folder names entries[{raw}], which does not load",
            pointer=pointer,
            entry=view,
            detail=shown,
        )
    elif folder.kind != "folder":
        ctx.warn(
            "W512",
            f"folder names a {folder.kind}, not a folder",
            pointer=pointer,
            entry=view,
            detail=shown,
        )
    elif folder.raw.get("parent") != view.raw.get("parent"):
        ctx.warn(
            "W513",
            f"folder names entries[{raw}], a folder of another file",
            pointer=pointer,
            entry=view,
            detail=shown,
        )
    elif _loops(ctx, view):
        ctx.warn(
            "W514",
            "the folders this row sits in lead back to it",
            pointer=pointer,
            entry=view,
            detail=shown,
        )


def _loops(ctx: Context, view: EntryView) -> bool:
    seen = {view.index}
    at = view.raw.get("folder")
    while isinstance(at, int) and not isinstance(at, bool):
        if at in seen:
            return True
        if not 0 <= at < len(ctx.entries):
            return False
        seen.add(at)
        at = ctx.entries[at].raw.get("folder")
    return False


def _names(ctx: Context) -> None:
    """Blocks and bookmarks are addressed by name — by a dump, a translator
    file, an Atlas script — so the reader numbers a repeated one apart."""
    taken: dict[str, EntryView] = {}
    for view in ctx.entries:
        if view.dropped or view.kind is None or view.kind == "folder":
            continue
        name = view.raw.get("name")
        name = str(name) if name is not None else view.name
        first = taken.get(name)
        if first is not None and view.kind in NAMED_UNIQUELY:
            ctx.warn(
                "W521",
                f"the name {name!r} is taken by entries[{first.index}]",
                pointer=view.at("name"),
                entry=view,
                detail="The reader renames this row so the two can be told apart; a "
                "dump or translator file naming it by the old name finds the other.",
            )
        taken.setdefault(name, view)


def _tables(ctx: Context) -> None:
    """Every table id a block, a session or an include names has to be one a
    table entry gives — or a charset, each of which is a table too."""
    provided: dict[str, EntryView] = {}
    complete = True
    for view in ctx.entries:
        if view.kind != "table" or view.dropped:
            continue
        found = table_ids(ctx.doc, view.raw) if ctx.check_files else None
        if found is None or not found.known:
            complete = False
            continue
        for table_id in found.ids:
            if table_id in provided and provided[table_id] is not view:
                ctx.warn(
                    "W522",
                    f"table id {table_id!r} is also given by "
                    f"entries[{provided[table_id].index}]",
                    pointer=view.pointer,
                    entry=view,
                    detail="Only one table answers to an id: blocks naming it read the "
                    "one listed last.",
                )
            provided[table_id] = view
        override = view.raw.get("table")
        if override is not None and not ID_PATTERN.fullmatch(str(override)):
            ctx.error(
                "E523",
                f"table {override!r} is not a table id (letters, digits, _ . -)",
                pointer=view.at("table"),
                entry=view,
                detail="The table cannot take that id.",
            )
    charsets = ctx.ids.plugins.get("charsets", set()) | ctx.ids.local.get(
        "charsets", set()
    )
    known = set(provided) | (charsets - {"none"})

    def unknown(table_id: str) -> bool:
        return table_id not in known

    for view in ctx.entries:
        if view.dropped or view.kind is None:
            continue
        if view.kind == "block" and view.config is not None and view.config.table:
            if unknown(view.config.table):
                _missing(ctx, view, view.config.table, view.at("config"), complete)
        session = view.raw.get("session")
        if isinstance(session, dict) and isinstance(session.get("table_id"), str):
            table_id = session["table_id"]
            if table_id and unknown(table_id) and complete:
                ctx.warn(
                    "W525",
                    f"session.table_id {table_id!r} names no table",
                    pointer=view.at("session", "table_id"),
                    entry=view,
                    detail="The entry opens reading through no table.",
                )
        includes = view.raw.get("includes") if view.kind == "table" else None
        if isinstance(includes, list):
            for at, table_id in enumerate(includes):
                if unknown(str(table_id)) and complete:
                    ctx.error(
                        "E526",
                        f"includes {table_id!r}, which names no table",
                        pointer=view.at("includes", at),
                        entry=view,
                        detail="A table whose include is missing does not build, and "
                        "every block reading through it reads nothing.",
                    )


def _missing(
    ctx: Context, view: EntryView, table_id: str, pointer: str, complete: bool
) -> None:
    if complete:
        ctx.error(
            "E524",
            f"table={table_id} names no table in the project",
            pointer=pointer,
            entry=view,
            detail="No table entry gives that id and no charset has it: the block "
            "reads nothing.",
        )
    else:
        ctx.warn(
            "W524",
            f"table={table_id} names no table this run could read",
            pointer=pointer,
            entry=view,
            detail="Some table files are missing or were not read, so the id may be "
            "one of theirs.",
        )
