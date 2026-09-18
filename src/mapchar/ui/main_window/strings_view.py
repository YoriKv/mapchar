"""Extracting a block's strings and building the grid's rows."""

from __future__ import annotations

import re
from collections import Counter

from PySide6.QtCore import QPoint
from PySide6.QtWidgets import QApplication, QMenu

from mapchar.core.block import BlockConfig, Status, block_bound
from mapchar.core.document import Document
from mapchar.core.font import Effect
from mapchar.core.table import TableSet
from mapchar.engines.layout import char_layout
from mapchar.engines.layout import layout as layout_glyphs
from mapchar.pipeline.extract import extract, respell_fixed_end
from mapchar.pipeline.insert import room_for, string_ends
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
            message = (
                f"{entry.name}: start table @{cfg.table_id} is not loaded; its "
                "strings are kept but cannot be re-read."
            )
            held = sum(
                1
                for st in (entry.pending_strings or {}).values()
                if st.translation is not None
            )
            if held:
                # They are not in the bytes and cannot be put there until the
                # table is back. The project keeps them meanwhile, so a save
                # writes them out again and the next readable extraction lands
                # them — but the user has to be told, in something that outlasts
                # a status message the load's own overwrites.
                message += (
                    f" {held} translation(s) an older project was holding are "
                    "still waiting for it and have not been written into the "
                    "bytes; the project keeps them until the table is back."
                )
                self._note_load_problem(message)
            self.statusBar().showMessage(message, 6000)
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
            ex = self._reuse_extraction(doc.data, cfg, tables)
            if ex is None:
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

            def spelled(text: str, rec) -> str:
                if not entry.fixed_ends_shown:
                    return text
                return respell_fixed_end(text, rec, cfg, tables)

            for rec in ex.strings:
                st = saved.get(rec.index)
                if st is None:
                    continue
                same = st.extent is None or st.extent == (rec.start_bit, rec.end_bit)
                if same:
                    # State follows the string, not the index: a string the new
                    # reading cuts at other bits is not the one that was
                    # marked, so its original, its mark and its notes are all
                    # about text that is no longer there and it starts afresh.
                    if st.original is not None:
                        rec.original = spelled(st.original, rec)
                    rec.status, rec.notes = st.status, st.notes
                if st.translation is not None:
                    legacy[rec.index] = spelled(st.translation, rec)
            entry.pending_strings = None
            entry.fixed_ends_shown = False
        for rec in ex.strings:
            rec.refresh_status()
        doc.strings = ex.strings
        doc.notices = ex.notices
        doc.inner_tables = ex.inner_tables
        doc.extraction_key = key
        if legacy:
            self._land_legacy_translations(entry, doc, legacy)
        self.files_panel.refresh_labels()
        return True

    @staticmethod
    def _reading_key(cfg: BlockConfig, tables: TableSet) -> tuple:
        """What a reading of some bytes depends on besides the bytes: the
        block's configuration and the tables as they stand."""
        return (
            cfg,
            tables.start.id,
            sum(len(t.entries) for t in tables.tables.values()),
        )

    def _remember_extraction(
        self, data: bytes, cfg: BlockConfig, tables: TableSet, extraction
    ) -> None:
        """Keep the reading a string edit checked its own result against.

        The edit lands by splicing exactly those bytes in, and the block is then
        read again to say what they now mean — the same bytes through the same
        tables, which is the reading already in hand
        (:meth:`~mapchar.ui.main_window.string_edit.StringEditMixin._reads_back`).
        """
        self._checked_extraction = (data, self._reading_key(cfg, tables), extraction)

    def _reuse_extraction(self, data: bytes, cfg: BlockConfig, tables: TableSet):
        """The remembered reading when it is of exactly these bytes through
        exactly this reading; ``None`` otherwise.

        Taken once and then forgotten: its records become the block's, and no
        second block may be given the same ones.
        """
        kept = self._checked_extraction
        if kept is None or kept[1] != self._reading_key(cfg, tables) or kept[0] != data:
            return None
        self._checked_extraction = None
        return kept[2]

    def _note_load_problem(self, message: str) -> None:
        """Keep something a block's read has to say where a project load shows
        it (:meth:`~mapchar.ui.main_window.projects.ProjectMixin.open_project`),
        rather than in a status message the load's own replaces."""
        self._load_notices.append(message)

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
        message = (
            f"{entry.name}: {len(problems)} translation(s) from the older project "
            "would not fit and were kept in the notes"
        )
        self._note_load_problem(message + ":\n  " + "\n  ".join(problems))
        self.statusBar().showMessage(message, 8000)

    def _edit_strings_now(
        self, entry: Entry, doc: Document, texts: dict[int, str]
    ) -> list[str]:
        """The edits of :meth:`_edit_strings` landed with no undo step, whole
        where they all go in and one at a time where they do not, so that one
        refused leaves the rest in.

        Refused on the same terms as the checked path
        (:meth:`~mapchar.ui.main_window.string_edit.StringEditMixin._reads_back`):
        a text that does not fit, that re-cuts the block, or that would not read
        back as itself does not land — the bytes are the translation, so they
        must say what the translator said, and a project's word for it is not
        enough.
        """
        cfg = entry.config
        tables = self._table_set_of(entry)
        if cfg is None or tables is None:
            return ["table not loaded"]
        _, problems = self._edit_each(
            entry,
            texts,
            "",
            land=lambda _e, edits, _text: self._land_strings_now(
                entry, doc, edits, cfg, tables
            ),
        )
        return problems

    def _land_strings_now(
        self,
        entry: Entry,
        doc: Document,
        edits: dict[int, str],
        cfg: BlockConfig,
        tables: TableSet,
    ) -> list[str]:
        """One batch of :meth:`_edit_strings_now`: the layout, the read-back
        check, and the splice onto every document that holds the same bytes."""
        from mapchar.pipeline.insert import apply_splices, layout_block

        recs = {i: r for i in edits if (r := doc.string_by_index(i)) is not None}
        if not recs:
            return []
        edits = {i: edits[i] for i in recs}
        try:
            for i, rec in recs.items():
                rec.replacement = edits[i]
            result = layout_block(doc.data, cfg, tables, doc.strings, self.registry)
        finally:
            for rec in recs.values():
                rec.replacement = None
        if result.problems:
            return [f"#{p.index}: {p.message}" for p in result.problems]
        new_data = apply_splices(doc.data, result.splices)
        span = (
            min(s.offset for s in result.splices),
            max(s.end for s in result.splices),
        )
        back = self._reads_back(cfg, tables, doc, new_data, edits, span)
        if back.block is not None:
            return [f"#{min(edits)}: {back.block}"]
        if back.string is not None:
            return [back.string]
        shared = self.workspace.entries_sharing(entry)
        for holder in shared:
            holder.doc.data = new_data
            holder.doc.extraction_key = None
        self._stamp_shared_bytes(entry, self.workspace.next_revision(), shared)
        # Read again so the next edit lays out over the strings as they now
        # sit, and the records keep their state by index.
        self._remember_extraction(new_data, cfg, tables, back.extraction)
        self._extract_current(entry, doc, tables)
        return []

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
        self.strings.set_rows(self._row_data(entry, doc, tables))

    def _patch_rows_over(self, entry, lo: int, hi: int) -> bool:
        """Refresh the grid's rows that the bytes ``lo``–``hi`` could change.

        A row shows what its string's bytes say, how much of its slot they take
        and what its status is; a string the changed bytes do not reach shows
        the same row it did. Its room can still move — a packed block's bound
        is its last string's end — so a row whose address, use or room has
        changed is refreshed too, and the rest are left alone.

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
        bound = block_bound(cfg, doc.strings)
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
        """Where each string's slot ends (:func:`slot_ends`), worked out once
        per reading.

        The bytes and the fill byte go in, so the Room column and the byte
        readout report the slot the layout will actually accept — the string's
        own bytes plus the fill after them — rather than the whole gap to the
        next string. Sorting a pointer block's thousands of strings for every
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
        ends = string_ends(doc.data, cfg, doc.strings, self.registry)
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
        same = Counter(_same_key(rec.original) for rec in doc.strings)
        self._same_counts = (doc.strings, same)
        return same

    def _row_data(self, entry, doc: Document, tables) -> list[RowData]:
        cfg = entry.config if entry is not None else None
        # Once for the block, not once per row: the bound is the same for every
        # string, and a pointer block has thousands.
        bound = block_bound(cfg, doc.strings) if cfg is not None else 0
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
        same = self._same_originals(doc)
        bound = block_bound(entry.config, doc.strings)
        self.strings.update_row(
            self._row_for(
                rec, entry.config, bound, same, self._string_slots(entry, doc, bound)
            )
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
            with self.files_panel.labels_held():
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
                landed, failed = self._edit_each(
                    block, edits, "Apply to identical originals"
                )
                n += landed
                problems += [f"{block.name} {p}" for p in failed]
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
            # The record's own status, not the row's: a row shows "overflows
            # box" in place of it, and an untouched string is still untranslated
            # whatever its box says about it.
            wanted = lambda d: self._is_untouched(d.index)  # noqa: E731
        else:
            wanted = lambda d: d.status in FLAGGED  # noqa: E731
        self._show_view("strings")
        if not self.strings.step_to(wanted, backwards):
            self.statusBar().showMessage(f"No {what} string", 3000)

    def _is_untouched(self, index: int) -> bool:
        """Whether the current block's string at ``index`` is still untouched."""
        rec = self._string(self._entry, index)
        return rec is not None and rec.status is Status.UNTOUCHED

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
