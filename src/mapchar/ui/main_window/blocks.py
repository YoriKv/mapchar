"""Blocks and bookmarks: creating them, editing them, jumping to them."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NestedPointerSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    source_span,
    source_start,
    with_region,
)
from mapchar.core.context import KEY_SUGGESTED_MAPPING
from mapchar.core.table import TokenKind
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import BlockEditCommand, TableCommand

_BLOCK_EDIT_FIELDS = ("name", "config", "compression_id", "spare_room")
"""The four things a block is read by, in the order
:class:`~mapchar.ui.undo_commands.BlockEditCommand` carries them."""

_KIND_NAMES = {
    RangeSource: "Range",
    PointerTableSource: "Pointer table",
    PointerListSource: "Pointer list",
    NestedPointerSource: "Nested pointer tables",
    EndToken: "End token",
    FixedLength: "Fixed length",
    Pascal: "Pascal (length prefix)",
    NextPointer: "Next pointer",
    Lines: "Lines",
}
"""What the block bar calls a source or string type: the Block dialog's words
for it, never the class name."""


class BlocksMixin:
    """Blocks and bookmarks: creating them, editing them, jumping to them.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _current_file(self) -> Entry | None:
        e = self._entry
        if e is None:
            return None
        return e.parent if e.kind is EntryKind.BLOCK else e

    def _current_block(
        self,
        *,
        need_doc: bool = False,
        need_config: bool = False,
        need_tables: bool = False,
        complain: str | None = None,
    ) -> Entry | None:
        """The current entry when it is a block ready for what is asked of it.

        ``complain`` is the message shown when it is not; without one the
        caller just gets ``None``.
        """
        entry = self._entry
        ready = entry is not None and entry.kind is EntryKind.BLOCK
        if ready and need_doc and entry.doc is None:
            ready = False
        if ready and need_config and entry.config is None:
            ready = False
        if ready and need_tables and self._table_set() is None:
            ready = False
        if ready:
            return entry
        if complain is not None:
            self._error(complain)
        return None

    @staticmethod
    def _block_label(entry: Entry, count: int) -> str:
        """What the block bar says the block on screen is: its name, how it is
        read, and how many strings that came to. A block with no configuration
        has nothing to spell out but its name."""
        cfg = entry.config
        if cfg is None:
            return f"{entry.name}: no reading"
        return (
            f"{entry.name}: "
            f"{_KIND_NAMES.get(type(cfg.source), 'Source')} · "
            f"{_KIND_NAMES.get(type(cfg.string_type), 'Strings')} · "
            f"@{cfg.table_id or '-'} · "
            f"{count} {'string' if count == 1 else 'strings'}"
        )

    def _new_block(self, start: int | None = None, stop: int | None = None) -> None:
        """A block over ``start`` to ``stop`` — the selection, else the view
        onwards — read the way the bars read the view now."""
        file_entry = self._current_file()
        if file_entry is None or self._doc is None:
            self._error("Open a ROM first.")
            return
        if start is None:
            start, stop = (
                self._selection if self._selection else (self._offset, self._doc.size)
            )
        cfg = with_region(self._reading() or self._default_reading(), start, stop)
        if not cfg.table_id:
            cfg = replace(cfg, table_id=self._default_table_id())
        entry = Entry(
            EntryKind.BLOCK,
            f"Block {start:X}",
            file_entry.path,
            parent=file_entry,
            config=cfg,
        )
        entry.session.resolve_pointers = self.resolve_pointers.isChecked()
        self._push_add(entry)
        self._activate_entry(entry)
        self._show_view("strings")

    def _suggested_mapping(self) -> str | None:
        """What the current file's container says the ROM is mapped as."""
        file_entry = self._current_file()
        doc = file_entry.doc if file_entry is not None else None
        if doc is None:
            return None
        value = doc.ctx.get(KEY_SUGGESTED_MAPPING)
        return str(value) if value else None

    def _default_reading(self) -> BlockConfig:
        return BlockConfig(RangeSource(0, 0), EndToken(), self._default_table_id())

    def _push_block_edit(self, entry: Entry, *, field: str | None = None, **changes):
        """One block edit: the four things a block is read by as they are, with
        only what ``changes`` names different. Nothing is pushed when nothing
        moved. ``field`` names the bar control it came from, so a run on one
        merges into a single step."""
        unknown = set(changes) - set(_BLOCK_EDIT_FIELDS)
        if unknown:
            raise TypeError(f"not part of a block edit: {', '.join(sorted(unknown))}")
        before = tuple(getattr(entry, name) for name in _BLOCK_EDIT_FIELDS)
        after = tuple(
            changes.get(name, value)
            for name, value in zip(_BLOCK_EDIT_FIELDS, before, strict=True)
        )
        if after == before or self._applying_undo:
            return
        scheme = after[_BLOCK_EDIT_FIELDS.index("compression_id")]
        if not self._confirm_payload_loss(entry, scheme):
            return
        self._push_command(BlockEditCommand(self, entry, before, after, field))

    def _confirm_payload_loss(self, entry: Entry, compression_id: str | None) -> bool:
        """Ask before a block edit throws a compressed block's edits away.

        A compressed block's translations are in the payload its scheme
        decodes, and nowhere else. Every other edit re-reads the new
        configuration over those same bytes and keeps them; a change of scheme
        leaves nothing that can decode them, so it is the one block edit that
        has to ask first.
        """
        if compression_id == entry.compression_id:
            return True
        if not (entry.dirty and entry.compression_id and entry.doc is not None):
            return True
        return self._ask(
            "Edit Block",
            f"{entry.name} has unsaved edits in its decompressed bytes, and "
            "changing its compression reads the slot again and discards them. "
            "Continue?",
        )

    def apply_block_config(
        self, entry: Entry, name: str, config: BlockConfig, compression_id, spare_room
    ) -> None:
        """Re-point a block and read the region again — the application path for
        a block edit and its undo."""
        entry.name = name
        scheme = entry.compression_id
        # A compressed block's translations are in its payload and nowhere else,
        # so a re-reading keeps those bytes and reads the new configuration over
        # them. Only a change of scheme cannot: what the old scheme decoded is
        # not what the new one would, so the slot is decoded afresh.
        keep_payload = (
            bool(scheme) and compression_id == scheme and entry.doc is not None
        )
        if compression_id != scheme:
            entry.slice_length = None  # a new scheme finds its own end
            if scheme is None:
                # Read from the file until now, so its slot is where it began.
                entry.slice_offset = self._block_file_offset(entry)
        entry.config = config
        entry.compression_id = compression_id
        entry.spare_room = spare_room
        # The re-read matches originals, statuses and notes back by index —
        # except onto a string the new reading cuts at other bits, which takes
        # its original afresh, as a string the old reading never had.
        if keep_payload:
            entry.stash_strings(reconfigured=True)
            entry.doc.strings = []
            entry.doc.extraction_key = None
        else:
            was_dirty = entry.dirty and bool(scheme) and entry.doc is not None
            self.workspace.drop_document(entry, reconfigured=True)
            if was_dirty:
                # Its payload held the only copy of its edits and nothing can
                # decode it now, so the block is no longer unsaved: there is
                # nothing left for a write to put back. _push_block_edit asked
                # before it came to this.
                self.workspace.mark_saved(entry)
        self.files_panel.refresh_labels()
        self._update_title()
        if entry is self._entry:
            self._reload_current_document()
        else:
            self._refresh_view()

    def _new_bookmark(self) -> None:
        """File ▸ New Bookmark: the view position plus the settings it is read
        under, so jumping back shows the same thing and not just the same bytes."""
        file_entry = self._current_file()
        if file_entry is None:
            return
        entry = Entry(
            EntryKind.BOOKMARK,
            f"Bookmark {self._offset:X}",
            file_entry.path,
            parent=file_entry,
            bookmark_offset=self._offset,
            compression_id=self._preview_scheme,
        )
        entry.session.table_id = self._current_table_id()
        entry.session.config = self._reading()
        entry.session.resolve_pointers = self.resolve_pointers.isChecked()
        entry.session.offset = self._offset
        entry.session.view = self._current_view()
        self._push_add(entry)

    def _jump_to_bookmark(self, entry: Entry) -> None:
        """Re-apply a bookmark's snapshot to its file and land on its offset."""
        if entry.parent is None:
            return
        self._read_file_as(entry.parent, entry.session.config, entry.session)
        self._preview_scheme = entry.compression_id
        self._go_to(entry.bookmark_offset)
        self._show_view(entry.session.view)

    def _read_file_as(self, file_entry: Entry, config, session) -> None:
        """Put ``file_entry`` on screen read by ``config`` — a bookmark's
        snapshot, a block's own reading — with ``session``'s Resolve pointers."""
        if config is not None:
            file_entry.session.config = config
            file_entry.session.table_id = config.table_id or None
            file_entry.session.resolve_pointers = session.resolve_pointers
        elif session.table_id:
            file_entry.session.config = replace(
                self._reading(file_entry), table_id=session.table_id
            )
            file_entry.session.table_id = session.table_id
        if file_entry is self._entry:
            self._restore_session()
            self._refresh_view()
        else:
            self._activate_entry(file_entry)

    def _block_file_offset(self, entry: Entry) -> int:
        """Where a block's bytes sit in its parent file.

        Not ``source.start``: a compressed block's source addresses its own
        decompressed payload, and a pointer list has no ``start`` at all. Each
        source answers in the file's own coordinates or the strings do.
        """
        if entry.compression_id:
            return entry.slice_offset
        start = source_start(entry.config.source if entry.config is not None else None)
        if start is not None:
            return start
        strings = entry.doc.strings if entry.doc is not None else []
        return strings[0].start if strings else 0

    def _source_bounds(self, entry: Entry) -> tuple[int, int] | None:
        """The bytes a block's view is confined to: its source, held inside
        its document — a range or fixed source's bytes, a pointer table, the
        stretch a pointer list's pointers lie in — or ``None`` when the source
        names none, which leaves the whole document in view."""
        span = source_span(entry.config.source if entry.config is not None else None)
        doc = self._doc if entry is self._entry else entry.doc
        if span is not None and doc is not None:
            span = (max(0, span[0]), min(span[1], doc.size))
        return span if span is not None and span[1] > span[0] else None

    def _view_source(self, entry: Entry) -> None:
        """Confine the view to the block's source, from its start — what a
        click on its Files row does, whether or not it was already on screen."""
        if entry is not self._entry:
            return
        bounds = self._source_bounds(entry)
        self._set_bounds(bounds)
        if bounds is not None:
            self._go_to(bounds[0])

    def _string_span(self, entry: Entry) -> tuple[int, int] | None:
        """The bytes a block's strings lie in, from the lowest-placed to the
        end of the highest, or ``None`` when it has none. The pointers may
        reach them in any order, share one, or leave bytes between them; that
        stretch holds those bytes too."""
        doc = self._doc if entry is self._entry else entry.doc
        strings = entry.doc.strings if entry.doc is not None else []
        if doc is None or not strings:
            return None
        start = max(0, min(rec.start for rec in strings))
        end = min(max(rec.end for rec in strings), doc.size)
        return (start, end) if end > start else None

    def _view_strings(self, entry: Entry) -> None:
        """The Strings mode on a pointer block: the view confined to its
        strings, read as text."""
        span = self._string_span(entry) if entry is self._entry else None
        if span is None:
            self._sync_view_mode()
            return
        self._string_bounds = span
        self._set_bounds(span)
        self._go_to(span[0])

    def _show_string(self, entry: Entry, index: int) -> None:
        """A string clicked under its block in the Files panel: the block on
        screen, the view confined to that string's bytes, and the string
        selected in the Strings grid, with no bytes selected in the view. The
        string's bytes are text, so a pointer block's view reads them as such
        for as long as it is confined to them.

        One string is a visit of its own, and the block it is under is not: the
        click is one gesture, so Back takes one step to leave the string for
        wherever the view was before it.
        """
        walking, self._history_walking = self._history_walking, True
        try:
            self._activate_entry(entry)
        finally:
            self._history_walking = walking
        rec = self._string(entry, index)
        if entry is not self._entry or rec is None or self._doc is None:
            return
        # The mode the string is opened over is what leaving it comes back to.
        if not self._in_one_string():
            entry.session.string_view = self._in_strings_mode()
        self._record_visit(entry, index)
        self._string_bounds = (max(0, rec.start), min(rec.end, self._doc.size))
        self._set_bounds((rec.start, rec.end))
        self.strings.select_index(index)
        self._on_string_row(index)
        # The view is already that string alone, so nothing in it is selected.
        self.raw.set_selection(0, 0)
        self._on_selection(0, 0)
        self.files_panel.select_string(entry, index)

    def _leave_string(self, entry: Entry) -> None:
        """Back out of one string of the block on screen to what it reads apart
        from that string: its source, or all its strings in the Strings mode —
        what Back does from a string to the block it is under, which is already
        on screen and so cannot be activated into place."""
        if entry is not self._entry or not self._in_one_string():
            return
        if entry.session.string_view:
            self._view_strings(entry)
        else:
            self._view_source(entry)
        self.files_panel.select_entry(entry)

    def _read_block_strings(self, entry: Entry) -> None:
        """The Files panel opened a block the session has not read: read it, so
        its strings can be listed without making it the view."""
        if entry.doc is None and entry.config is not None:
            self._block_strings(entry)

    def _jump_to_source(self, entry: Entry) -> None:
        """Files panel ▸ Jump to Source: the parent file at the block's offset,
        set up to read it — read the way the block reads and, for a decompressed
        block, its scheme armed in the Compression preview, since what sits at
        that address in the file is the packed structure."""
        if entry.parent is None or entry.config is None:
            return
        offset = self._block_file_offset(entry)
        self._read_file_as(entry.parent, entry.config, entry.session)
        self._preview_scheme = entry.compression_id
        self._go_to(offset)

    def _block_from_region(self, region) -> None:
        """A block over a scanned region, with its guessed terminator as end
        token — the two together, so undoing the block takes the token with it."""
        tables = self._table_set()
        # An encoding is nobody's to edit: its strings end where it says.
        table_entry = (
            self.workspace.entry_for_table(tables.start.id) if tables else None
        )
        with self._macro("Block from region"):
            if table_entry is not None and region.terminator is not None:
                bits = format(region.terminator, "08b")
                start = table_entry.table
                # A table that already spells [end] on other bits keeps it;
                # only a free key and a free label take the guess.
                if (
                    start is not None
                    and bits not in start.entries
                    and "end" not in start.labels
                ):
                    from mapchar.core.table import Entry as TableEntry

                    after = deepcopy(start)
                    after.add(TableEntry(bits, TokenKind.END, "[end]"))
                    self._push_command(
                        TableCommand(self, table_entry, deepcopy(start), after)
                    )
            self._new_block(region.start, region.end)
