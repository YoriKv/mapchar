"""Table files on disk: watching them and re-reading them."""

from __future__ import annotations

import os

from PySide6.QtWidgets import QMessageBox

from mapchar.core.errors import MapcharError
from mapchar.project.tables import adopt_tables, read_table_file
from mapchar.project.workspace import Entry, EntryKind


class TablesDockMixin:
    """Table files on disk: watching them and re-reading them.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _watch_table(self, entry: Entry) -> None:
        if entry.kind is EntryKind.TABLE and entry.path and os.path.exists(entry.path):
            self.table_watcher.addPath(entry.path)

    def _rewatch_tables(self) -> None:
        paths = self.table_watcher.files()
        if paths:
            self.table_watcher.removePaths(paths)
        for e in self.workspace.entries:
            self._watch_table(e)

    def _on_table_file_changed(self, path: str) -> None:
        entry = self.workspace.find_table(path)
        if entry is None:
            return
        if os.path.exists(path):
            self.table_watcher.addPath(path)
        if entry.dirty:
            answer = QMessageBox.question(
                self,
                "Table changed on disk",
                f"{entry.name} changed on disk and has edits here. Re-read it? "
                "Your edits stay on top of it, and win where they overlap.",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.reload_table(entry)

    def _tables_changed(self) -> None:
        """A table's entries changed: every document extracts again, and the
        Tables dock and the views catch up."""
        self.workspace.invalidate_extractions()
        self.tables_panel.rebuild()
        self._refresh_view()

    def reload_table(self, entry: Entry) -> None:
        if not entry.path:
            return
        try:
            tf = read_table_file(entry.path, entry.dialect, self.registry)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot reload {entry.name}: {exc}")
            return
        # The in-app edits are laid back over the file's new contents rather
        # than dropped: a reload picks up what changed on disk, not a revert.
        adopt_tables(entry, tf.tables, tf.notices)
        entry.dialect = tf.dialect
        if not entry.table_overlay:
            self.workspace.mark_saved(entry)
        self._refresh_table_picks()
        self._tables_changed()
        self.statusBar().showMessage(f"Reloaded {entry.name}", 4000)
