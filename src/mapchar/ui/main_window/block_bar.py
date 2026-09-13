"""Blocks and bookmarks: creating them, editing them, jumping to them."""

from __future__ import annotations

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    RangeSource,
    source_start,
)
from mapchar.core.context import KEY_SUGGESTED_MAPPING
from mapchar.core.table import TokenKind
from mapchar.plugins.base import Stage
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.dialogs import BlockDialog
from mapchar.ui.undo_commands import BlockEditCommand
from mapchar.ui.widgets import select_data


class BlockBarMixin:
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

    def _block_dialog(
        self, config: BlockConfig | None, name: str, entry: Entry | None = None
    ) -> BlockDialog:
        """The block dialog over every list it needs, prefilled for ``entry``.

        One builder for New Block…, Edit… and To Block, so none of the three can
        quietly offer a shorter form of a block than the others.
        """
        return BlockDialog(
            list(self.workspace.tables()),
            config,
            name,
            self,
            self.registry.ids(Stage.MAPPING),
            compression_items=self._plugin_items(Stage.COMPRESSION),
            compression_id=entry.compression_id if entry is not None else None,
            spare_room=entry.spare_room if entry is not None else "fill",
            suggested_mapping=self._suggested_mapping(),
        )

    def _suggested_mapping(self) -> str | None:
        """What the current file's container says the ROM is mapped as."""
        file_entry = self._current_file()
        doc = file_entry.doc if file_entry is not None else None
        if doc is None:
            return None
        value = doc.ctx.get(KEY_SUGGESTED_MAPPING)
        return str(value) if value else None

    def _new_block(self, start: int | None = None, stop: int | None = None) -> None:
        file_entry = self._current_file()
        if file_entry is None or self._doc is None:
            self._error("Open a ROM first.")
            return
        table_ids = list(self.workspace.tables())
        if not table_ids:
            self._error("Load a table first.")
            return
        if start is None:
            start, stop = (
                self._selection if self._selection else (self._offset, self._doc.size)
            )
        cfg = BlockConfig(
            RangeSource(start, stop or self._doc.size),
            EndToken(),
            self.table_pick.currentData() or table_ids[0],
        )
        dialog = self._block_dialog(cfg, f"Block {start:X}")
        if dialog.exec() != BlockDialog.DialogCode.Accepted:
            return
        compression_id = dialog.compression_id()
        entry = Entry(
            EntryKind.BLOCK,
            dialog.name.text().strip() or f"Block {start:X}",
            file_entry.path,
            parent=file_entry,
            config=dialog.config(),
            compression_id=compression_id,
            # A decompressed block is addressed by its compressed slot, which is
            # where the region the dialog was seeded from starts.
            slice_offset=start if compression_id else 0,
            spare_room=dialog.spare_room_rule(),
        )
        self._push_add(entry)
        self._activate_entry(entry)
        self.tabs.setCurrentIndex(1)

    def _edit_block(self) -> None:
        """A block's Edit…: re-point it, as one undo step, keeping its work.

        The translations are carried over by index rather than discarded: an
        edit says "read these bytes differently", not "throw away what I typed",
        and re-reading the region is how the edit takes effect.
        """
        entry = self._current_block()
        if entry is None:
            return
        if entry.dirty and not self._ask(
            "Edit block",
            f"{entry.name} has unsaved edits. Editing it re-reads the region; "
            "the translations are matched back onto it by index, but any that "
            "the new configuration has no string for are dropped. Continue?",
        ):
            return
        dialog = self._block_dialog(entry.config, entry.name, entry)
        if dialog.exec() != BlockDialog.DialogCode.Accepted:
            return
        before = (
            entry.name,
            entry.config,
            entry.compression_id,
            entry.spare_room,
        )
        after = (
            self._free_name(dialog.name.text().strip() or entry.name, entry),
            dialog.config(),
            dialog.compression_id(),
            dialog.spare_room_rule(),
        )
        if after == before:
            return  # OK'd unchanged: nothing happened, nothing to undo
        self._push_command(BlockEditCommand(self, entry, before, after))

    def apply_block_config(
        self, entry: Entry, name: str, config: BlockConfig, compression_id, spare_room
    ) -> None:
        """Re-point a block and read the region again — the application path for
        a block edit and its undo."""
        entry.name = name
        entry.config = config
        if compression_id != entry.compression_id:
            entry.slice_length = None  # a new scheme finds its own end
        entry.compression_id = compression_id
        entry.spare_room = spare_room
        # The one drop that keeps the translations, so the re-read matches them
        # back onto the new configuration by index.
        self.workspace.drop_document(entry)
        if entry is self._entry:
            self._doc = self._load_document(entry)
            self._restore_session()
        self.files_panel.refresh_labels()
        self._update_title()
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
            compression_id=self.compression_pick.currentData(),
        )
        entry.session.table_id = self.table_pick.currentData()
        entry.session.offset = self._offset
        entry.session.view = "strings" if self.tabs.currentIndex() == 1 else "raw"
        self._push_add(entry)

    def _jump_to_bookmark(self, entry: Entry) -> None:
        """Re-apply a bookmark's snapshot to its file and land on its offset."""
        if entry.parent is None:
            return
        self._activate_entry(entry.parent)
        if entry.session.table_id:
            self._choose_table(entry.session.table_id)
        select_data(self.compression_pick, entry.compression_id)
        self._go_to(entry.bookmark_offset)
        self.tabs.setCurrentIndex(1 if entry.session.view == "strings" else 0)

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

    def _jump_to_source(self, entry: Entry) -> None:
        """Files panel ▸ Jump to Source: the parent file at the block's offset,
        set up to read it — its start table picked and, for a decompressed
        block, its scheme armed in the Compression preview, since what sits at
        that address in the file is the packed structure."""
        if entry.parent is None or entry.config is None:
            return
        offset = self._block_file_offset(entry)
        self._activate_entry(entry.parent)
        if entry.config.table_id:
            self._choose_table(entry.config.table_id)
        select_data(self.compression_pick, entry.compression_id)
        self._go_to(offset)

    def _block_from_region(self, region) -> None:
        """A block over a scanned region, with its guessed terminator as end token."""
        tables = self._table_set()
        if tables is not None and region.terminator is not None:
            bits = format(region.terminator, "08b")
            start = tables.start
            if bits not in start.entries:
                from mapchar.core.table import Entry as TableEntry

                start.add(TableEntry(bits, TokenKind.END, "[end]"))
                table_entry = self.workspace.entry_for_table(start.id)
                if table_entry is not None:
                    self.workspace.stamp(table_entry)
                self._refresh_view()
        self._new_block(region.start, region.end)
