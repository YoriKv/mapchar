"""The Codecs bar: container, compression and start table."""

from __future__ import annotations

from mapchar.core.errors import MapcharError
from mapchar.core.table import TableSet
from mapchar.plugins.base import Stage
from mapchar.project.workspace import EntryKind
from mapchar.ui.widgets import fill_pick, select_data


class CodecsBarMixin:
    """The Codecs bar: container, compression and start table.

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

    def _refresh_table_picks(self) -> None:
        fill_pick(
            self.table_pick,
            [
                (f"@{tid}  ({len(table.entries)})", tid)
                for tid, table in self.workspace.tables().items()
            ],
            "(no table)",
        )
        self.table_pick.add_command_row()
        self.tables_panel.set_start_table(self.table_pick.currentData())

    def _choose_table(self, table_id: str) -> None:
        select_data(self.table_pick, table_id)

    def _on_table_pick(self) -> None:
        if self._entry is not None:
            self._entry.session.table_id = self.table_pick.currentData()
            if (
                self._entry.kind is EntryKind.BLOCK
                and self._entry.config is not None
                and self.table_pick.currentData()
            ):
                from dataclasses import replace

                self._entry.config = replace(
                    self._entry.config, table_id=self.table_pick.currentData()
                )
                # Through the workspace, so the translations are stashed on the
                # way out: a different start table re-reads the region, it does
                # not throw away what was typed into it.
                self.workspace.drop_document(self._entry)
                self._doc = self._load_document(self._entry)
        self.tables_panel.set_start_table(self.table_pick.currentData())
        self._refresh_view()

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
        """The set the picked start table heads."""
        return self._table_set_for(self.table_pick.currentData())

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
