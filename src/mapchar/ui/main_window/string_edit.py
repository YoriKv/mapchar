"""Editing strings: their text, which lives in the ROM's bytes, and their
notes and status, which live in the project."""

from __future__ import annotations

from collections.abc import Callable
from typing import NamedTuple

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    Extraction,
    Status,
    block_bound,
    source_span,
)
from mapchar.core.document import Document
from mapchar.core.errors import MapcharError
from mapchar.core.table import TableSet
from mapchar.core.text import same_text
from mapchar.engines.layout import char_layout
from mapchar.pipeline.extract import extract, reextract
from mapchar.pipeline.insert import apply_splices, layout_block, room_for
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import StringFieldCommand, StringsEditCommand


class ReadBack(NamedTuple):
    """Why a laid-out buffer may not stand for an edit, and what it reads as."""

    block: str | None
    """Why the block as a whole refuses it: it will not read at all, or not as
    the same strings."""
    string: str | None
    """The first string that would not read as it must, named by its index."""
    extraction: Extraction | None
    """The reading, when nothing is against it — the one the re-read after the
    edit lands takes, rather than reading the same bytes a second time."""


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

    def _stamp_shared_bytes(
        self, entry: Entry, revision: int, shared: list[Entry] | None = None
    ) -> None:
        """Put ``revision`` on every entry holding a buffer the edit changed.

        Not just the one edited: blocks over one compressed slot share its
        payload, so an edit in any of them is in all of them and a write of any
        of them writes it. One left unstamped would show no unsaved mark and
        answer Write with "nothing to write" while its bytes said otherwise.
        The entries that only *read* another's buffer — a plain block, whose
        bytes are its file's — are not stamped: their file is
        (:meth:`_bytes_owner`).
        """
        owner = self._bytes_owner(entry)
        sharing = self.workspace.entries_sharing(entry) if shared is None else shared
        for holder in sharing:
            if holder is not owner and self._bytes_owner(holder) is holder:
                self.workspace.stamp(holder, revision)
        self.workspace.stamp(owner, revision)

    def _docs_sharing(self, entry: Entry) -> list:
        """The documents of
        :meth:`~mapchar.project.workspace.Workspace.entries_sharing`. A splice
        lands on all of them, so no view is left showing bytes that are no
        longer there."""
        return [e.doc for e in self.workspace.entries_sharing(entry)]

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
        back = self._reads_back(cfg, tables, doc, new_data, edits, (lo, hi))
        if back.block is not None:
            return [back.block]
        if back.string is not None:
            return [back.string]
        first = min(edits)
        label = text or ("Edit translation" if len(edits) == 1 else "Edit translations")
        self._remember_extraction(new_data, cfg, tables, back.extraction)
        self._push_command(
            StringsEditCommand(self, entry, lo, before, after, first, label, run=run)
        )
        return []

    def _reads_back(
        self,
        cfg: BlockConfig,
        tables: TableSet,
        doc: Document,
        data: bytes,
        edits: dict[int, str],
        span: tuple[int, int] | None = None,
    ) -> ReadBack:
        """Whether ``data`` may stand for ``edits``, on the one set of terms.

        The bytes are the translation, so they must say what the translator
        said: the block must still read as the same strings, each edited string
        must read back as its text, and every other one must read as it does
        now — an edit that changes how the bytes after it are cut has changed
        strings nobody asked to change. Both landings, the undo step and the
        undo-free one a project load makes, refuse on these terms and no other.

        ``span`` is the stretch the edit changed: where the block's reading
        comes apart (:func:`~mapchar.pipeline.extract.reextract`), only what it
        reaches is read again.
        """
        try:
            check = None
            if span is not None:
                check = reextract(data, cfg, tables, doc.strings, *span, self.registry)
            if check is None:
                check = extract(data, cfg, tables, self.registry)
        except MapcharError as exc:
            return ReadBack(str(exc), None, None)
        if len(check.strings) != len(doc.strings):
            return ReadBack(
                f"the block would read as {len(check.strings)} strings instead of "
                f"{len(doc.strings)}",
                None,
                None,
            )
        read = {r.index: r for r in check.strings}
        for i, t in edits.items():
            back = read[i].current_text()
            if not same_text(back, t):
                return ReadBack(None, f"#{i}: reads back as {back!r}", None)
        for rec in doc.strings:
            if rec.index in edits or read[rec.index] is rec:
                # A record a partial reading kept is the bytes it was.
                continue
            back = read[rec.index].current_text()
            if not same_text(back, rec.current_text()):
                return ReadBack(None, f"#{rec.index}: would change to {back!r}", None)
        return ReadBack(None, None, check)

    def _edit_each(
        self,
        entry: Entry,
        edits: dict[int, str],
        text: str,
        *,
        land: Callable[[Entry, dict[int, str], str], list[str]] | None = None,
    ) -> tuple[int, list[str]]:
        """``edits`` into the block's bytes: whole where they all go in, else
        one string at a time.

        A block is laid out as a whole, so one text it cannot take refuses the
        rest with it. Going on string by string keeps everything the block does
        have room for, and the translator is told about the rest. How many
        strings landed comes back with the refusals.

        ``land`` is what puts one batch in — :meth:`_edit_strings`, which makes
        an undo step, unless the caller has a landing of its own.
        """
        if not edits:
            return 0, []
        land = land or self._edit_strings
        if len(edits) > 1 and not land(entry, edits, text):
            return len(edits), []
        landed, problems = 0, []
        for index, one in edits.items():
            refused = land(entry, {index: one}, text)
            if refused:
                problems += refused
            else:
                landed += 1
        return landed, problems

    def apply_strings_edit(
        self, entry: Entry, offset: int, data: bytes, revision: int
    ) -> None:
        """Land one side of a strings edit: the bytes, on every document that
        shares them, at the revision that half of the step leaves the owner at.

        ``revision`` is the token the command captured rather than a fresh one:
        an undo hands back exactly the unsaved-state the owner had before the
        edit, so undoing back to what was written reads clean again.
        """
        shared = self.workspace.entries_sharing(entry)
        # The blocks over one file hold the very same buffer: spliced once, the
        # result is theirs too, rather than a copy of a whole ROM per block.
        spliced: dict[int, tuple[bytes, bytes]] = {}
        for holder in shared:
            before = holder.doc.data
            done = spliced.get(id(before))
            if done is None or done[0] is not before:
                buf = bytearray(before)
                buf[offset : offset + len(data)] = data
                done = spliced[id(before)] = (before, bytes(buf))
            holder.doc.data = done[1]
            holder.doc.extraction_key = None
        self._stamp_shared_bytes(entry, revision, shared)
        self._reread_blocks_over(entry, offset, offset + len(data))
        self.files_panel.refresh_labels()
        self._update_title()
        # Every row of the grid is a font layout and a render of its string, so
        # laying a pointer block's thousands out again for an edit that touched
        # one of them is what every commit and every undo would cost. Only the
        # rows the changed bytes reach are refreshed, and the refresh below is
        # told to leave the grid alone.
        self._rows_patched = self._patch_rows_over(entry, offset, offset + len(data))
        try:
            self._refresh_view()
        finally:
            self._rows_patched = False
        # Whatever is left of the reading the edit checked is of bytes every
        # block that wanted them has now read: holding it would hold a copy of
        # the buffer with it.
        self._checked_extraction = None
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
                tables = self._table_set_of(block)
                if tables is not None and self._checked_extraction is None:
                    # An undo or redo brings no reading of its own; a block
                    # whose reading comes apart reads only what changed.
                    part = reextract(
                        doc.data,
                        block.config,
                        tables,
                        doc.strings,
                        lo,
                        hi,
                        self.registry,
                    )
                    if part is not None:
                        self._remember_extraction(doc.data, block.config, tables, part)
                self._extract_current(block, doc, tables)

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
        bound = block_bound(entry.config, entry.doc.strings, entry.doc.data)
        room = (
            room_for(
                rec,
                entry.config,
                bound,
                self._string_slots(entry, entry.doc, bound),
            )
            if rec
            else 0
        )
        readout = f"{used} / {room} byte(s)" if room else f"{used} byte(s)"
        if room and used > room:
            readout += f" — {used - room} over"
        box = self._layout_box(entry)
        if box is not None and box.chars_per_line > 0:
            chars = char_layout(text, box)
            readout += f" · {chars.widest} / {box.chars_per_line} chars"
            if box.lines_per_page > 0:
                readout += f", {chars.lines} / {box.lines_per_page} lines"
        self.statusBar().showMessage(readout)
        self.preview_window.set_readout(readout)
        self.strings.set_readout(readout)
