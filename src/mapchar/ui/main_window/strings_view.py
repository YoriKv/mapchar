"""Extracting a block's strings and building the grid's rows."""

from __future__ import annotations

import re
from collections import Counter

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.core.block import FixedLength, FixedSource, Status, WriteMode
from mapchar.core.document import Document
from mapchar.core.font import Effect
from mapchar.core.table import TableSet
from mapchar.engines.layout import layout as layout_glyphs
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import block_bound, layout_block
from mapchar.project.workspace import Entry, StringState
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.strings_view import CodeInfo, RowData
from mapchar.ui.undo_commands import StringFieldCommand

_CODE_IN_TEXT = re.compile(r"(?<!\\)\[([^\]\s]+)")
"""A ``[label`` in script text, for counting which codes a block uses."""


class StringsViewMixin:
    """Extracting a block's strings and building the grid's rows.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _overflow_status(self, rec, entry: Entry) -> bool:
        font_entry = self._bound_font(entry)
        if font_entry is None or font_entry.font is None or entry.box is None:
            return False
        source = rec.translation if rec.translation is not None else rec.original
        return layout_glyphs(source, font_entry.font, entry.box).overflows

    @staticmethod
    def _stash_strings(entry: Entry, doc: Document | None = None) -> None:
        """Copy a block's translations onto the entry, where no document holds
        them.

        The safety net under everything that drops or fails to build a document
        — a block edit, an unavailable table, a refused extraction — so the next
        successful read puts the same translations back on the same indices.
        Merged into whatever is already stashed, since a block may go through
        several such rounds before it reads again.
        """
        doc = doc if doc is not None else entry.doc
        if doc is None or not doc.strings:
            return
        saved = dict(entry.pending_strings or {})
        for rec in doc.strings:
            touched = (
                rec.translation is not None
                or rec.status is not Status.UNTOUCHED
                or rec.notes
            )
            if touched:
                saved[rec.index] = StringState(rec.translation, rec.status, rec.notes)
        entry.pending_strings = saved or None

    def _extract_current(
        self, entry: Entry, doc: Document, tables: TableSet | None
    ) -> None:
        cfg = entry.config
        if cfg is None:
            doc.strings = []
            doc.extraction_key = None
            return
        if tables is None:
            # The table set is gone: removed, failed to reload, or never picked.
            # The string records stay exactly as they are and are stashed where
            # a document drop cannot reach them — every translation of every
            # block on that table would otherwise go with it. The block reads
            # as unreadable in the Files panel until the table comes back.
            self._stash_strings(entry, doc)
            doc.extraction_key = None
            self.statusBar().showMessage(
                f"{entry.name}: start table @{cfg.table_id} is not loaded; its "
                "strings are kept but cannot be re-read.",
                6000,
            )
            self.files_panel.refresh_labels()
            return
        key = (
            cfg,
            tables.start.id,
            id(doc.data),
            sum(len(t.entries) for t in tables.tables.values()),
        )
        if doc.extraction_key == key:
            return
        try:
            ex = extract(doc.data, cfg, tables, self.registry)
        except NotImplementedError as exc:
            # Same rule as a missing table set: the read failed, so there is
            # nothing to replace the translations with, and dropping them would
            # lose work the user cannot get back.
            self.statusBar().showMessage(str(exc), 5000)
            self._stash_strings(entry, doc)
            return
        old = {s.index: s for s in doc.strings}
        for rec in ex.strings:
            prev = old.get(rec.index)
            if prev is not None:
                rec.translation, rec.status, rec.notes = (
                    prev.translation,
                    prev.status,
                    prev.notes,
                )
        saved = entry.pending_strings
        if saved:
            for rec in ex.strings:
                st = saved.get(rec.index)
                if st is not None:
                    rec.translation, rec.status, rec.notes = (
                        st.translation,
                        st.status,
                        st.notes,
                    )
            entry.pending_strings = None
        doc.strings = ex.strings
        doc.notices = ex.notices
        doc.extraction_key = key
        self.files_panel.refresh_labels()

    def _fill_strings(self, doc: Document) -> None:
        entry = self._entry
        tables = self._table_set()
        self.strings.set_codes(self._code_infos(tables, doc))
        self.strings.set_newline_code(self._newline_code(entry))
        self.strings.set_rows(self._row_data(entry, doc, tables))

    @staticmethod
    def _code_infos(tables: TableSet | None, doc: Document) -> list[CodeInfo]:
        """The table set's codes, with their operand shapes and their use counts.

        The counts are what ranks the Insert code buttons: the codes a block
        actually holds come first, not the first two dozen labels by name.
        """
        if tables is None:
            return []
        shapes: dict[str, str] = {}
        for t in tables.tables.values():
            for label, e in t.labels.items():
                shapes.setdefault(label, " ".join(o.spec() for o in e.operands))
        uses: Counter[str] = Counter()
        for rec in doc.strings:
            uses.update(_CODE_IN_TEXT.findall(rec.current_text()))
        return [
            CodeInfo(label, shapes[label], uses.get(label, 0))
            for label in sorted(shapes)
        ]

    @staticmethod
    def _newline_code(entry) -> str:
        """What Shift+Return writes: the box's newline code, else ``[line]``."""
        box = entry.box if entry is not None else None
        for label, effect in (box.effects if box else {}).items():
            if effect.effect is Effect.NEWLINE:
                return f"[{label}]"
        return "[line]"

    def _row_data(self, entry, doc: Document, tables) -> list[RowData]:
        cfg = entry.config if entry is not None else None
        result = None
        if cfg is not None and tables is not None and doc.strings:
            result = layout_block(doc.data, cfg, tables, doc.strings, self.registry)
        # What the Files panel's "too long" count reads; the layout is only run
        # here, so this is where the number is known.
        doc.too_long = sum(p.over for p in result.problems) if result is not None else 0
        rows = []
        for rec in doc.strings:
            rows.append(self._row_for(rec, cfg, result, doc.strings))
        return rows

    def _row_for(self, rec, cfg, result, strings=()) -> RowData:
        used = rec.byte_length(cfg.skips) if cfg is not None else rec.length
        room, problem = self._room(rec, cfg, strings), ""
        status = rec.status.value
        if result is not None:
            enc = result.encoded.get(rec.index)
            if enc is not None and enc.problem is None:
                used = len(enc.data)
            problems = [p for p in result.problems if p.index == rec.index]
            if problems:
                problem = problems[0].message
                status = "too long" if problems[0].over else "invalid"
        if status in ("untouched", "edited", "review") and self._entry is not None:
            if self._overflow_status(rec, self._entry):
                status = "overflows box"
        return RowData(
            rec.index,
            rec.start,
            rec.original_text(),
            rec.translation,
            used,
            room,
            status,
            rec.notes,
            problem,
            " ".join(f"{p.address:X}" for p in rec.pointers),
        )

    @staticmethod
    def _room(rec, cfg, strings=()) -> int:
        if cfg is None:
            return rec.length
        if isinstance(cfg.string_type, FixedLength):
            return cfg.string_type.length
        if isinstance(cfg.source, FixedSource):
            return cfg.source.length
        if cfg.effective_write_mode is WriteMode.PACKED:
            return max(block_bound(cfg, list(strings)) - rec.start, 0)
        return rec.byte_length(cfg.skips)

    def _refresh_string_row(self, entry, index: int) -> None:
        doc = entry.doc
        if doc is None:
            return
        rec = next((r for r in doc.strings if r.index == index), None)
        if rec is None:
            return
        tables = self._table_set()
        result = None
        if tables is not None and entry.config is not None:
            result = layout_block(
                doc.data, entry.config, tables, doc.strings, self.registry
            )
        self.strings.update_row(self._row_for(rec, entry.config, result, doc.strings))
        self._sync_preview()

    def _string(self, entry, index: int):
        """One string of an entry's document by index, if both are there."""
        if entry is None or entry.doc is None:
            return None
        return entry.doc.string_by_index(index)

    def _revert_selected(self) -> None:
        for index in self.strings.selected_indices():
            rec = self._string(self._entry, index)
            if rec is not None and rec.translation is not None:
                self._push_command(
                    StringFieldCommand(
                        self, self._entry, index, "translation", rec.translation, None
                    )
                )

    def _toggle_review_selected(self) -> None:
        for index in self.strings.selected_indices():
            rec = self._string(self._entry, index)
            if rec is None:
                continue
            new = Status.EDITED if rec.status is Status.REVIEW else Status.REVIEW
            if new is Status.EDITED and rec.translation is None:
                new = Status.UNTOUCHED
            self._push_command(
                StringFieldCommand(
                    self, self._entry, index, "status", rec.status.value, new.value
                )
            )

    def _copy_originals(self) -> None:
        entry = self._entry
        if entry is None or entry.doc is None:
            return
        self.undo_stack.beginMacro("Copy originals")
        for rec in entry.doc.strings:
            if rec.translation is None:
                self._push_command(
                    StringFieldCommand(
                        self, entry, rec.index, "translation", None, rec.original_text()
                    )
                )
        self.undo_stack.endMacro()

    def _strings_menu(self, indices: list[int], pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Revert to original", self._revert_selected)
        menu.addAction("Toggle review", self._toggle_review_selected)
        if indices:
            rec = self._string(self._entry, indices[0])
            if rec is not None:
                menu.addAction(
                    "Copy original",
                    lambda: QApplication.clipboard().setText(rec.original_text()),
                )
        menu.exec(pos)

    def _on_string_row(self, index: int) -> None:
        rec = self._string(self._entry, index)
        if rec is None:
            return
        # Moving off a row ends the editing run on it, so the next edit starts a
        # step of its own rather than merging into the last one
        # (:class:`~mapchar.ui.undo_commands.StringFieldCommand`).
        if not self._applying_undo:
            self._edit_run += 1
        self._sync_preview()
        if not (self._offset <= rec.start < self._offset + self.raw.visible_bytes()):
            self._go_to(max(0, rec.start - BYTES_PER_ROW))
        self.raw.set_selection(rec.start, rec.end)
        self._selection = (rec.start, rec.end)
        self._update_nav_status()
