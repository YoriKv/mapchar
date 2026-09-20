"""Finding the files a project references that are no longer where it left them,
and pointing its entries at where they are now."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

from mapchar.core.capabilities import EntryKind
from mapchar.project.entry import Entry, normalize_path

if TYPE_CHECKING:
    from mapchar.project.workspace import Workspace


def missing_paths(ws: Workspace) -> list[str]:
    """Every referenced path not on disk, de-duplicated, in list order.

    De-duplicated *before* the stat: a ROM carries its blocks and bookmarks, and
    every one of them names the same file, so one shared file is one worklist
    row — located once, corrected everywhere.
    """
    seen: set[str] = set()
    result: list[str] = []
    for entry in ws.entries:
        for path in entry.paths:
            key = normalize_path(path)
            if key in seen:
                continue
            seen.add(key)
            if not os.path.exists(path):
                result.append(path)
    return result


def relocate_path(ws: Workspace, old_path: str, new_path: str) -> list[Entry]:
    """Re-point every reference to ``old_path`` at ``new_path``; the entries
    touched.

    Rewrites an entry's ``path`` and any of its ``extra_paths`` naming the same
    file — so relocating a shared ROM fixes the file and the blocks and
    bookmarks under it together.
    Pure data: the caller re-reads whatever was affected.
    """
    key = normalize_path(old_path)
    old_name, new_name = os.path.basename(old_path), os.path.basename(new_path)
    touched: list[Entry] = []
    for entry in ws.entries:
        changed = entry.path is not None and normalize_path(entry.path) == key
        if changed:
            entry.path = new_path
            # A row named after its file follows the file; a name the user typed
            # is theirs and survives the move.
            if entry.name == old_name:
                entry.name = new_name
        moved_extra = tuple(
            new_path if normalize_path(p) == key else p for p in entry.extra_paths
        )
        if moved_extra != entry.extra_paths:
            entry.extra_paths = moved_extra
            changed = True
        if changed:
            touched.append(entry)
    return touched


def retarget_files(ws: Workspace, entry: Entry, paths: tuple[str, ...]) -> list[Entry]:
    """Re-point a file entry at ``paths``, carrying its children; the entries
    touched.

    The file list is the entry's identity as much as its contents: ``paths[0]``
    is the row in the Files panel, the key a block or bookmark is found by, and
    the file a write is attributed to. So the children move in the same step —
    their offsets are counted against the *join*, so one left on the old list
    would address something else. A row still named after its first file follows
    the new one, the same rule :func:`relocate_path` uses. Pure data: the caller
    drops the affected documents and reads them again.
    """
    if entry.kind is not EntryKind.FILE or not paths:
        return []
    first, *rest = paths
    named_after_file = bool(entry.path) and entry.name == os.path.basename(entry.path)
    # The children are found before the path moves: they are keyed by the one
    # that is about to change.
    touched = [entry, *ws.children(entry)]
    for moved in touched:
        moved.path = first
        moved.extra_paths = tuple(rest)
    if named_after_file:
        entry.name = os.path.basename(first)
    return touched
