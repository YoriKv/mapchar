"""Writing the buffers back to disk."""

from __future__ import annotations

from contextlib import nullcontext

from mapchar.core.errors import MapcharError
from mapchar.pipeline.filechange import FileChange, existing_bytes
from mapchar.pipeline.insert import Splice, apply_splices
from mapchar.pipeline.pipeline import (
    FileRef,
    PathwayConfig,
    compress_for_slot,
    save,
)
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.dialogs import TextDialog
from mapchar.ui.undo_commands import BlockSide, BytesCommand, WriteCommand, WriteSide


class WritingMixin:
    """Writing the buffers back to disk.

    A string edit is already in the bytes — the file's buffer, or the payload
    of the slot a compressed block decodes — so a write lays nothing out: it
    compresses each edited slot back into the file and saves the file through
    its container.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _write_entry(self, entry: Entry) -> None:
        """The Files panel's Write: one entry, or an explanation why not.

        A bookmark and a table are not the same kind of "cannot": a bookmark has
        no bytes of its own at all, and a table is written with Save As File
        rather than through the pipeline. Silence made both read as a bug.
        """
        if entry.kind is EntryKind.BOOKMARK:
            self._error(
                f"{entry.name} is a saved position, not a region: there are no "
                "bytes of its own to write. Write the ROM it sits in, or a block."
            )
            return
        if entry.kind is EntryKind.TABLE:
            self._error(
                f"{entry.name} is a table file. Its in-app edits are written with "
                "Save As File…, not through the ROM's write path."
            )
            return
        if entry.kind is EntryKind.FONT:
            self._error(f"{entry.name} is a glyph sheet; mapChar never writes to it.")
            return
        doc = entry.doc if entry.doc is not None else self._load_document(entry)
        if doc is not None and not doc.writable:
            missing = ", ".join(doc.missing_plugins) or "a stage with no write-back"
            self._error(
                f"{entry.name} is view-only: one of the stages it reads through "
                f"has no way to put the bytes back ({missing}), so it cannot be "
                "written."
            )
            return
        if entry.kind is EntryKind.BLOCK:
            if not (entry.dirty or (entry.parent is not None and entry.parent.dirty)):
                self.statusBar().showMessage(f"{entry.name} has nothing to write", 3000)
                return
            self._write_blocks([entry] if entry.dirty else [], files=[entry.parent])
            return
        blocks = [b for b in self.workspace.blocks_of(entry) if b.dirty]
        if entry.dirty or blocks:
            self._write_blocks(blocks, files=[entry])
        else:
            self.statusBar().showMessage(f"{entry.name} has nothing to write", 3000)

    def _block_strings(self, entry: Entry) -> list | None:
        """The block's strings, extracted afresh under its own table set;
        ``None`` when the block cannot be read at all."""
        doc = self._load_document(entry)
        if doc is None or entry.config is None:
            return None
        self._extract_current(entry, doc, self._table_set_of(entry))
        return doc.strings

    def _dirty_blocks(self) -> list[Entry]:
        """The blocks with a buffer of their own that is unsaved: the ones
        decompressing a slot. A plain block's edits are its file's."""
        return [e for e in self.workspace.of_kind(EntryKind.BLOCK) if e.dirty]

    def _write_current(self) -> None:
        """File ▸ Write: the entry on screen, or why it cannot be written.

        The same gate as the Files panel's Write, so neither surface can fail
        silently on a row that has no bytes of its own or reads through a stage
        with no way back.
        """
        entry = self._entry
        if entry is None:
            self.statusBar().showMessage("Nothing to write", 3000)
            return
        self._write_entry(entry)

    def _write_all(self) -> bool:
        blocks = self._dirty_blocks()
        files = [e for e in self.workspace.files() if e.dirty]
        if not blocks and not files:
            self.statusBar().showMessage("Nothing to write", 3000)
            return True
        return self._write_blocks(blocks, files=files)

    def _write_blocks(
        self, blocks: list[Entry], files: list[Entry] | None = None
    ) -> bool:
        """Write the files with unsaved bytes, the slots of ``blocks`` compressed
        back into them first.

        Each file written is one undo step (:class:`WriteCommand`); several in
        one call are one macro, so Write All comes back with one Ctrl+Z. The
        disk is written here, where a refusal can be reported beside the block
        it concerns, and the command's first redo lands only the in-memory side
        — its file already holds the result, and
        :meth:`~mapchar.pipeline.filechange.FileChange.apply` leaves a file that
        does alone.
        """
        by_file: dict[int, tuple[Entry, list[Entry]]] = {}
        for b in blocks:
            if b.parent is None:
                continue
            by_file.setdefault(id(b.parent), (b.parent, []))[1].append(b)
        for f in files or []:
            by_file.setdefault(id(f), (f, []))
        ok = True
        commands: list[WriteCommand] = []
        for file_entry, file_blocks in by_file.values():
            command = self._write_file(file_entry, file_blocks)
            if command is None:
                ok = False
            else:
                commands.append(command)
        # One file's write is its own step, named after the file; several are
        # grouped under one, so undoing a Write All puts every file back.
        group = self._macro("Write All") if len(commands) > 1 else nullcontext()
        with group:
            for command in commands:
                self._push_command(command)
        # The bytes on disk no longer say the originals; the project does, so
        # a copy of it goes out now rather than on the timer.
        if commands:
            self._autosave()
        return ok

    def _write_file(
        self, file_entry: Entry, file_blocks: list[Entry]
    ) -> WriteCommand | None:
        """Write one file with the slots of ``file_blocks`` compressed into it;
        ``None`` when it was refused.

        Several blocks can sit over one compressed slot and share its payload,
        so each slot is compressed once, and every block over it — asked for
        or not — is written with it, since the payload is theirs too.

        Every block handed here is written, and a block that cannot be is a
        problem reported against its name: a block left out of a write that
        reported success would stay unsaved for ever, with nothing able to
        resolve it.
        """
        parent_doc = self._load_document(file_entry)
        if parent_doc is None:
            return None
        problems: list[str] = []
        new_data = parent_doc.data
        # A plain block's bytes are the file's own buffer, which is what is
        # about to be written: it has no slot to compress, and is written with
        # the file. A compressed one is read now if it is not loaded — its
        # payload is what its slot is written from, and a dropped document must
        # not turn into a block silently skipped.
        plain = [b for b in file_blocks if not b.compression_id]
        compressed: list[Entry] = []
        for block in file_blocks:
            if not block.compression_id:
                continue
            if self._load_document(block) is None:
                problems.append(
                    f"{block.name}: its compressed slot cannot be read, so it "
                    "cannot be written."
                )
            else:
                compressed.append(block)
        slots: dict[tuple, list[Entry]] = {}
        for block in compressed:
            slot = (block.compression_id, block.slice_offset)
            if slot not in slots:
                slots[slot] = self.workspace.entries_sharing(block)
        written: list[tuple[Entry, bytes | None]] = [(b, None) for b in plain]
        for (_scheme, offset), sharing in slots.items():
            payload = sharing[0].doc.data
            packed, problem = self._recompress(sharing[0], payload)
            if problem:
                problems.append(problem)
                continue
            new_data = apply_splices(new_data, [Splice(offset, packed)])
            written += [(block, payload) for block in sharing]
        if problems:
            TextDialog("Cannot Write", "\n".join(problems), self).exec()
            return None
        if not parent_doc.writable:
            self._error(f"{file_entry.name} is view-only (a stage cannot write back).")
            return None
        cfg = PathwayConfig(
            FileRef(file_entry.paths),
            file_entry.container_id,
            file_entry.compression_id,
            pathway=file_entry.name,
        )
        held = existing_bytes(file_entry.paths)
        try:
            changed = save(new_data, cfg, self.registry, parent_doc.ctx)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot write {file_entry.name}: {exc}")
            return None
        now = existing_bytes(tuple(changed))
        files = tuple(
            change
            for path in changed
            if (change := FileChange.between(path, held[path], now[path])) is not None
        )
        before = WriteSide(
            tuple(c.flipped() for c in files),
            parent_doc.data,
            file_entry.live_revision,
            file_entry.saved_revision,
            tuple(
                BlockSide(
                    block,
                    parent_doc.data if payload is None else payload,
                    block.live_revision,
                    block.saved_revision,
                )
                for block, payload in written
            ),
        )
        after = WriteSide(
            files,
            new_data,
            file_entry.live_revision,
            file_entry.live_revision,
            tuple(
                BlockSide(
                    block,
                    new_data if payload is None else payload,
                    block.live_revision,
                    block.live_revision,
                )
                for block, payload in written
            ),
        )
        return WriteCommand(self, file_entry, before, after)

    def apply_write(self, entry: Entry, side: WriteSide) -> None:
        """Land one side of a write: the files on disk, then everything in memory.

        Disk first, and all of it or none: a file that no longer holds the side
        being left is reported and the step is skipped whole, since a buffer
        moved to one side while the file stayed on the other would show bytes
        that are not there. Then the file's buffer, the written blocks'
        payloads, and the same refresh of what else reads the file that the
        write itself does. String state — originals, statuses, notes — is the
        project's and does not move with a write.
        """
        for change in side.files:
            if not change.holds_after() and not change.holds_before():
                self._error(
                    f"{change.path} has changed on disk since it was written, so it "
                    "was left as it is."
                )
                return
        for change in side.files:
            change.apply()
        doc = self._load_document(entry)
        if doc is None:
            return
        doc.data = side.data
        self.workspace.set_revisions(entry, side.live, side.saved)
        written = []
        for bs in side.blocks:
            written.append(bs.entry)
            bdoc = bs.entry.doc
            if bdoc is not None:
                bdoc.data = bs.data
                bdoc.extraction_key = None
            self.workspace.set_revisions(bs.entry, bs.live, bs.saved)
        for child in self.workspace.blocks_of(entry, loaded=True):
            if child in written:
                continue
            if not child.compression_id:
                child.doc.data = side.data
                child.doc.extraction_key = None
            elif not child.dirty:
                # A compressed sibling's bytes are a *decode* of the region that
                # just changed, so they cannot be refreshed in place: the block
                # is dropped and decompresses again from the new bytes when it
                # is next shown. One with unsaved edits keeps its document —
                # that is where they live.
                self.workspace.drop_document(child)
                if child is self._entry:
                    self._doc = self._load_document(child)
        # Any *other* entry reading these bytes — a font sheet, a file that
        # borrows this one as its second file — is now holding the old ones.
        for written_path in entry.paths:
            self.workspace.invalidate_path(written_path, keep=entry)
        verb = "Wrote" if side.saved == side.live else "Restored"
        self.statusBar().showMessage(
            f"{verb} {entry.name} ({len(written)} block(s))", 5000
        )
        self.files_panel.refresh_labels()
        self._refresh_view()

    def _recompress(self, block: Entry, payload: bytes) -> tuple[bytes, str | None]:
        """The bytes of a block's compressed slot, or why it cannot be written.

        Compressing, the slot's bound and the spare room a short result leaves
        are all the pipeline's
        (:func:`~mapchar.pipeline.pipeline.compress_for_slot`), so a block's
        write is held to the same rules as a file's. What is left here is
        reporting: a refusal is a problem to list beside the block, not an
        exception to escape a write of several.
        """
        parent_doc = block.parent.doc if block.parent is not None else None
        if parent_doc is None:
            return b"", f"{block.name}: its file is not loaded"
        ctx = block.doc.ctx if block.doc is not None else parent_doc.ctx.inherit()
        try:
            cfg = self._block_pathway(block, parent_doc)
            return compress_for_slot(payload, cfg, self.registry, ctx), None
        except MapcharError as exc:
            return b"", str(exc)

    def apply_bytes(
        self, entry: Entry, offset: int, data: bytes, revision: int
    ) -> None:
        """Splice bytes into a file entry's buffer, at the revision this half of
        the step leaves it at.

        ``revision`` is the command's captured token rather than a fresh one, so
        an undo back to what is on disk reads clean rather than staying unsaved
        for ever.
        """
        doc = self._load_document(entry)
        if doc is None:
            return
        # Every plain block reads this buffer; a compressed one reads its own
        # slot's payload, which the file's bytes do not reach until it is
        # decoded again.
        for shared in self._docs_sharing(entry):
            buf = bytearray(shared.data)
            buf[offset : offset + len(data)] = data
            shared.data = bytes(buf)
            shared.extraction_key = None
        self.workspace.stamp(entry, revision)
        self._reread_blocks_over(entry, offset, offset + len(data))
        self._refresh_view()

    def overtype_bytes(self, offset: int, data: bytes) -> None:
        file_entry = self._current_file()
        doc = self._doc
        if file_entry is None or doc is None or not data:
            return
        if offset + len(data) > doc.size:
            self._error("The bytes would run past the end of the file.")
            return
        before = doc.data[offset : offset + len(data)]
        if before == data:
            return
        self._push_command(BytesCommand(self, file_entry, offset, before, data))
