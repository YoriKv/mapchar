"""The Codecs and Reading bars: container, compression, table and how the bytes
are cut into strings, all applied as they change."""

from __future__ import annotations

from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.core.errors import MapcharError
from mapchar.core.table import TableSet
from mapchar.plugins.base import Stage
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import BlockEditCommand
from mapchar.ui.widgets import fill_pick, select_data

POINTER = "\x00pointer"
"""The Table list's datum for **Pointer**: read the bytes as pointers. No table
id can carry it."""

DEFAULT_ENCODING = "ascii"
"""What a file with no table of its own is read as."""


class CodecsBarMixin:
    """The Codecs and Reading bars: container, compression, table and how the
    bytes are cut into strings.

    The bars show the reading of the entry on screen — a block's configuration,
    or a file's session — and every change is applied as it is made: a block's
    as an undo step that re-reads its strings, a file's to its session. Either
    way the views read again, so what the settings do is on screen while they
    are set.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _fill_container_pick(self) -> None:
        fill_pick(self.container_pick, self._plugin_items(Stage.CONTAINER), None, False)

    def _fill_compression_pick(self) -> None:
        fill_pick(self.compression_pick, self._plugin_items(Stage.COMPRESSION), "None")

    def _plugin_items(self, stage: Stage) -> list[tuple[str, object]]:
        return [(p.info.name, p.info.id) for p in self.registry.plugins(stage)]

    def _scheme(self):
        """The compression plugin the toolbar has picked, bound to the buffer."""
        cid = self.compression_pick.currentData()
        return self.registry.plugin(Stage.COMPRESSION, cid) if cid else None

    # -- the tables on offer ----------------------------------------------------

    def _reset_builtin_tables(self) -> None:
        """The registry's charsets as tables under the loaded ones; after the
        registry changes, so a charset plugin's arrival shows in the lists."""
        from mapchar.plugins.charsets import CharsetTables

        self.workspace.builtin_tables = CharsetTables(self.registry)
        self.workspace.invalidate_extractions()

    def _table_items(self) -> list[tuple[str, str]]:
        """Every table a reading can pick: the loaded ones with their entry
        counts, then the encodings no loaded table shadows."""
        loaded = self.workspace.loaded_tables()
        items = [(f"@{tid}  ({len(t.entries)})", tid) for tid, t in loaded.items()]
        builtin = self.workspace.builtin_tables
        names = builtin.names() if hasattr(builtin, "names") else []
        items += [(name, cid) for cid, name in names if cid not in loaded]
        return items

    def _refresh_table_picks(self) -> None:
        items = self._table_items()
        pick = self.table_pick
        was = pick.blockSignals(True)
        fill_pick(pick, [("Pointer", POINTER)], None)
        pick.insertSeparator(pick.count())
        for label, data in items:
            pick.addItem(label, data)
        pick.add_command_row()
        fill_pick(self.strings_pick, items)
        self._select_reading()
        pick.blockSignals(was)
        self.tables_panel.set_start_table(self._current_table_id())

    def _select_reading(self) -> None:
        """Show the reading's table in both lists, without applying anything."""
        cfg = self._reading()
        if cfg is None:
            self._sync_pointer_row(False)
            return
        table_id, pointers = cfg.table_id, cfg.has_pointers
        for combo, data in (
            (self.table_pick, POINTER if pointers else table_id),
            (self.strings_pick, table_id),
        ):
            was = combo.blockSignals(True)
            if not select_data(combo, data):
                # A table the reading names but nobody loaded is still what it
                # names: said so, rather than shown as some other table.
                at = combo.count() - 2 if combo is self.table_pick else combo.count()
                label = f"@{data}  (not loaded)" if data else "(no table)"
                combo.insertItem(at, label, data or "")
                combo.setCurrentIndex(at)
            combo.blockSignals(was)
        self._sync_pointer_row(pointers)

    def _sync_pointer_row(self, pointers: bool) -> None:
        for group in self.pointer_groups:
            group.setVisible(pointers)

    def _default_table_id(self) -> str:
        loaded = self.workspace.loaded_tables()
        if loaded:
            return next(iter(loaded))
        builtin = self.workspace.builtin_tables
        if DEFAULT_ENCODING in builtin:
            return DEFAULT_ENCODING
        return next(iter(builtin), "")

    def _choose_table(self, table_id: str) -> None:
        """Make ``table_id`` the table the reading decodes through: the Table
        list's pick, or while that is Pointer, the strings'."""
        if self.table_pick.currentData() == POINTER:
            select_data(self.strings_pick, table_id)
        else:
            select_data(self.table_pick, table_id)

    # -- the reading ------------------------------------------------------------

    def _reading(self, entry: Entry | None = None) -> BlockConfig | None:
        """How ``entry`` (the one on screen by default) is read: a block's own
        configuration, a file's session, or ``None`` for anything else."""
        entry = self._entry if entry is None else entry
        if entry is None:
            return None
        if entry.kind is EntryKind.BLOCK:
            return entry.config
        if entry.kind is not EntryKind.FILE:
            return None
        if entry.session.config is not None:
            return entry.session.config
        return BlockConfig(
            RangeSource(0, 0),
            EndToken(),
            entry.session.table_id or self._default_table_id(),
        )

    def _reads_pointers(self) -> bool:
        cfg = self._reading()
        return cfg is not None and cfg.has_pointers

    def _current_table_id(self) -> str | None:
        """The table the entry on screen decodes its text through."""
        cfg = self._reading()
        return cfg.table_id or None if cfg is not None else None

    def _load_reading_bar(self) -> None:
        """Show the entry on screen's reading in both bars."""
        entry = self._entry
        cfg = self._reading()
        if cfg is None:
            return
        block = entry.kind is EntryKind.BLOCK
        self._bars_show = (entry, cfg, entry.compression_id, entry.spare_room)
        self.reading_bar.load(
            cfg,
            block=block,
            spare_room=entry.spare_room,
            compressed=block and bool(entry.compression_id),
        )
        # The container publishes what the header says the ROM is mapped as; a
        # reading turned to pointers starts on that rather than on linear, which
        # is wrong for every banked ROM.
        suggested = self._suggested_mapping()
        if suggested and not cfg.has_pointers:
            self.reading_bar.suggest_mapping(suggested)
        was = self.show_strings.blockSignals(True)
        self.show_strings.setChecked(entry.session.show_strings)
        self.show_strings.blockSignals(was)
        if block:
            was = self.compression_pick.blockSignals(True)
            select_data(self.compression_pick, entry.compression_id)
            self.compression_pick.blockSignals(was)
        self._select_reading()

    def _sync_bars(self) -> None:
        """Show the reading again if something besides the bars changed it — an
        import, a pointer search, an undo — so the next edit in them starts from
        what the entry holds rather than writing back what they showed before."""
        entry = self._entry
        cfg = self._reading()
        if cfg is None:
            return
        if self._bars_show != (entry, cfg, entry.compression_id, entry.spare_room):
            pickers = (self.table_pick, self.strings_pick, self.compression_pick)
            blocked = [p.blockSignals(True) for p in pickers]
            try:
                self._load_reading_bar()
            finally:
                for picker, was in zip(pickers, blocked, strict=True):
                    picker.blockSignals(was)

    def _on_table_pick(self) -> None:
        pointers = self.table_pick.currentData() == POINTER
        self.reading_bar.set_pointers(pointers)
        self._sync_pointer_row(pointers)
        if not pointers:
            # Kept in step, so switching to Pointer reads the strings through
            # the table that was just in use.
            was = self.strings_pick.blockSignals(True)
            select_data(self.strings_pick, self.table_pick.currentData())
            self.strings_pick.blockSignals(was)
        self._on_reading_edited("table")

    def _on_show_strings(self, shown: bool) -> None:
        if self._entry is not None:
            self._entry.session.show_strings = shown
        self._refresh_view()

    def _on_compression_pick(self) -> None:
        """A file's pick previews a scheme; a block's is the scheme it is read
        through, so on a block it is a change to the block."""
        entry = self._entry
        if entry is not None and entry.kind is EntryKind.BLOCK:
            self._on_reading_edited("compression")
        else:
            self._refresh_view()

    def _on_reading_edited(self, field: str) -> None:
        """Apply what the bars now say to the entry on screen."""
        entry = self._entry
        base = self._reading()
        if entry is None or base is None:
            return
        pointers = self.table_pick.currentData() == POINTER
        pick = self.strings_pick if pointers else self.table_pick
        cfg = self.reading_bar.config(base, pick.currentData() or "")
        if entry.kind is EntryKind.BLOCK:
            before = (entry.name, entry.config, entry.compression_id, entry.spare_room)
            after = (
                entry.name,
                cfg,
                self.compression_pick.currentData(),
                self.reading_bar.spare_room_rule(),
            )
            if after != before:
                self._push_command(BlockEditCommand(self, entry, before, after, field))
        elif cfg != base or entry.session.config is None:
            entry.session.config = cfg
            entry.session.table_id = cfg.table_id
            self._refresh_view()
        self.tables_panel.set_start_table(self._current_table_id())

    def _on_chain_changed(self) -> None:
        entry = self._entry
        if entry is None:
            return
        file_entry = entry.parent if entry.kind is EntryKind.BLOCK else entry
        if file_entry is None or file_entry.kind is not EntryKind.FILE:
            return
        file_entry.container_id = self.container_pick.currentData() or "raw"
        # Every block under the file is re-read against the new unwrapping, so
        # each drop goes through the workspace and keeps its translations.
        self.workspace.drop_document(file_entry)
        for child in self.workspace.children(file_entry):
            self.workspace.drop_document(child)
        self._doc = self._load_document(entry)
        self._refresh_view()

    def _table_set(self) -> TableSet | None:
        """The set the reading's table heads."""
        return self._table_set_for(self._current_table_id())

    def _table_set_for(self, table_id: str | None) -> TableSet | None:
        """The set ``table_id`` heads, or ``None`` when it is not loaded."""
        tables = self.workspace.tables()
        if not table_id or table_id not in tables:
            return None
        try:
            return TableSet.build(tables[table_id], tables)
        except MapcharError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return None
