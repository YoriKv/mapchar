"""Table files on disk: watching them and re-reading them."""

from __future__ import annotations

import os

from mapchar.core.errors import MapcharError
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.tables import adopt_table, read_table_file, same_table


class TableFilesMixin:
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
        if entry.dirty and not self._ask(
            "Table Changed on Disk",
            f"{entry.name} changed on disk and has edits here. Re-read it? "
            "Your edits stay on top of it, and win where they overlap.",
        ):
            return
        self.reload_table(entry)

    def _tables_changed(self) -> None:
        """A table's entries changed: every document extracts again, and the
        table picks, the table counts and the views catch up."""
        self.workspace.invalidate_extractions()
        self._refresh_table_picks()
        self.files_panel.refresh_labels()
        self._refresh_view()

    def reload_table(self, entry: Entry) -> None:
        """Re-read one table file, and catch the UI up on what it changed."""
        if not entry.path:
            return
        try:
            changed = self._reread_table(entry)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot reload {entry.name}: {exc}")
            return
        if changed:
            self._tables_changed()
            self._retry_unwritten()
        self.statusBar().showMessage(f"Reloaded {entry.name}", 4000)

    def refresh_tables(self) -> None:
        """Re-read every table file on disk, and catch the UI up on the ones
        that changed.

        What the watcher does as a file is written, asked for outright, for
        the editor it never sees: one that replaces the file rather than
        writing over it, or one on a filesystem the watcher cannot follow. A
        file that says what it said costs a read and nothing else, and a table
        with no file of its own — the project carries it whole — is skipped.
        """
        reloaded, failed = 0, []
        for entry in self.workspace.of_kind(EntryKind.TABLE):
            try:
                if self._reread_table(entry):
                    reloaded += 1
            except (OSError, MapcharError) as exc:
                failed.append(f"{entry.name}: {exc}")
        if reloaded:
            self._tables_changed()
            self._retry_unwritten()
        # A file that was replaced leaves the watcher holding a path that is
        # gone: this is where it takes the new one up.
        self._rewatch_tables()
        if failed:
            self._error("Cannot reload:\n" + "\n".join(failed))
        self.statusBar().showMessage(
            f"Reloaded {reloaded} table{'' if reloaded == 1 else 's'}"
            if reloaded
            else "Tables are up to date",
            4000,
        )

    def _reread_table(self, entry: Entry) -> bool:
        """Read ``entry``'s file again, and say whether what it holds changed.

        The in-app edits are laid back over the file's new contents rather than
        dropped: a re-read picks up what changed on disk, not a revert. Raises
        ``OSError`` for the file and
        :class:`~mapchar.core.errors.MapcharError` for its contents.
        """
        if not entry.path or not os.path.exists(entry.path):
            return False
        tf = read_table_file(entry.path, entry.dialect, self.registry)
        before, dialect = entry.table, entry.dialect
        adopt_table(entry, tf.table, tf.notices)
        entry.dialect = tf.dialect
        if not entry.table_overlay:
            self.workspace.mark_saved(entry)
        return entry.dialect != dialect or not same_table(before, entry.table)
