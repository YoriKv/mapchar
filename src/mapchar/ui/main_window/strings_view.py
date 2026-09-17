"""Extracting a block's strings and building the grid's rows."""

from __future__ import annotations

import re
from collections import Counter

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.core.block import Status, block_bound
from mapchar.core.document import Document
from mapchar.core.font import Effect
from mapchar.core.table import TableSet
from mapchar.engines.layout import char_layout
from mapchar.engines.layout import layout as layout_glyphs
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import room_for, slot_ends
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.strings_view import FLAGGED, CodeInfo, RowData
from mapchar.ui.undo_commands import StringFieldCommand

_CODE_IN_TEXT = re.compile(r"(?<!\\)\[([^\]\s]+)")
"""A ``[label`` in script text, for counting which codes a block uses."""


def _same_key(text: str) -> str:
    """What two originals are the same by: their text, line breaks aside."""
    return text.replace("\n", "")


class StringsViewMixin:
    """Extracting a block's strings and building the grid's rows.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _overflow_status(self, rec, entry: Entry) -> bool:
        """Whether the string overflows its box: through the bound font, or,
        with none, by the characters per line the box sets."""
        if entry.box is None:
            return False
        font_entry = self._bound_font(entry)
        if font_entry is not None and font_entry.font is not None:
            return layout_glyphs(
                rec.current_text(), font_entry.font, entry.box
            ).overflows
        if entry.box.chars_per_line > 0:
            return char_layout(rec.current_text(), entry.box).overflows
        return False

    def _extract_current(
        self, entry: Entry, doc: Document, tables: TableSet | None
    ) -> bool:
        """Read the block's strings, unless what they are read from is as it was
        the last time; ``True`` when the strings changed."""
        cfg = entry.config
        if cfg is None:
            changed = bool(doc.strings) or doc.extraction_key is not None
            doc.strings = []
            doc.extraction_key = None
            return changed
        if tables is None:
            # The table set is gone: removed, failed to reload, or never picked.
            # The string records stay exactly as they are and are stashed where
            # a document drop cannot reach them — every translation of every
            # block on that table would otherwise go with it. The block reads
            # as unreadable in the Files panel until the table comes back.
            entry.stash_strings(doc)
            changed = doc.extraction_key is not None
            doc.extraction_key = None
            self.statusBar().showMessage(
                f"{entry.name}: start table @{cfg.table_id} is not loaded; its "
                "strings are kept but cannot be re-read.",
                6000,
            )
            self.files_panel.refresh_labels()
            return changed
        key = (
            cfg,
            tables.start.id,
            id(doc.data),
            sum(len(t.entries) for t in tables.tables.values()),
        )
        if doc.extraction_key == key:
            return False
        try:
            ex = extract(doc.data, cfg, tables, self.registry)
        except NotImplementedError as exc:
            # Same rule as a missing table set: the read failed, so there is
            # nothing to replace the translations with, and dropping them would
            # lose work the user cannot get back.
            self.statusBar().showMessage(str(exc), 5000)
            entry.stash_strings(doc)
            return False
        # A re-read without a drop — the bytes changed, the table changed —
        # keeps every string's state by index; a drop stashed it on the entry
        # and it comes back the same way, except where a block edit cut the
        # string at other bits, whose original is then taken from the bytes.
        old = {s.index: s for s in doc.strings}
        for rec in ex.strings:
            prev = old.get(rec.index)
            if prev is not None:
                rec.original, rec.status, rec.notes = (
                    prev.original,
                    prev.status,
                    prev.notes,
                )
        saved = entry.pending_strings
        legacy: dict[int, str] = {}
        if saved:
            for rec in ex.strings:
                st = saved.get(rec.index)
                if st is None:
                    continue
                same = st.extent is None or st.extent == (rec.start_bit, rec.end_bit)
                if same and st.original is not None:
                    rec.original = st.original
                rec.status, rec.notes = st.status, st.notes
                if st.translation is not None:
                    legacy[rec.index] = st.translation
            entry.pending_strings = None
        for rec in ex.strings:
            rec.refresh_status()
        doc.strings = ex.strings
        doc.notices = ex.notices
        doc.extraction_key = key
        if legacy:
            self._land_legacy_translations(entry, doc, legacy)
        self.files_panel.refresh_labels()
        return True

    def _land_legacy_translations(
        self, entry: Entry, doc: Document, texts: dict[int, str]
    ) -> None:
        """Put the translations an older project was still holding into the
        bytes, as the edits they were: the file reads unsaved until written.

        Not an undo step — the project is being read, and the history is
        cleared with it. What will not fit is reported and stays as the
        original, in the notes so it is not lost.
        """
        problems = self._edit_strings_now(entry, doc, texts)
        if not problems:
            return
        for index, text in texts.items():
            rec = doc.string_by_index(index)
            if rec is not None and rec.matches_original(rec.current_text()):
                rec.notes = (
                    rec.notes + "\n" if rec.notes else ""
                ) + f"unplaced: {text}"
        self.statusBar().showMessage(
            f"{entry.name}: {len(problems)} translation(s) from the older project "
            "would not fit and were kept in the notes",
            8000,
        )

    def _edit_strings_now(
        self, entry: Entry, doc: Document, texts: dict[int, str]
    ) -> list[str]:
        """The edits of :meth:`_edit_strings` landed at once, with no undo step,
        and one at a time so that one refused leaves the rest in."""
        from mapchar.pipeline.insert import apply_splices, layout_block

        cfg = entry.config
        tables = self._table_set_of(entry)
        if cfg is None or tables is None:
            return ["table not loaded"]
        problems: list[str] = []
        for index, text in texts.items():
            rec = doc.string_by_index(index)
            if rec is None:
                continue
            rec.replacement = text
            try:
                result = layout_block(doc.data, cfg, tables, doc.strings, self.registry)
            finally:
                rec.replacement = None
            if result.problems:
                problems += [f"#{p.index}: {p.message}" for p in result.problems]
                continue
            new_data = apply_splices(doc.data, result.splices)
            for shared in self._docs_sharing(entry):
                shared.data = new_data
                shared.extraction_key = None
            self.workspace.stamp(self._bytes_owner(entry))
            # Read again so the next edit lays out over the strings as they
            # now sit, and the records keep their state by index.
            self._extract_current(entry, doc, tables)
        return problems

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
        # Once for the block, not once per row: the bound is the same for every
        # string, and a pointer block has thousands.
        bound = block_bound(cfg, doc.strings) if cfg is not None else 0
        same = Counter(_same_key(rec.original) for rec in doc.strings)
        ends = slot_ends(doc.strings, bound)
        return [self._row_for(rec, cfg, bound, same, ends) for rec in doc.strings]

    def _row_for(
        self, rec, cfg, bound: int, same: Counter | None = None, ends=None
    ) -> RowData:
        used = rec.byte_length(cfg.skips) if cfg is not None else rec.length
        room = room_for(rec, cfg, bound, ends)
        status = rec.status.value
        if self._entry is not None and self._overflow_status(rec, self._entry):
            status = "overflows box"
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
            (same[_same_key(rec.original)] - 1) if same is not None else 0,
        )

    def _refresh_string_row(self, entry, index: int) -> None:
        doc = entry.doc
        if doc is None:
            return
        rec = doc.string_by_index(index)
        if rec is None:
            return
        same = Counter(_same_key(r.original) for r in doc.strings)
        bound = block_bound(entry.config, doc.strings)
        self.strings.update_row(
            self._row_for(rec, entry.config, bound, same, slot_ends(doc.strings, bound))
        )
        self._sync_preview()

    def _string(self, entry, index: int):
        """One string of an entry's document by index, if both are there."""
        if entry is None or entry.doc is None:
            return None
        return entry.doc.string_by_index(index)

    def _revert_selected(self) -> None:
        """Put the original text back into the bytes of the selected strings."""
        entry = self._entry
        edits = {}
        for index in self.strings.selected_indices():
            rec = self._string(entry, index)
            if rec is not None and rec.edited:
                edits[index] = rec.original
        if not edits:
            return
        problems = self._edit_strings(entry, edits, "Revert to original")
        if problems:
            self._refuse_edit(problems)

    def _toggle_review_selected(self) -> None:
        self._toggle_status_selected(Status.REVIEW)

    def _toggle_done_selected(self) -> None:
        self._toggle_status_selected(Status.DONE)

    def _toggle_status_selected(self, held: Status) -> None:
        """Mark the selected strings ``held`` — review or done — or, when they
        are, let the bytes settle their status again."""
        with self._macro(f"Toggle {held.value}"):
            for index in self.strings.selected_indices():
                rec = self._string(self._entry, index)
                if rec is None:
                    continue
                new = Status.EDITED if rec.status is held else held
                self._push_command(
                    StringFieldCommand(
                        self, self._entry, index, "status", rec.status.value, new.value
                    )
                )

    def _identical_originals(self, rec, entry, project: bool) -> dict[Entry, list[int]]:
        """Every other string whose original is ``rec``'s, by block: the block's
        own, or every block of the project that is read."""
        key = _same_key(rec.original)
        blocks = [entry]
        if project:
            for other in self.workspace.of_kind(EntryKind.BLOCK):
                if other is entry or other.config is None:
                    continue
                doc = self._load_document(other)
                if doc is None:
                    continue
                self._extract_current(other, doc, self._table_set_of(other))
                blocks.append(other)
        found: dict[Entry, list[int]] = {}
        for block in blocks:
            if block.doc is None:
                continue
            hits = [
                r.index
                for r in block.doc.strings
                if _same_key(r.original) == key
                and not (block is entry and r.index == rec.index)
            ]
            if hits:
                found[block] = hits
        return found

    def _apply_to_identical(self, index: int, project: bool) -> None:
        """The selected string's text into every string with the same original."""
        entry = self._entry
        rec = self._string(entry, index)
        if rec is None:
            return
        text = rec.current_text()
        found = self._identical_originals(rec, entry, project)
        n, problems = 0, []
        with self._macro("Apply to identical originals"):
            for block, indices in found.items():
                if block is not entry:
                    self._activate_entry(block)
                edits = {
                    i: text
                    for i in indices
                    if (r := self._string(block, i)) is not None
                    and r.current_text() != text
                }
                if not edits:
                    continue
                failed = self._edit_strings(
                    block, edits, "Apply to identical originals"
                )
                if failed:
                    problems += [f"{block.name} {p}" for p in failed]
                else:
                    n += len(edits)
            if self._entry is not entry:
                self._activate_entry(entry)
        self.strings.select_index(index)
        self.statusBar().showMessage(f"Applied to {n} string(s)", 4000)
        if problems:
            self._report("Not Applied", f"{len(problems)} string(s) refused", problems)

    def _strings_menu(self, indices: list[int], pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Re&vert to Original", self._revert_selected)
        menu.addAction("Toggle Revie&w", self._toggle_review_selected)
        menu.addAction("Toggle &Done", self._toggle_done_selected)
        if indices:
            rec = self._string(self._entry, indices[0])
            if rec is not None:
                menu.addAction(
                    "Copy &Original",
                    lambda: QApplication.clipboard().setText(rec.original),
                )
                menu.addSeparator()
                index = indices[0]
                same = menu.addAction(
                    "&Apply to Identical Originals in Block",
                    lambda: self._apply_to_identical(index, False),
                )
                same.setEnabled(
                    bool(self._identical_originals(rec, self._entry, False))
                )
                menu.addAction(
                    "Apply to Identical Originals in &Project",
                    lambda: self._apply_to_identical(index, True),
                )
        menu.exec(pos)

    def _step_strings(self, what: str, backwards: bool = False) -> None:
        """Edit ▸ Next / Previous Untranslated or Flagged: the next row of that
        kind among the rows the filter shows, wrapping round."""
        if self._current_block(need_doc=True) is None:
            return
        if what == "untranslated":
            wanted = lambda d: d.status == "untouched"  # noqa: E731
        else:
            wanted = lambda d: d.status in FLAGGED  # noqa: E731
        self._show_view("strings")
        if not self.strings.step_to(wanted, backwards):
            self.statusBar().showMessage(f"No {what} string", 3000)

    def _progress_text(self, doc: Document) -> str:
        """How far the block and the project are: strings whose bytes no longer
        say the original, over all of them."""

        def counts(statuses) -> tuple[int, int, int]:
            statuses = list(statuses)
            touched = sum(s is not Status.UNTOUCHED for s in statuses)
            done = sum(s is Status.DONE for s in statuses)
            return touched, done, len(statuses)

        touched, done, total = counts(rec.status for rec in doc.strings)
        all_touched, all_done, all_total = 0, 0, 0
        for e in self.workspace.of_kind(EntryKind.BLOCK):
            if e.doc is not None:
                a, d, t = counts(rec.status for rec in e.doc.strings)
            elif e.pending_strings:
                a, d, t = counts(st.status for st in e.pending_strings.values())
            else:
                continue
            all_touched += a
            all_done += d
            all_total += t

        def pct(d: int, t: int) -> str:
            return f"{d} / {t} ({100 * d // t}%)" if t else "0 / 0"

        text = f"translated {pct(touched, total)}"
        if done:
            text += f", done {done}"
        if all_total != total:
            text += f" · project {pct(all_touched, all_total)}"
            if all_done:
                text += f", done {all_done}"
        return text

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
        self._sync_glossary()
        if not (self._offset <= rec.start < self._offset + self._view_bytes()):
            self._go_to(rec.start)
        # Through the one selection path, so the Hex dock follows too — and
        # after the move, which would otherwise sync it before the selection is
        # on the window. ``select_index`` blocks its signals, so the row the
        # selection lands back on does not come round again.
        self.raw.set_selection(rec.start, rec.end)
        self._on_selection(rec.start, rec.end)
