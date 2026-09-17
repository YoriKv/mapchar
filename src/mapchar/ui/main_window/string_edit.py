"""Editing strings: their text, which lives in the ROM's bytes, and their
notes and status, which live in the project."""

from __future__ import annotations

from mapchar.core.block import EndToken, Status, block_bound, source_span
from mapchar.core.errors import MapcharError
from mapchar.core.text import nfc
from mapchar.engines.layout import char_layout
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, layout_block, room_for, slot_ends
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import StringFieldCommand, StringsEditCommand


def _same_text(a: str, b: str) -> bool:
    return nfc(a).replace("\n", "") == nfc(b).replace("\n", "")


class StringEditMixin:
    """Editing strings: their text, which lives in the ROM's bytes, and their
    notes and status, which live in the project.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _on_translation_edited(self, index: int, text: str) -> None:
        """The Translation cell committed: the edit lands, or its refusal is
        reported and the cell goes back to what the bytes say."""
        problems = self._commit_cell(index, text)
        if problems:
            self._refuse_edit(problems)
            self._refresh_string_row(self._entry, index)

    def _commit_translation(self, index: int, text: str) -> str | None:
        """The editor's Return: the edit lands, or why it did not comes back
        and the editor stays open on the text."""
        problems = self._commit_cell(index, text)
        return "; ".join(problems) if problems else None

    def _commit_cell(self, index: int, text: str) -> list[str]:
        """One cell's text into the bytes, in the run the cell is on. A cell
        left blank puts the original back."""
        rec = self._string(self._entry, index)
        if rec is None:
            return []
        if not text.strip():
            text = rec.original
        return self._edit_strings(self._entry, {index: text}, run=self._edit_run)

    def _on_edit_problem(self, message: str) -> None:
        self.statusBar().showMessage(message, 8000)
        self.preview_window.set_readout(message)
        self.strings.set_readout(message, problem=True)

    def _refuse_edit(self, problems: list[str]) -> None:
        self._on_edit_problem("; ".join(problems))

    def _set_translation(self, entry, index: int, text: str) -> list[str]:
        """One string's text, as an undo step; the reasons when it is refused."""
        return self._edit_strings(entry, {index: text})

    def _bytes_owner(self, entry: Entry) -> Entry:
        """Whose buffer a block's strings sit in, and so who reads unsaved: the
        block itself when it decompresses its own slot, else its file."""
        if entry.compression_id or entry.parent is None:
            return entry
        return entry.parent

    def _docs_sharing(self, entry: Entry) -> list:
        """Every loaded document holding the same bytes as ``entry``'s.

        A plain block reads its file's buffer, with every other plain block on
        that file; a compressed one reads its slot's payload, with every other
        block over the same slot. A splice lands on all of them, so no view is
        left showing bytes that are no longer there.
        """
        if entry.compression_id:
            slot = (entry.compression_id, entry.slice_offset)
            return [
                e.doc
                for e in self.workspace.entries
                if e.doc is not None
                and e.parent is entry.parent
                and (e.compression_id, e.slice_offset) == slot
            ]
        file_entry = entry.parent if entry.parent is not None else entry
        docs = [file_entry.doc] if file_entry.doc is not None else []
        docs += [
            e.doc
            for e in self.workspace.children(file_entry)
            if e.doc is not None and not e.compression_id
        ]
        return docs

    def _edit_strings(
        self,
        entry: Entry | None,
        edits: dict[int, str],
        text: str | None = None,
        *,
        run: int | None = None,
    ) -> list[str]:
        """Put ``{index: text}`` into the block's bytes as one undo step.

        The block is laid out again with the new texts in place of those
        strings' bytes and every other string's bytes kept, and the stretch the
        layout changed is spliced into the buffer. The edit is refused whole —
        nothing lands, the reasons come back — when a text does not encode,
        does not fit, or would not read back as the text typed: the bytes are
        the translation, so they must say what the translator said.

        ``run`` is the editing run a cell commit belongs to: consecutive
        commits in one run on one string merge into one step.
        """
        if entry is None or entry.doc is None or entry.config is None:
            return ["no block to edit"]
        doc, cfg = entry.doc, entry.config
        tables = self._table_set_of(entry)
        if tables is None:
            return [f"table @{cfg.table_id} is not loaded"]
        by_index = {r.index: r for r in doc.strings}
        edits = {i: t for i, t in edits.items() if i in by_index}
        if not edits:
            return []
        try:
            for i, t in edits.items():
                by_index[i].replacement = t
            result = layout_block(doc.data, cfg, tables, doc.strings, self.registry)
        finally:
            for i in edits:
                by_index[i].replacement = None
        if result.problems:
            return [f"#{p.index}: {p.message}" for p in result.problems]
        new_data = apply_splices(doc.data, result.splices)
        lo = min(s.offset for s in result.splices)
        hi = max(s.end for s in result.splices)
        before, after = doc.data[lo:hi], new_data[lo:hi]
        if before == after:
            return []
        try:
            check = extract(new_data, cfg, tables, self.registry)
        except MapcharError as exc:
            return [str(exc)]
        if len(check.strings) != len(doc.strings):
            return [
                f"the block would read as {len(check.strings)} strings instead of "
                f"{len(doc.strings)}"
            ]
        read = {r.index: r for r in check.strings}
        for i, t in edits.items():
            back = read[i].current_text()
            if not _same_text(back, t):
                return [f"#{i}: reads back as {back!r}"]
        # Every other string must read as it did: an edit that changes how
        # the bytes after it are cut has changed strings nobody asked to change.
        for rec in doc.strings:
            if rec.index in edits:
                continue
            back = read[rec.index].current_text()
            if not _same_text(back, rec.current_text()):
                return [f"#{rec.index}: would change to {back!r}"]
        first = min(edits)
        label = text or ("Edit translation" if len(edits) == 1 else "Edit translations")
        self._push_command(
            StringsEditCommand(self, entry, lo, before, after, first, label, run=run)
        )
        return []

    def apply_strings_edit(
        self, entry: Entry, offset: int, data: bytes, revision: int
    ) -> None:
        """Land one side of a strings edit: the bytes, on every document that
        shares them, at the revision that half of the step leaves the owner at.

        ``revision`` is the token the command captured rather than a fresh one:
        an undo hands back exactly the unsaved-state the owner had before the
        edit, so undoing back to what was written reads clean again.
        """
        for doc in self._docs_sharing(entry):
            buf = bytearray(doc.data)
            buf[offset : offset + len(data)] = data
            doc.data = bytes(buf)
            doc.extraction_key = None
        self.workspace.stamp(self._bytes_owner(entry), revision)
        self._reread_blocks_over(entry, offset, offset + len(data))
        self.files_panel.refresh_labels()
        self._update_title()
        self._refresh_view()
        self._refresh_project_strings()

    def _reread_blocks_over(self, entry: Entry, lo: int, hi: int) -> None:
        """Read again every loaded block whose strings share ``entry``'s bytes
        and lie over ``lo``–``hi``: their records say what the bytes said, and
        the bytes just changed. A block elsewhere reads again when next shown.
        """
        docs = {id(d) for d in self._docs_sharing(entry)}
        for block in self.workspace.of_kind(EntryKind.BLOCK):
            doc = block.doc
            if doc is None or id(doc) not in docs or block.config is None:
                continue
            spans = [source_span(block.config.source)]
            if doc.strings:
                spans.append(
                    (min(r.start for r in doc.strings), max(r.end for r in doc.strings))
                )
            if any(
                span is not None and span[0] < hi and lo < span[1] for span in spans
            ):
                self._extract_current(block, doc, self._table_set_of(block))

    def _on_notes_edited(self, index: int, text: str) -> None:
        entry = self._entry
        rec = self._string(entry, index)
        if rec is None or text == rec.notes:
            return
        self._push_command(
            StringFieldCommand(
                self, entry, index, "notes", rec.notes, text, run=self._edit_run
            )
        )

    def apply_string_field(
        self, entry, index: int, field: str, value, revision: int
    ) -> None:
        """Land one field of one string — its notes or its status — at the
        revision that half of the step leaves the entry at."""
        rec = self._string(entry, index)
        if rec is None:
            return
        if field == "notes":
            rec.notes = value
        elif field == "status":
            rec.status = Status(value)
            rec.refresh_status()
        self.workspace.stamp(entry, revision)
        self._refresh_string_row(entry, index)
        self.files_panel.refresh_labels()
        self._update_title()
        self._refresh_project_strings()

    def _on_draft(self, text: str) -> None:
        """The Translation cell as it is typed: a live encode and a live preview.

        The draft never touches the document — it lands on the Preview, on the
        byte readout, and on the Preview's list of what the font cannot spell.
        """
        entry = self._entry
        tables = self._table_set()
        if entry is None or entry.config is None or tables is None:
            return
        from mapchar.engines.encode import encode

        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        if self.preview_window.isVisible() and rec is not None:
            self.preview_window.show_string(text, f"{entry.name} #{rec.index}")
        try:
            result = encode(
                text,
                tables,
                end_terminated=isinstance(entry.config.string_type, EndToken),
                ends=entry.config.strings_per_pointer,
            )
        except MapcharError as exc:
            self.statusBar().showMessage(str(exc))
            self.preview_window.set_readout(str(exc))
            self.strings.set_readout(str(exc), problem=True)
            return
        used = -(-len(result.bits) // 8)
        bound = block_bound(entry.config, entry.doc.strings)
        room = (
            room_for(rec, entry.config, bound, slot_ends(entry.doc.strings, bound))
            if rec
            else 0
        )
        readout = f"{used} / {room} byte(s)" if room else f"{used} byte(s)"
        if room and used > room:
            readout += f" — {used - room} over"
        box = entry.box
        if box is not None and box.chars_per_line > 0:
            chars = char_layout(text, box)
            readout += f" · {chars.widest} / {box.chars_per_line} chars"
            if box.lines_per_page > 0:
                readout += f", {chars.lines} / {box.lines_per_page} lines"
        self.statusBar().showMessage(readout)
        self.preview_window.set_readout(readout)
        self.strings.set_readout(readout)
