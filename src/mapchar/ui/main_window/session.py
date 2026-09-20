"""The active entry: activation, document loading, and session state."""

from __future__ import annotations

from mapchar.core.block import source_start
from mapchar.core.document import Document
from mapchar.core.errors import MapcharError
from mapchar.pipeline.pipeline import FileRef, PathwayConfig, SlotFill, load
from mapchar.project.workspace import Entry, EntryKind


class SessionMixin:
    """The active entry: activation, document loading, and session state.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _activate_entry(self, entry: Entry | None) -> None:
        """The single funnel for switching which entry is on screen.

        Capture the outgoing session, load the incoming document, restore the
        widgets, refresh once. Two kinds of entry never *become* the view and
        take their own route out: a table, which opens in the Table Editor, and
        a bookmark, which jumps the entry that owns its bytes. A folder is not
        even that: it leaves the view as it was, as a group heading does.
        Neither touches ``workspace.current``, because a row that cannot be the
        view must not
        claim to be it — everything that reads ``current`` (the undo commands'
        reach, the visit trail, the title) would then be pointing at a document
        that is not loaded.

        Re-activating the entry already on screen is a no-op. Several paths ask
        for it — an undo reaching its entry, a Files row clicked twice, a search
        result in the current block — and doing the work anyway would capture and
        restore the session over itself, losing an offset the user had just moved.
        """
        if entry is not None and entry.kind is EntryKind.TABLE:
            self._edit_table_entry(entry)
            return
        if entry is not None and entry.kind is EntryKind.BOOKMARK:
            self._jump_to_bookmark(entry)
            return
        if entry is not None and entry.kind is EntryKind.FOLDER:
            return  # a folder groups rows; like a group heading, it shows nothing
        if entry is self._entry and self.workspace.current is entry:
            return
        # The widgets still show the outgoing entry, so its session is written
        # back before the switch — not inside the reload below, which would
        # stamp the view it is leaving onto the entry arriving.
        self._capture_session()
        self._entry = entry
        self.workspace.set_current(entry)
        # A run of edits in one string ends when the entry does: two commands
        # either side of a switch are two steps, whatever cell they landed in.
        self._edit_run += 1
        self._reload_current_document(capture=False)

    def _reload_current_document(self, *, capture: bool = True) -> None:
        """Read the entry on screen again and put the view back where it was.

        Capture the live session, load the document, restore the widgets from
        the session, refresh once — the tail of :meth:`_activate_entry`, and what
        anything that drops the current document underneath the view has to do to
        put one back. The capture is what keeps the view where the user left it:
        the offset and the open tab live in the widgets, so skipping it re-reads
        the entry onto whatever its session last held. Only a switch passes
        ``capture=False``, having already written the *outgoing* entry's session
        back before it changed hands.
        """
        if capture:
            self._capture_session()
        self._doc = (
            self._load_document(self._entry) if self._entry is not None else None
        )
        self._restore_session()
        self._refresh_view()

    def _load_document(self, entry: Entry) -> Document | None:
        if entry.doc is not None:
            return entry.doc
        try:
            if entry.kind is EntryKind.FILE:
                cfg = PathwayConfig(
                    FileRef(entry.paths),
                    entry.container_id,
                    entry.compression_id,
                )
                loaded = load(cfg, self.registry)
                entry.doc = Document(
                    loaded.data,
                    loaded.ctx,
                    loaded.writable,
                    loaded.raw,
                    loaded.missing_plugins,
                )
                entry.missing = False
            elif entry.kind is EntryKind.BLOCK and entry.parent is not None:
                parent_doc = self._load_document(entry.parent)
                if parent_doc is None:
                    return None
                if entry.compression_id:
                    entry.doc = self._load_compressed_block(entry, parent_doc)
                else:
                    entry.doc = Document(
                        parent_doc.data, parent_doc.ctx, parent_doc.writable
                    )
        except (OSError, MapcharError) as exc:
            # A file that is not there reaches here as the container stage's
            # failure, since the pipeline funnels its own source read too. The
            # entry still has to be marked missing, which is what offers Locate,
            # so the cause is unwrapped rather than the label read.
            cause = exc if isinstance(exc, OSError) else exc.__cause__
            if isinstance(cause, OSError):
                entry.missing = True
                self._error(f"{entry.name}: {cause}")
            else:
                self._error(str(exc))
            return None
        self.files_panel.refresh_labels()
        return entry.doc

    def _block_pathway(self, entry: Entry, parent_doc: Document) -> PathwayConfig:
        """The pathway of one compressed block: its slot inside its parent.

        The parent's *buffer* is the source, not its file, so a block reads the
        unsaved edits made through the file's own view rather than the stale
        bytes on disk. A ``slice_length`` nobody recorded leaves the slot
        unbounded, which the pipeline reads as running to the end of that buffer.
        """
        # A compressed slot's tail takes the fill pattern's first byte.
        fill = entry.config.fill[0] if entry.config and entry.config.fill else 0xFF
        return PathwayConfig(
            FileRef(
                entry.paths or ((entry.parent.path,) if entry.parent else ()),
                data=parent_doc.data,
                offset=entry.slice_offset,
                length=entry.slice_length,
            ),
            compression_id=entry.compression_id,
            slot_fill=SlotFill.parse(entry.spare_room),
            fill_byte=fill,
            pathway=entry.name,
        )

    def _load_compressed_block(
        self, entry: Entry, parent_doc: Document
    ) -> Document | None:
        """Decompress a block's slot through the pipeline, on an inherited context.

        Inherited rather than fresh: the block's own decode publishes its own
        consumed size, but the header size and suggested mapping its parent's
        container found are facts about the file and its pointer mappings need
        them.
        """
        # Another block over the same slot already holds its payload, edits
        # included: the slot is one stream, so this block reads that rather
        # than decompressing the file's bytes underneath those edits. This
        # block is not among them — it is the one with no document yet.
        for other in self.workspace.entries_sharing(entry):
            # The payload carries the other block's unsaved edits, so this
            # block is exactly as unsaved as the one it took them from.
            self.workspace.set_revisions(
                entry, other.live_revision, other.saved_revision
            )
            return Document(other.doc.data, other.doc.ctx, other.doc.writable)
        cfg = self._block_pathway(entry, parent_doc)
        try:
            loaded = load(cfg, self.registry, parent_doc.ctx.inherit())
        except MapcharError as exc:
            self._error(f"cannot decompress {entry.name}: {exc}")
            return None
        doc = Document(loaded.data, loaded.ctx, parent_doc.writable and loaded.writable)
        doc.missing_plugins = list(parent_doc.missing_plugins) + loaded.missing_plugins
        if not loaded.writable and not loaded.missing_plugins:
            doc.missing_plugins.append(f"{entry.compression_id} (no compress)")
        return doc

    def _restore_session(self) -> None:
        entry = self._entry
        with self._bars_quiet():
            self._refresh_table_picks()
            if entry is None:
                self._offset = 0
                self._bounds = None
                return
            # What the preview is reading through is worked out from the
            # incoming entry's own pick, by the refresh that ends this.
            self._preview_scheme = None
            self._auto_armed = False
            # The structures found are one file's offsets.
            self._forget_structures()
            self._string_bounds = None
            self._load_reading_bar()
            self._offset = entry.session.offset
            # A block opens on its source, or on its strings when that is what
            # it was left reading: the view is confined to that stretch, and the
            # position it was left at is kept only while it is inside.
            self._bounds = None
            if entry.kind is EntryKind.BLOCK and entry.config is not None:
                self._bounds = self._source_bounds(entry)
                start = source_start(entry.config.source)
                # A block left reading its strings comes back on them, not on
                # the source it was made over.
                span = self._string_span(entry) if entry.session.string_view else None
                if span is not None:
                    self._string_bounds = self._bounds = span
                    start = span[0]
                    self._sync_view_mode()
                inside = self._bounds is None or (
                    self._bounds[0] <= self._offset < self._bounds[1]
                )
                if not entry.session.offset or not inside:
                    self._offset = 0 if start is None else start
            self._show_view(entry.session.view)

    def _capture_session(self) -> None:
        """Write the on-screen entry's live view back into its session.

        The offset and the open tab are part of what a save stores, so anything
        asking "what would be written" has to capture them first — otherwise an
        edited session reads clean until the next entry switch.
        """
        entry = self._entry
        if entry is None:
            return
        entry.session.offset = self._offset
        entry.session.view = self._current_view()
        # One string opened over the block is a visit, not its mode: what the
        # block is left reading is whichever mode lies under it.
        if not self._in_one_string():
            entry.session.string_view = self._in_strings_mode()
