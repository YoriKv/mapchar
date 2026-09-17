"""The ``.mapchar`` project file: JSON, references and settings, never bytes."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from mapchar.core.block import FixedLength, Status
from mapchar.core.errors import MapcharError
from mapchar.core.font import CodeEffect, Effect, TextBox
from mapchar.core.table import Table
from mapchar.plugins.aliases import current_config_ids, current_id
from mapchar.project.formats.script import format_config, parse_config
from mapchar.project.formats.table_native import sanitize_id
from mapchar.project.glossary import GlossaryTerm, glossary_dicts, glossary_from
from mapchar.project.tables import adopt_table
from mapchar.project.workspace import (
    NAMED_UNIQUELY,
    Entry,
    EntryKind,
    EntrySession,
    StringState,
    free_name,
    tree_order,
)

PROJECT_VERSION = 2


class ProjectError(MapcharError):
    pass


@dataclass
class LoadedProject:
    entries: list[Entry]
    current: Entry | None
    warnings: list[str] = field(default_factory=list)
    version: int = PROJECT_VERSION
    """The version the file claims, after any migration walked it forward."""
    migrated_from: int | None = None
    """The version it was written at, when a migration ran; ``None`` otherwise."""
    glossary: list[GlossaryTerm] = field(default_factory=list)


# -- migrations ------------------------------------------------------------
#
# One entry per version bump, keyed by the version it *reads*: ``_MIGRATIONS[n]``
# takes a document written at version ``n`` and returns it at ``n + 1``. They run
# in sequence, so a file several versions old is walked forward a step at a time
# and no migration has to know about more than the bump it was written for.
#
# A migration rewrites only what a rename or a reshape moved. A defaulted key or
# a widened range is not its business: reading those tolerantly is already
# :func:`_entry_from`'s job, and doing it twice leaves two answers to maintain.


def _mark_fixed_ends(data: dict[str, Any]) -> dict[str, Any]:
    """1 → 2: a fixed-length string that stops at an end token no longer shows
    the end token, so the strings version 1 saved for such a block spell one
    today's reading does not. Respelling them takes the block's tables, so the
    block is marked and its first reading does it."""
    for raw in data.get("entries") or []:
        if (
            isinstance(raw, dict)
            and raw.get("kind") == "block"
            and raw.get("strings")
            and _stops_at_end(raw.get("config"))
        ):
            raw["fixed_ends_shown"] = True
    return data


def _stops_at_end(spec: Any) -> bool:
    try:
        config = parse_config(spec) if isinstance(spec, str) else None
    except (ValueError, KeyError):
        return False
    st = config.string_type if config is not None else None
    return isinstance(st, FixedLength) and st.stop_at_end


_MIGRATIONS: dict[int, Callable[[dict[str, Any]], dict[str, Any]]] = {
    1: _mark_fixed_ends
}


def _migrated(data: dict[str, Any]) -> tuple[dict[str, Any], int | None]:
    """``data`` walked forward to :data:`PROJECT_VERSION`, and where it started.

    The second element is the version the file claimed when it needed migrating,
    and ``None`` when it did not — which is also the answer for a file from the
    future, since there is nothing to walk it forward with. Each step is
    idempotent, so re-running one over an already-current document is harmless.
    """
    stated = data.get("version", 1)
    version = stated if isinstance(stated, int) and not isinstance(stated, bool) else 1
    started = version
    while (migration := _MIGRATIONS.get(version)) is not None:
        data = migration(data)
        version += 1
        data["version"] = version
    return data, started if version != started else None


def _rel(path: str | None, base: str | None) -> str | None:
    if path is None:
        return None
    if base is None:
        return path
    try:
        rel = os.path.relpath(path, base)
    except ValueError:  # another drive letter on Windows — keep it absolute
        rel = path
    return rel.replace(os.sep, "/")


def _abs(path: str | None, base: str | None) -> str | None:
    if path is None or base is None or os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(base, path))


def _recover_case(path: str) -> str:
    """Match a missing path case-insensitively, segment by segment."""
    if os.path.exists(path):
        return path
    head, parts = path, []
    while head and not os.path.exists(head):
        head, tail = os.path.split(head)
        if not tail:
            return path
        parts.append(tail)
    for part in reversed(parts):
        try:
            names = os.listdir(head or ".")
        except OSError:
            return path
        match = next((n for n in names if n.lower() == part.lower()), None)
        if match is None:
            return path
        head = os.path.join(head, match)
    return head


def _string_records(entry: Entry) -> list[dict[str, Any]]:
    """A block's strings as the file stores them: every one with its original,
    plus a status and notes where they are not the defaults.

    Read from the extracted document when there is one, and otherwise from
    :attr:`~mapchar.project.workspace.Entry.pending_strings` — the state a block
    that was loaded but never opened is still carrying. Without that fallback a
    save would write back only the blocks the user happened to look at, and drop
    the originals of every other one. A translation an older project was still
    holding goes out again as it came, until an extraction puts it in the ROM.
    """
    if entry.doc is not None and entry.doc.strings:
        states = [
            (rec.index, rec.original, rec.status, rec.notes, None)
            for rec in entry.doc.strings
        ]
    elif entry.pending_strings:
        states = [
            (i, st.original, st.status, st.notes, st.translation)
            for i, st in sorted(entry.pending_strings.items())
        ]
    else:
        entry.strings_cache = None
        return []
    kept = entry.strings_cache
    if kept is not None and kept[0] == states:
        # The same list object, not an equal one: what serialises it can then
        # tell by identity that its own text still stands
        # (:attr:`~mapchar.project.workspace.Entry.strings_cache`).
        return kept[1]
    records: list[dict[str, Any]] = []
    for index, original, status, notes, translation in states:
        s: dict[str, Any] = {"i": index}
        if original is not None:
            s["o"] = original
        if translation is not None:
            s["t"] = translation
        if status is not Status.UNTOUCHED:
            s["s"] = status.value
        if notes:
            s["n"] = notes
        records.append(s)
    entry.strings_cache = (states, records)
    return records


def entry_dict(entry: Entry, entries: list[Entry], base: str | None) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": entry.kind.value, "name": entry.name}
    if entry.path:
        d["path"] = _rel(entry.path, base)
    if entry.extra_paths:
        d["extra_paths"] = [_rel(p, base) for p in entry.extra_paths]
    if entry.kind is EntryKind.FILE:
        if entry.container_id != "raw":
            d["container_id"] = entry.container_id
        if entry.compression_id:
            d["compression_id"] = entry.compression_id
    if entry.is_child:
        d["parent"] = entries.index(entry.parent) if entry.parent in entries else None
        folder = entry.folder
        if folder is not None and folder in entries:
            d["folder"] = entries.index(folder)
    if entry.kind is EntryKind.BLOCK:
        if entry.compression_id:
            d["compression_id"] = entry.compression_id
            d["slice_offset"] = entry.slice_offset
            # Written on the same test the reader applies, so a length round
            # trips to itself: an absent key and a zero both mean "nobody
            # measured it", and only one of the two is worth writing down.
            if entry.slice_length:
                d["slice_length"] = entry.slice_length
            if entry.spare_room != "fill":
                d["spare_room"] = entry.spare_room
        if entry.config is not None:
            d["config"] = format_config(entry.config)
        strings = _string_records(entry)
        if strings:
            d["strings"] = strings
            if entry.fixed_ends_shown:
                d["fixed_ends_shown"] = True
    if entry.kind is EntryKind.BLOCK and entry.box is not None:
        b = entry.box
        d["box"] = {
            "width": b.width,
            "height": b.height,
            "line_height": b.line_height,
            "letter_spacing": b.letter_spacing,
            "lines_per_page": b.lines_per_page,
            "chars_per_line": b.chars_per_line,
            "origin": [b.origin_x, b.origin_y],
            "effects": {k: [v.effect.value, v.value] for k, v in b.effects.items()},
        }
    if entry.kind is EntryKind.BOOKMARK:
        d["offset"] = entry.bookmark_offset
    if entry.kind is EntryKind.TABLE:
        if entry.dialect:
            d["dialect"] = entry.dialect
        # The in-app edits, never the table file itself: a file another tool
        # reads keeps saying what it said until Save As File folds these in.
        if entry.table_charset:
            d["charset"] = entry.table_charset
        if entry.table_includes is not None:
            d["includes"] = list(entry.table_includes)
        if entry.table_overlay:
            d["overlay"] = dict(entry.table_overlay)
        # A table with no file is named nowhere else; one renamed in the app
        # is named here in place of its file's id. Taken from ``table_id``
        # first, so a rename survives a save made while the file is missing and
        # no table was ever read.
        if not entry.path or entry.table_id:
            table_id = entry.table_id or (entry.table.id if entry.table else None)
            if table_id:
                d["table"] = table_id
    session: dict[str, Any] = {}
    if entry.session.table_id:
        session["table_id"] = entry.session.table_id
    if entry.session.offset:
        session["offset"] = entry.session.offset
    if entry.session.view != "raw":
        session["view"] = entry.session.view
    if entry.session.config is not None and entry.kind is not EntryKind.BLOCK:
        session["config"] = format_config(entry.session.config)
    if entry.session.resolve_pointers:
        session["resolve_pointers"] = True
    if session:
        d["session"] = session
    return d


def project_dict(
    entries: list[Entry],
    current: Entry | None,
    base: str | None,
    glossary: Iterable[GlossaryTerm] = (),
) -> dict[str, Any]:
    d: dict[str, Any] = {"version": PROJECT_VERSION}
    if current in entries:
        d["current"] = entries.index(current)
    d["entries"] = [entry_dict(e, entries, base) for e in entries]
    terms = glossary_dicts(glossary)
    if terms:
        d["glossary"] = terms
    return d


def save_project(
    path: str,
    entries: list[Entry],
    current: Entry | None,
    glossary: Iterable[GlossaryTerm] = (),
) -> None:
    base = os.path.dirname(os.path.abspath(path))
    data = project_dict(entries, current, base, glossary)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


CLIPBOARD_KEY = "mapchar-entries"
"""What marks a clipboard payload as a set of mapChar entries."""


def _wire_folders(entries: list[Entry | None], raw: list[Any]) -> None:
    """Put each row in the folder its record names by index.

    A row whose folder is not a folder of the same file — a broken record, a
    hand edit, a loop — stands directly under its file instead.
    """
    for entry, item in zip(entries, raw, strict=True):
        at = (
            item.get("folder") if entry is not None and isinstance(item, dict) else None
        )
        if not isinstance(at, int) or isinstance(at, bool):
            continue
        folder = entries[at] if 0 <= at < len(entries) else None
        if (
            entry is not None
            and entry.is_child
            and folder is not None
            and folder.kind is EntryKind.FOLDER
            and folder.parent is entry.parent
        ):
            entry.folder = folder
    for entry in entries:
        if entry is None:
            continue
        seen = {id(entry)}
        folder = entry.folder
        while folder is not None:
            if id(folder) in seen:
                entry.folder = None
                break
            seen.add(id(folder))
            folder = folder.folder


def _unfold_orphans(kept: list[Entry]) -> None:
    """A row whose folder was dropped stands directly under its file."""
    present = {id(e) for e in kept}
    for entry in kept:
        if entry.folder is not None and id(entry.folder) not in present:
            entry.folder = None


def entries_payload(entries: list[Entry]) -> str:
    """``entries`` as clipboard text: the same records a project stores, with
    **absolute** paths, so a copy pastes into another window and another project.

    The parent and folder indices are taken within the copied list, so a block
    copied together with its ROM stays attached to that ROM and one copied with
    its folder stays in it, and one copied alone arrives loose for the paste to
    re-aim.
    """
    return json.dumps(
        {
            "version": PROJECT_VERSION,
            CLIPBOARD_KEY: [entry_dict(e, entries, None) for e in entries],
        },
        ensure_ascii=False,
    )


def entries_from_payload(text: str) -> list[Entry]:
    """The entries a clipboard payload carries, parents wired up among them.

    Anything that is not a mapChar entry payload — any other text on the
    clipboard — comes back as an empty list rather than as an error.
    """
    try:
        data = json.loads(text)
    except ValueError:
        return []
    raw = data.get(CLIPBOARD_KEY) if isinstance(data, dict) else None
    if not isinstance(raw, list):
        return []
    entries: list[Entry | None] = []
    parents: list[int | None] = []
    for item in raw:
        try:
            entry, parent_index = _entry_from(item, "")
        except Exception:  # noqa: BLE001 - a broken record is dropped, never fatal
            entries.append(None)
            parents.append(None)
            continue
        entries.append(entry)
        parents.append(parent_index)
    for entry, parent_index in zip(entries, parents, strict=True):
        if entry is not None and parent_index is not None:
            if 0 <= parent_index < len(entries):
                entry.parent = entries[parent_index]
    _wire_folders(entries, raw)
    kept = [e for e in entries if e is not None]
    _unfold_orphans(kept)
    return kept


def load_project(path: str) -> LoadedProject:
    try:
        # utf-8-sig: an editor that stamps a byte-order mark on the project
        # file must not make it unreadable.
        with open(path, encoding="utf-8-sig") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"cannot read project: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries", []), list):
        raise ProjectError("not a mapchar project")
    warnings: list[str] = []
    # Before anything reads an entry key: every reader below is written against
    # the current schema, so an older file is walked forward first and the rest
    # of this function never learns there was more than one spelling.
    data, migrated_from = _migrated(data)
    version = data.get("version", 1)
    if isinstance(version, int) and version > PROJECT_VERSION:
        warnings.append(
            f"project version {version} is newer than this build understands"
        )
    base = os.path.dirname(os.path.abspath(path))
    entries: list[Entry] = []
    raw_entries = data.get("entries", [])
    parents: list[int | None] = []
    for i, raw in enumerate(raw_entries):
        try:
            entry, parent_index = _entry_from(raw, base)
        except Exception as exc:  # noqa: BLE001 - a broken entry is dropped, never fatal
            warnings.append(f"entry {i} dropped: {exc}")
            entries.append(None)  # type: ignore[arg-type]
            parents.append(None)
            continue
        entries.append(entry)
        parents.append(parent_index)
    for entry, parent_index in zip(entries, parents, strict=True):
        if entry is not None and parent_index is not None:
            parent = entries[parent_index] if 0 <= parent_index < len(entries) else None
            entry.parent = parent
    _wire_folders(entries, raw_entries)
    kept = [e for e in entries if e is not None]
    current = None
    ci = data.get("current")
    # ``True`` is an int in Python but never an index a writer meant.
    if isinstance(ci, bool):
        ci = None
    if isinstance(ci, int) and 0 <= ci < len(entries) and entries[ci] is not None:
        current = entries[ci]
    if current is not None and current.kind is EntryKind.BOOKMARK:
        current = None  # a bookmark can never be shown; a hand-edited index degrades
    for e in kept:
        if e.is_child and e.parent is None:
            warnings.append(f"{e.name}: parent entry missing")
    kept = [e for e in kept if not (e.is_child and e.parent is None)]
    _unfold_orphans(kept)
    # Every row straight after what holds it, which a hand-edited file need not
    # have kept, and which adding and moving rows rely on.
    kept = tree_order(kept)
    # A file written by hand, or by a build that let names repeat: the rows
    # are numbered here so that everything that names a string by its block
    # (a dump, a translator file, an Atlas script) can find the block again.
    taken: set[str] = set()
    for e in kept:
        if e.kind is EntryKind.FOLDER:
            continue  # a folder is never what a string is named by
        if e.kind in NAMED_UNIQUELY:
            unique = free_name(e.name, taken)
            if unique != e.name:
                warnings.append(
                    f"{e.name}: renamed {unique}; two rows cannot share a name"
                )
                e.name = unique
        taken.add(e.name)
    return LoadedProject(
        kept,
        current if current in kept else None,
        warnings,
        version if isinstance(version, int) else PROJECT_VERSION,
        migrated_from,
        glossary_from(data.get("glossary")),
    )


def _entry_from(raw: dict[str, Any], base: str) -> tuple[Entry, int | None]:
    if str(raw.get("kind")) == "font":
        # Projects written before the Preview drew in a system font kept a
        # glyph-sheet entry per font. Dropped by name, so the warning says what
        # happened rather than naming a kind nobody recognises.
        raise ValueError(
            "glyph-sheet fonts are gone; the Preview draws every block in the "
            "system font its Font tab picks"
        )
    kind = EntryKind(raw["kind"])
    path = _abs(raw.get("path"), base)
    if path is not None:
        path = _recover_case(path)
    extra = tuple(_recover_case(_abs(p, base)) for p in raw.get("extra_paths", []))  # type: ignore[arg-type]
    session_raw = raw.get("session", {}) or {}
    session = EntrySession(
        table_id=session_raw.get("table_id"),
        offset=int(session_raw.get("offset", 0)),
        view=session_raw.get("view", "raw"),
        resolve_pointers=bool(session_raw.get("resolve_pointers", False)),
    )
    if session_raw.get("config"):
        # A view setting, so one that no longer reads costs the setting and
        # never the entry.
        try:
            session.config = current_config_ids(parse_config(session_raw["config"]))
        except (ValueError, KeyError):
            session.config = None
    entry = Entry(
        kind,
        str(raw.get("name", os.path.basename(path or "") or kind.value)),
        path,
        extra,
        container_id=str(raw.get("container_id", "raw")),
        compression_id=raw.get("compression_id"),
        session=session,
    )
    if kind is EntryKind.BLOCK and raw.get("config"):
        entry.config = parse_config(raw["config"])
    # A project names plugins the build it was saved by had. A renamed id keeps
    # resolving (mapchar.plugins.aliases), so the entry opens through the plugin
    # that has its behaviour now rather than degrading to a pass-through.
    entry.container_id = current_id(entry.container_id)
    if entry.compression_id:
        entry.compression_id = current_id(entry.compression_id)
    if entry.config is not None:
        entry.config = current_config_ids(entry.config)
    if kind is EntryKind.BLOCK:
        entry.slice_offset = int(raw.get("slice_offset", 0))
        # A slot with no room in it is not a thing anyone means: a stored 0
        # reads as unknown.
        stored = raw.get("slice_length")
        entry.slice_length = int(stored) if stored else None
        entry.spare_room = str(raw.get("spare_room", "fill"))
    if kind is EntryKind.BOOKMARK:
        entry.bookmark_offset = int(raw.get("offset", 0))
    if kind is EntryKind.TABLE:
        entry.dialect = raw.get("dialect")
        charset = raw.get("charset")
        entry.table_charset = str(charset) if charset else None
        includes = raw.get("includes")
        if isinstance(includes, list):
            entry.table_includes = tuple(str(i) for i in includes)
        if path and raw.get("table"):
            entry.table_id = str(raw["table"])
        # Kept until the file has been read, which is what it is laid over
        # (:func:`~mapchar.project.tables.adopt_table`).
        overlay = raw.get("overlay")
        entry.table_overlay = {
            str(bits): None if line is None else str(line)
            for bits, line in (overlay if isinstance(overlay, dict) else {}).items()
        }
        if not path:
            # No file to read: the overlay is all there is of the table.
            try:
                table = Table(str(raw.get("table") or sanitize_id(entry.name)))
            except MapcharError:
                table = Table("table")
            adopt_table(entry, table, from_file=False)
    if kind is EntryKind.BLOCK and isinstance(raw.get("box"), dict):
        b = raw["box"]
        origin = b.get("origin", [0, 0])
        effects = {}
        for label, pair in (b.get("effects") or {}).items():
            try:
                effects[str(label)] = CodeEffect(Effect(pair[0]), int(pair[1]))
            except (ValueError, IndexError, TypeError):
                continue
        entry.box = TextBox(
            int(b.get("width", 128)),
            int(b.get("height", 32)),
            int(b.get("line_height", 8)),
            int(b.get("letter_spacing", 0)),
            int(b.get("lines_per_page", 0)),
            int(origin[0]),
            int(origin[1]),
            effects,
            int(b.get("chars_per_line", 0)),
        )
    saved: dict[int, StringState] = {}
    for s in raw.get("strings", []) or []:
        try:
            saved[int(s["i"])] = StringState(
                s.get("o"),
                Status(s.get("s", "untouched")),
                str(s.get("n", "")),
                s.get("t"),
            )
        except (KeyError, ValueError):
            continue
    # Until the block is opened and extracted this is the only place its
    # originals exist, and a save has to be able to write them back
    # (:func:`_string_records`).
    entry.pending_strings = saved or None
    entry.fixed_ends_shown = bool(saved) and bool(raw.get("fixed_ends_shown"))
    parent = raw.get("parent")
    return entry, (int(parent) if isinstance(parent, int) else None)
