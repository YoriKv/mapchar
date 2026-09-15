"""The Format and Reading bars: the table or encoding, whether the bytes are
strings or pointers, and how they are cut, all applied as they change."""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.core.errors import MapcharError
from mapchar.core.table import TableSet
from mapchar.plugins.base import Stage
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.undo_commands import BlockEditCommand
from mapchar.ui.widgets import fill_pick, select_data

DEFAULT_ENCODING = "ascii"
"""What a file with no table of its own is read as."""


class FormatBarMixin:
    """The Format and Reading bars: the table or encoding, strings or pointers,
    and how the bytes are cut.

    The bars show the reading of the entry on screen — a block's configuration,
    or a file's session — and every change is applied as it is made: a block's
    as an undo step that re-reads its strings, a file's to its session. Either
    way the views read again, so what the settings do is on screen while they
    are set.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _scheme(self):
        """The compression plugin the Decompressed view previews through."""
        cid = self._preview_scheme
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
        pick = self.format_pick
        was = pick.blockSignals(True)
        fill_pick(pick, self._table_items())
        pick.add_command_row()
        self._select_reading()
        pick.blockSignals(was)
        self.tables_panel.set_start_table(self._current_table_id())

    def _select_reading(self) -> None:
        """Show the reading's format and mode, without applying anything."""
        cfg = self._reading()
        if cfg is None:
            # Nothing on screen has a reading, so the bar shows the default one
            # rather than every control the last entry needed.
            self.reading_bar.show_default()
            self._sync_mode(False)
            return
        table_id, pick = cfg.table_id, self.format_pick
        was = pick.blockSignals(True)
        if not select_data(pick, table_id):
            # A table the reading names but nobody loaded is still what it
            # names: said so, rather than shown as some other table.
            at = pick.count() - 2
            label = f"@{table_id}  (not loaded)" if table_id else "(no table)"
            pick.insertItem(at, label, table_id or "")
            pick.setCurrentIndex(at)
        pick.blockSignals(was)
        self._sync_mode(cfg.has_pointers)

    def _sync_mode(self, pointers: bool) -> None:
        """Show the mode, and the controls it uses. A block's mode is what it
        was made with, so on a block the toggle only picks what the view shows:
        a pointer block's table or its strings, and nothing but strings for any
        other block. Strings' bytes are text, so a view of them shows Strings
        with only the settings that shape a string."""
        entry = self._entry
        block = entry is not None and entry.kind is EntryKind.BLOCK
        string_view = self._in_string_view()
        shown = pointers and not string_view
        self.mode_toggle.set_value(shown)
        self.mode_toggle.button(True).setEnabled(pointers or not block)
        self.reading_bar.show_string_view(string_view)
        self.resolve_group.setVisible(shown)

    def _sync_view_mode(self) -> None:
        """Show the mode again after the view's bounds changed."""
        cfg = self._reading()
        self._sync_mode(cfg is not None and cfg.has_pointers)

    def _in_string_view(self) -> bool:
        """Whether the view is confined to exactly the strings last opened as
        text: one string, or all of a pointer block's."""
        return self._string_bounds is not None and self._bounds == self._string_bounds

    def _in_strings_mode(self) -> bool:
        """Whether the view is a pointer block's Strings mode: confined to all
        of its strings, read as text."""
        entry = self._entry
        return (
            entry is not None
            and entry.kind is EntryKind.BLOCK
            and self._in_string_view()
            and self._string_bounds == self._string_span(entry)
        )

    def _in_one_string(self) -> bool:
        """Whether the view is one string opened from the Files panel — a visit
        of its own, laid over whichever mode the block is in."""
        return self._in_string_view() and not self._in_strings_mode()

    def _default_table_id(self) -> str:
        loaded = self.workspace.loaded_tables()
        if loaded:
            return next(iter(loaded))
        builtin = self.workspace.builtin_tables
        if DEFAULT_ENCODING in builtin:
            return DEFAULT_ENCODING
        return next(iter(builtin), "")

    def _choose_table(self, table_id: str) -> None:
        """Make ``table_id`` the table the reading decodes through."""
        select_data(self.format_pick, table_id)

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
        """Whether the Hex and Text tabs show pointers: the reading has them,
        and the view is not strings' bytes, which are text."""
        if self._in_string_view():
            return False
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
        was = self.resolve_pointers.blockSignals(True)
        self.resolve_pointers.setChecked(entry.session.resolve_pointers)
        self.resolve_pointers.blockSignals(was)
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
            pickers = (self.format_pick, self.resolve_pointers)
            blocked = [p.blockSignals(True) for p in pickers]
            try:
                self._load_reading_bar()
            finally:
                for picker, was in zip(pickers, blocked, strict=True):
                    picker.blockSignals(was)

    def _on_format_pick(self) -> None:
        self._on_reading_edited("table")

    def _on_mode(self, pointers: bool) -> None:
        """Strings or pointers: the source turns to the other kind, keeping
        every other setting, and the table reads the strings either way.

        One mode cannot hold the other's source, so the reading switched away
        from is set aside on the entry, and switching back takes up its source
        and string type again rather than rebuilding them from the bar.

        A block keeps its reading: on a pointer block the mode only shows its
        pointer table or its strings.
        """
        entry, base = self._entry, self._reading()
        if entry is not None and entry.kind is EntryKind.BLOCK and base is not None:
            if not base.has_pointers:
                self._sync_mode(False)
            elif pointers:
                self._view_source(entry)
            else:
                self._view_strings(entry)
            return
        if entry is None or base is None or base.has_pointers == pointers:
            self._sync_mode(base is not None and base.has_pointers)
            return
        back = entry.session.set_aside
        entry.session.set_aside = base
        if back is not None and back.has_pointers == pointers:
            self.reading_bar.load(
                replace(base, source=back.source, string_type=back.string_type),
                block=False,
                spare_room=entry.spare_room,
            )
        else:
            self.reading_bar.set_pointers(pointers)
        self._sync_mode(pointers)
        self._on_reading_edited("mode")

    def _on_resolve_pointers(self, resolved: bool) -> None:
        if self._entry is not None:
            self._entry.session.resolve_pointers = resolved
        self._refresh_view(moved=True)

    def _on_reading_edited(self, field: str) -> None:
        """Apply what the bars now say to the entry on screen."""
        entry = self._entry
        base = self._reading()
        if entry is None or base is None:
            return
        cfg = self.reading_bar.config(base, self.format_pick.currentData() or "")
        if entry.kind is EntryKind.BLOCK:
            before = (entry.name, entry.config, entry.compression_id, entry.spare_room)
            after = (
                entry.name,
                cfg,
                entry.compression_id,
                self.reading_bar.spare_room_rule(),
            )
            if after != before:
                self._push_command(BlockEditCommand(self, entry, before, after, field))
        elif cfg != base or entry.session.config is None:
            entry.session.config = cfg
            entry.session.table_id = cfg.table_id
            self._refresh_view()
        self.tables_panel.set_start_table(self._current_table_id())

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
