"""The ``.mapchar`` project file: JSON, references and settings, never bytes."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from mapchar.core.block import Status
from mapchar.core.errors import MapcharError
from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.project.formats.script import format_config, parse_config
from mapchar.project.workspace import Entry, EntryKind, EntrySession

PROJECT_VERSION = 1


class ProjectError(MapcharError):
    pass


@dataclass
class StringState:
    translation: str | None = None
    status: Status = Status.UNTOUCHED
    notes: str = ""


@dataclass
class LoadedProject:
    entries: list[Entry]
    current: Entry | None
    strings: dict[int, dict[int, StringState]] = field(default_factory=dict)
    """Per block entry index, per string index: the saved translation state."""
    warnings: list[str] = field(default_factory=list)


def _rel(path: str | None, base: str | None) -> str | None:
    if path is None:
        return None
    if base is None:
        return path
    try:
        rel = os.path.relpath(path, base)
    except ValueError:
        return path
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


def entry_dict(entry: Entry, entries: list[Entry], base: str | None) -> dict[str, Any]:
    d: dict[str, Any] = {"kind": entry.kind.value, "name": entry.name}
    if entry.path:
        d["path"] = _rel(entry.path, base)
    if entry.extra_paths:
        d["extra_paths"] = [_rel(p, base) for p in entry.extra_paths]
    if entry.kind is EntryKind.FILE:
        if entry.container_id != "raw":
            d["container_id"] = entry.container_id
        if entry.reshape_id:
            d["reshape_id"] = entry.reshape_id
        if entry.compression_id:
            d["compression_id"] = entry.compression_id
    if entry.kind in (EntryKind.BLOCK, EntryKind.BOOKMARK):
        d["parent"] = entries.index(entry.parent) if entry.parent in entries else None
    if entry.kind is EntryKind.BLOCK:
        if entry.compression_id:
            d["compression_id"] = entry.compression_id
            d["slice_offset"] = entry.slice_offset
            d["slice_length"] = entry.slice_length
            if entry.spare_room != "fill":
                d["spare_room"] = entry.spare_room
        if entry.config is not None:
            d["config"] = format_config(entry.config)
        if entry.doc is not None:
            strings = []
            for rec in entry.doc.strings:
                if (
                    rec.translation is None
                    and rec.status is Status.UNTOUCHED
                    and not rec.notes
                ):
                    continue
                s: dict[str, Any] = {"i": rec.index}
                if rec.translation is not None:
                    s["t"] = rec.translation
                if rec.status is not Status.UNTOUCHED:
                    s["s"] = rec.status.value
                if rec.notes:
                    s["n"] = rec.notes
                strings.append(s)
            if strings:
                d["strings"] = strings
    if entry.kind is EntryKind.BLOCK and entry.box is not None:
        b = entry.box
        d["box"] = {
            "font_index": b.font_index,
            "width": b.width,
            "height": b.height,
            "line_height": b.line_height,
            "letter_spacing": b.letter_spacing,
            "lines_per_page": b.lines_per_page,
            "origin": [b.origin_x, b.origin_y],
            "effects": {k: [v.effect.value, v.value] for k, v in b.effects.items()},
        }
    if entry.kind is EntryKind.FONT and entry.font is not None:
        f = entry.font
        d["font"] = {
            "cell": [f.cell_width, f.cell_height],
            "columns": f.columns,
            "base": f.base,
            "chars": f.chars,
            "glyphs": dict(f.glyphs),
            "widths": list(f.widths),
            "space": f.space,
            "missing": f.missing,
            "transparent": f.transparent,
        }
    if entry.kind is EntryKind.BOOKMARK:
        d["offset"] = entry.bookmark_offset
    if entry.kind is EntryKind.TABLE and entry.dialect:
        d["dialect"] = entry.dialect
    session: dict[str, Any] = {}
    if entry.session.table_id:
        session["table_id"] = entry.session.table_id
    if entry.session.offset:
        session["offset"] = entry.session.offset
    if entry.session.view != "raw":
        session["view"] = entry.session.view
    if session:
        d["session"] = session
    return d


def project_dict(
    entries: list[Entry], current: Entry | None, base: str | None
) -> dict[str, Any]:
    d: dict[str, Any] = {"version": PROJECT_VERSION}
    if current in entries:
        d["current"] = entries.index(current)
    d["entries"] = [entry_dict(e, entries, base) for e in entries]
    return d


def save_project(path: str, entries: list[Entry], current: Entry | None) -> None:
    base = os.path.dirname(os.path.abspath(path))
    data = project_dict(entries, current, base)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_project(path: str) -> LoadedProject:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError) as exc:
        raise ProjectError(f"cannot read project: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("entries"), list):
        raise ProjectError("not a mapchar project")
    warnings: list[str] = []
    version = data.get("version", 1)
    if isinstance(version, int) and version > PROJECT_VERSION:
        warnings.append(
            f"project version {version} is newer than this build understands"
        )
    base = os.path.dirname(os.path.abspath(path))
    entries: list[Entry] = []
    strings: dict[int, dict[int, StringState]] = {}
    raw_entries = data["entries"]
    parents: list[int | None] = []
    for i, raw in enumerate(raw_entries):
        try:
            entry, parent_index, saved_strings = _entry_from(raw, base)
        except Exception as exc:  # noqa: BLE001 - a broken entry is dropped, never fatal
            warnings.append(f"entry {i} dropped: {exc}")
            entries.append(None)  # type: ignore[arg-type]
            parents.append(None)
            continue
        entries.append(entry)
        parents.append(parent_index)
        if saved_strings:
            strings[i] = saved_strings
    for entry, parent_index in zip(entries, parents, strict=True):
        if entry is not None and parent_index is not None:
            parent = entries[parent_index] if 0 <= parent_index < len(entries) else None
            entry.parent = parent
    kept = [e for e in entries if e is not None]
    strings = {
        kept.index(entries[i]): s for i, s in strings.items() if entries[i] is not None
    }
    current = None
    ci = data.get("current")
    if isinstance(ci, int) and 0 <= ci < len(entries) and entries[ci] is not None:
        current = entries[ci]
    for e in kept:
        if e.is_child and e.parent is None:
            warnings.append(f"{e.name}: parent entry missing")
    kept = [e for e in kept if not (e.is_child and e.parent is None)]
    return LoadedProject(kept, current if current in kept else None, strings, warnings)


def _entry_from(
    raw: dict[str, Any], base: str
) -> tuple[Entry, int | None, dict[int, StringState]]:
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
    )
    entry = Entry(
        kind,
        str(raw.get("name", os.path.basename(path or "") or kind.value)),
        path,
        extra,
        container_id=str(raw.get("container_id", "raw")),
        reshape_id=raw.get("reshape_id"),
        compression_id=raw.get("compression_id"),
        session=session,
    )
    if kind is EntryKind.BLOCK and raw.get("config"):
        entry.config = parse_config(raw["config"])
    if kind is EntryKind.BLOCK:
        entry.slice_offset = int(raw.get("slice_offset", 0))
        entry.slice_length = int(raw.get("slice_length", 0))
        entry.spare_room = str(raw.get("spare_room", "fill"))
    if kind is EntryKind.BOOKMARK:
        entry.bookmark_offset = int(raw.get("offset", 0))
    if kind is EntryKind.TABLE:
        entry.dialect = raw.get("dialect")
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
            b.get("font_index"),
        )
    if kind is EntryKind.FONT:
        f = raw.get("font") or {}
        cell = f.get("cell", [8, 8])
        entry.font = Font(
            path,
            int(cell[0]),
            int(cell[1]),
            int(f.get("columns", 16)),
            int(f.get("base", 0)),
            str(f.get("chars", "")),
            {str(k): int(v) for k, v in (f.get("glyphs") or {}).items()},
            tuple(int(w) for w in f.get("widths", [])),
            f.get("space"),
            f.get("missing"),
            f.get("transparent", 0),
        )
    saved: dict[int, StringState] = {}
    for s in raw.get("strings", []) or []:
        try:
            saved[int(s["i"])] = StringState(
                s.get("t"), Status(s.get("s", "untouched")), str(s.get("n", ""))
            )
        except (KeyError, ValueError):
            continue
    parent = raw.get("parent")
    return entry, (int(parent) if isinstance(parent, int) else None), saved
