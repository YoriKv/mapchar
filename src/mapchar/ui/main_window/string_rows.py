"""Building the Strings grid's rows, and the counts they are laid out from."""

from __future__ import annotations

import re
from collections import Counter

from mapchar.core.block import block_bound
from mapchar.core.document import Document
from mapchar.core.font import Effect
from mapchar.core.table import TableSet
from mapchar.engines.layout import char_layout
from mapchar.engines.layout import layout as layout_glyphs
from mapchar.pipeline.insert import room_for, room_note, string_ends
from mapchar.project.workspace import Entry
from mapchar.ui.code_editor import CodeInfo
from mapchar.ui.strings_view import OVERFLOWS, RowData

_CODE_IN_TEXT = re.compile(r"(?<!\\)\[([^\]\s]+)")
"""A ``[label`` in script text, for counting which codes a block uses."""


def same_key(text: str) -> str:
    """What two originals are the same by: their text, line breaks aside."""
    return text.replace("\n", "")


class StringRowsMixin:
    """Building the Strings grid's rows, and the counts they are laid out from.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _string(self, entry, index: int):
        """One string of an entry's document by index, if both are there."""
        if entry is None or entry.doc is None:
            return None
        return entry.doc.string_by_index(index)

    def _overflow_status(self, rec, entry: Entry) -> bool:
        """Whether the string overflows its box: measured through the preview
        font, or counted where the box sets characters per line."""
        box = self._layout_box(entry)
        if box is None:
            return False
        text = rec.current_text()
        font = self._layout_font(box, text)
        if font is None:
            return char_layout(text, box).overflows
        return layout_glyphs(text, font, box).overflows

    def _fill_strings(self, doc: Document) -> None:
        if self._rows_patched:
            # A string edit has already refreshed the rows its bytes reached
            # (:meth:`_patch_rows_over`); the grid is as current as a rebuild
            # would leave it, and a rebuild costs every row.
            return
        entry = self._entry
        tables = self._table_set()
        self.strings.set_codes(self._code_infos(tables, doc))
        self.strings.set_newline_code(self._newline_code(entry))
        self.strings.set_rows(self._row_data(entry, doc))

    def _patch_rows_over(self, entry, lo: int, hi: int) -> bool:
        """Refresh the grid's rows that the bytes ``lo``–``hi`` could change.

        A row shows what its string's bytes say, how much of its slot they take
        and what its status is; a string the changed bytes do not reach shows
        the same row it did. Its room can still move — a packed block's bound
        follows its text and the fill behind it — so a row whose address, use
        or room has changed is refreshed too, and the rest are left alone.

        ``False`` when the grid cannot be patched at all — the block edited is
        not the one on screen, or the reading has cut it into other strings —
        and the caller lays the grid out whole instead.
        """
        if entry is None or entry is not self._entry:
            return False
        doc, cfg = entry.doc, entry.config
        if doc is None or doc is not self._doc or cfg is None:
            return False
        tables = self._table_set_of(entry)
        if tables is None:
            return False
        self._extract_current(entry, doc, tables)
        rows = self.strings.rows_by_index()
        if len(rows) != len(doc.strings):
            return False
        bound = block_bound(cfg, doc.strings, entry.room)
        ends = self._string_slots(entry, doc, bound)
        same = self._same_originals(doc)
        for rec in doc.strings:
            old = rows.get(rec.index)
            if old is None:
                return False
            if (
                (rec.end > lo and hi > rec.start)
                or rec.start != old.address
                or rec.byte_length(cfg.skips) != old.used
                or room_for(rec, cfg, bound, ends) != old.room
            ):
                row = self._row_for(rec, cfg, bound, same, ends)
                if row != old:
                    self.strings.update_row(row)
        return True

    @staticmethod
    def _code_infos(tables: TableSet | None, doc: Document) -> list[CodeInfo]:
        """The table set's codes, with their operand shapes and their use counts.

        The counts are what ranks the Insert code buttons: the codes a block
        actually holds come first, not the first two dozen labels by name.
        """
        if tables is None:
            return []
        shapes: dict[str, str] = {}
        comments: dict[str, str] = {}
        for t in tables.tables.values():
            for label, e in t.labels.items():
                shapes.setdefault(label, " ".join(o.spec() for o in e.operands))
                if e.comment:
                    comments.setdefault(label, e.comment.strip().split("\n")[0])
        uses: Counter[str] = Counter()
        for rec in doc.strings:
            uses.update(_CODE_IN_TEXT.findall(rec.current_text()))
        return [
            CodeInfo(label, shapes[label], uses.get(label, 0), comments.get(label, ""))
            for label in sorted(shapes)
        ]

    def _newline_code(self, entry) -> str:
        """What Shift+Return writes: the code carrying the *newline* effect —
        by the box, the table or as the block's line code — else ``[line]``."""
        box = self._layout_box(entry)
        for label, effect in (box.effects if box else {}).items():
            if effect.effect is Effect.NEWLINE:
                return f"[{label}]"
        return "[line]"

    def _string_slots(self, entry, doc: Document, bound: int) -> dict[int, int] | None:
        """Where each string's room ends (:func:`string_ends`), worked out once
        per reading.

        The bytes and the fill byte go in, so the Bytes column and the byte
        readout report the room the layout will actually accept — a slotted
        string's own bytes plus the fill after them, a packed one's own bytes
        plus its group's spare — rather than the whole gap to the next string
        or the whole block. Sorting a pointer block's thousands of strings for every
        row, and for every keystroke, is what the cache is for: the slots are
        where the records sit in the bytes, so the very records and the very
        bytes they were worked out from are what say the answer still stands.
        Both are held rather than named, so no buffer that has gone can be
        mistaken for one that is still there.
        """
        cfg = entry.config if entry is not None else None
        if cfg is None:
            return None
        cached = self._slots_cache
        if (
            cached is not None
            and cached[0] is doc.strings
            and cached[1] is doc.data
            and cached[2] == (bound, cfg)
        ):
            return cached[3]
        ends = string_ends(doc.data, cfg, doc.strings, self.registry, entry.room)
        self._slots_cache = (doc.strings, doc.data, (bound, cfg), ends)
        return ends

    def _same_originals(self, doc: Document) -> Counter:
        """How many of the block's strings share each original, by the key two
        originals are the same under.

        A record's original is settled before it becomes the block's, and a
        re-reading makes new records, so the counts stand as long as the list
        does: refreshing one row does not count the other thousands again.
        """
        cached = self._same_counts
        if cached is not None and cached[0] is doc.strings:
            return cached[1]
        same = Counter(same_key(rec.original) for rec in doc.strings)
        self._same_counts = (doc.strings, same)
        return same

    def _row_data(self, entry, doc: Document) -> list[RowData]:
        cfg = entry.config if entry is not None else None
        # Once for the block, not once per row: the bound is the same for every
        # string, and a pointer block has thousands.
        bound = block_bound(cfg, doc.strings, entry.room) if cfg is not None else 0
        same = self._same_originals(doc)
        ends = self._string_slots(entry, doc, bound)
        return [self._row_for(rec, cfg, bound, same, ends) for rec in doc.strings]

    def _row_for(
        self, rec, cfg, bound: int, same: Counter | None = None, ends=None
    ) -> RowData:
        used = rec.byte_length(cfg.skips) if cfg is not None else rec.length
        room = room_for(rec, cfg, bound, ends)
        status = rec.status.value
        if self._entry is not None and self._overflow_status(rec, self._entry):
            status = OVERFLOWS
        return RowData(
            rec.index,
            rec.start,
            rec.original,
            rec.current_text(),
            used,
            room,
            status,
            rec.notes,
            " ".join(f"{p.address:X}" for p in rec.pointers),
            (same[same_key(rec.original)] - 1) if same is not None else 0,
            room_note(used, room, cfg),
        )

    def _refresh_string_row(self, entry, index: int) -> None:
        doc = entry.doc
        if doc is None:
            return
        rec = doc.string_by_index(index)
        if rec is None:
            return
        same = self._same_originals(doc)
        bound = block_bound(entry.config, doc.strings, entry.room)
        self.strings.update_row(
            self._row_for(
                rec, entry.config, bound, same, self._string_slots(entry, doc, bound)
            )
        )
        self._sync_preview()
