"""Laying strings out and writing them back to disk."""

from __future__ import annotations

from mapchar.core.block import Status
from mapchar.core.errors import MapcharError
from mapchar.pipeline.insert import Splice, apply_splices, layout_block
from mapchar.pipeline.pipeline import FileRef, PathwayConfig, compress_for_slot, save
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.dialogs import TextDialog
from mapchar.ui.undo_commands import BytesCommand


class WritingMixin:
    """Laying strings out and writing them back to disk.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _write_entry(self, entry: Entry) -> None:
        """The Files panel's Write: one entry, or an explanation why not.

        A bookmark and a table are not the same kind of "cannot": a bookmark has
        no bytes of its own at all, and a table is written with Save As Native
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
                "Save As Native…, not through the ROM's write path."
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
            self._write_blocks([entry])
            return
        blocks = [b for b in self.workspace.children(entry) if b.dirty]
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
        self._extract_current(entry, doc, self._table_set_for(entry.config.table_id))
        return doc.strings

    def _dirty_blocks(self) -> list[Entry]:
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
        """Lay every block out over its file and write the files that changed."""
        by_file: dict[int, tuple[Entry, list[Entry]]] = {}
        for b in blocks:
            if b.parent is None:
                continue
            by_file.setdefault(id(b.parent), (b.parent, []))[1].append(b)
        for f in files or []:
            by_file.setdefault(id(f), (f, []))
        ok = True
        for file_entry, file_blocks in by_file.values():
            parent_doc = self._load_document(file_entry)
            if parent_doc is None:
                ok = False
                continue
            new_data = parent_doc.data
            problems: list[str] = []
            written_blocks = []
            # Several blocks can sit over one compressed slot, and the slot holds
            # one stream: each is laid out into the *same* decompressed buffer and
            # the buffer is compressed once. Recompressing per block would have
            # the last splice at the slot's offset replace every earlier one, so
            # one of two blocks written together would silently lose its edits.
            payloads: dict[tuple[str, int], bytes] = {}
            members: dict[tuple[str, int], list[Entry]] = {}
            for block in file_blocks:
                doc = self._load_document(block)
                if doc is None or block.config is None:
                    continue
                ts = self._table_set_for(block.config.table_id)
                if ts is None:
                    problems.append(
                        f"{block.name}: table @{block.config.table_id} is not loaded"
                        " or does not build"
                    )
                    continue
                self._extract_current(block, doc, ts)
                slot = (block.compression_id or "", block.slice_offset)
                if block.compression_id:
                    base = payloads.setdefault(slot, doc.data)
                else:
                    base = new_data
                res = layout_block(base, block.config, ts, doc.strings, self.registry)
                if not res.ok:
                    for p in res.problems:
                        problems.append(f"{block.name} #{p.index}: {p.message}")
                    continue
                if block.compression_id:
                    payloads[slot] = apply_splices(base, res.splices)
                    members.setdefault(slot, []).append(block)
                else:
                    new_data = apply_splices(new_data, res.splices)
                    written_blocks.append(block)
            for slot, payload in payloads.items():
                sharing = members.get(slot)
                if not sharing:
                    continue
                # The slot's bounds and spare-room rule are one slot's, so the
                # first block over it speaks for all of them.
                packed, problem = self._recompress(sharing[0], payload)
                if problem:
                    problems.append(problem)
                    continue
                new_data = apply_splices(new_data, [Splice(slot[1], packed)])
                for block in sharing:
                    if block.doc is not None:
                        block.doc.pending_payload = payload
                    written_blocks.append(block)
            if problems:
                TextDialog("Cannot Write", "\n".join(problems), self).exec()
                ok = False
                continue
            if not parent_doc.writable:
                self._error(
                    f"{file_entry.name} is view-only (a stage cannot write back)."
                )
                ok = False
                continue
            cfg = PathwayConfig(
                FileRef(file_entry.paths),
                file_entry.container_id,
                file_entry.compression_id,
                pathway=file_entry.name,
            )
            try:
                save(new_data, cfg, self.registry, parent_doc.ctx)
            except (OSError, MapcharError) as exc:
                self._error(f"Cannot write {file_entry.name}: {exc}")
                ok = False
                continue
            parent_doc.data = new_data
            self.workspace.mark_saved(file_entry)
            for block in written_blocks:
                doc = block.doc
                if doc is not None:
                    pending = doc.pending_payload
                    if block.compression_id and pending is not None:
                        doc.data = pending
                    else:
                        doc.data = new_data
                    doc.extraction_key = None
                    for rec in doc.strings:
                        if rec.translation is not None:
                            rec.translation = None
                            rec.status = Status.UNTOUCHED
                self.workspace.mark_saved(block)
            for child in self.workspace.children(file_entry):
                if child.doc is None or child in written_blocks:
                    continue
                if not child.compression_id:
                    child.doc.data = new_data
                    child.doc.extraction_key = None
                elif not child.dirty:
                    # A compressed sibling's bytes are a *decode* of the region
                    # that just changed, so they cannot be refreshed in place:
                    # the block is dropped and decompresses again from the new
                    # bytes when it is next shown. One with unsaved edits keeps
                    # its document — that is where they live.
                    self.workspace.drop_document(child)
                    if child is self._entry:
                        self._doc = self._load_document(child)
            # Any *other* entry reading these bytes — a font sheet, a file that
            # borrows this one as its second file — is now holding the old ones.
            for written_path in file_entry.paths:
                self.workspace.invalidate_path(written_path, keep=file_entry)
            self.statusBar().showMessage(
                f"Wrote {file_entry.name} ({len(written_blocks)} block(s))", 5000
            )
        self.files_panel.refresh_labels()
        self._refresh_view()
        return ok

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
        buf = bytearray(doc.data)
        buf[offset : offset + len(data)] = data
        doc.data = bytes(buf)
        self.workspace.stamp(entry, revision)
        for child in self.workspace.children(entry):
            if child.doc is not None:
                child.doc.data = doc.data
                child.doc.extraction_key = None
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
