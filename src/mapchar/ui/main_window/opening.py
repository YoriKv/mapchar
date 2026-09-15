"""Opening files: the dialogs, the readers, and drag and drop."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QMessageBox

from mapchar import APP_NAME
from mapchar.core.errors import MapcharError
from mapchar.core.font import Font
from mapchar.plugins.registry import SIGNATURE_HEAD
from mapchar.project.formats.script import HEADER as SCRIPT_HEADER
from mapchar.project.formats.table_native import HEADER as TABLE_HEADER
from mapchar.project.tables import adopt_table, read_table_file
from mapchar.project.workspace import Entry, EntryKind


class OpeningMixin:
    """Opening files: the dialogs, the readers, and drag and drop.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

    def _open_rom_dialog(self) -> None:
        path = self._pick_open("Open ROM")
        if path:
            self.open_rom(path)

    def open_rom(self, path: str) -> Entry | None:
        self._remember_dir(path)
        existing = self.workspace.find_file(path)
        if existing is not None:
            self._activate_entry(existing)
            return existing
        try:
            with open(path, "rb") as f:
                data = f.read()
        except OSError as exc:
            self._error(f"Cannot open {path}: {exc}")
            return None
        plugin = self.registry.detect_container(data[:SIGNATURE_HEAD], path, len(data))
        entry = self.workspace.new_file(
            path, container_id=plugin.info.id if plugin else "raw"
        )
        self._push_add(entry)
        self._activate_entry(entry)
        return entry

    def _open_table_dialog(self) -> None:
        path = self._pick_open("Open Table", "Tables (*.tbl *.txt);;All files (*)")
        if path:
            self.open_table(path)

    def open_table(self, path: str, dialect: str | None = None) -> Entry | None:
        self._remember_dir(path)
        existing = self.workspace.find_table(path)
        if existing is not None:
            return existing
        try:
            tf = read_table_file(path, dialect, self.registry)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot load table {path}: {exc}")
            return None
        clash = set(self.workspace.loaded_tables()) & {t.id for t in tf.tables}
        if clash:
            self._error(f"Table id(s) already loaded: {', '.join(sorted(clash))}")
            return None
        entry = Entry(EntryKind.TABLE, os.path.basename(path), path, dialect=tf.dialect)
        adopt_table(entry, tf.table, tf.notices)
        self.undo_stack.beginMacro(f"Open {entry.name}")
        self._push_add(entry)
        # A table file holds one table; the others a legacy conversion made are
        # entries of their own, with no file until they are saved.
        for extra in tf.extra_tables:
            self._add_memory_table(extra, f"{extra.id}.tbl")
        self.undo_stack.endMacro()
        if tf.notices:
            self.statusBar().showMessage(
                f"{entry.name}: {len(tf.notices)} conversion notice(s);"
                " see Table Editor",
                6000,
            )
        self._refresh_table_picks()
        # A reading whose table is not there — none loaded yet — takes this one.
        if self._current_table_id() not in self.workspace.tables():
            self._choose_table(tf.table.id)
        return entry

    def _open_font_dialog(self) -> None:
        path = self._pick_open("Open Font", "Images (*.png *.bmp);;All files (*)")
        if path:
            self.open_font(path)

    def open_font(self, path: str) -> Entry:
        self._remember_dir(path)
        entry = Entry(EntryKind.FONT, os.path.basename(path), path, font=Font(path))
        self._push_add(entry)
        return entry

    @staticmethod
    def _dropped_paths(event) -> list[str]:
        """Every local-file path in a drag payload (empty when it has none)."""
        mime = event.mimeData()
        if not mime.hasUrls():
            return []
        return [path for url in mime.urls() if (path := url.toLocalFile())]

    def dragEnterEvent(self, event) -> None:  # noqa: N802 - Qt override
        # Only offer to accept a drag that carries local files, so a drag of
        # anything else shows no drop cursor rather than failing on release.
        if self._dropped_paths(event):
            event.acceptProposedAction()
        else:
            event.ignore()

    dragMoveEvent = dragEnterEvent

    def dropEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Open what was dropped as what its name says it is.

        A ``.mapchar`` is a whole session rather than a file to add — opening one
        replaces the workspace — so it claims the entire drop instead of racing
        the other files, which the replace would discard anyway.

        Holding **Ctrl** says "ask me": detection is a guess from a suffix, and
        this is how the user overrules it without going to find the matching menu
        entry for a file already in hand. Ctrl is also the platform's own
        drag-copy modifier, which costs a prompt that can be cancelled — the
        cheap direction to be wrong in.
        """
        paths = self._dropped_paths(event)
        if not paths:
            return
        event.acceptProposedAction()
        project = next((p for p in paths if p.lower().endswith(".mapchar")), None)
        if project is not None:
            self.open_project(project)
            if len(paths) > 1:
                self.statusBar().showMessage(
                    f"Opened the project; the other {len(paths) - 1} dropped "
                    "file(s) were ignored.",
                    5000,
                )
            return
        ask = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        for path in paths:
            kind = self._ask_drop_kind(path) if ask else self._drop_kind(path)
            if kind is None:
                continue
            self._open_dropped(path, kind)

    @staticmethod
    def _drop_kind(path: str) -> str:
        """What a dropped file is: the guess Ctrl overrules.

        Suffixes settle most of it. ``.txt`` settles nothing — a table, a native
        script and a Cartographer command file all wear it — so a text file is
        read far enough to find one of mapChar's own header lines, and anything
        else falls through to "a binary to open as a ROM".
        """
        lower = path.lower()
        if lower.endswith(".tbl"):
            return "table"
        if lower.endswith(".png"):
            return "font"
        if lower.endswith((".tsv", ".csv")):
            return "delimited"
        if lower.endswith(".po"):
            return "po"
        if lower.endswith(".txt"):
            try:
                with open(path, encoding="utf-8", errors="replace") as f:
                    head = next((ln.strip() for ln in f if ln.strip()), "")
            except OSError:
                return "rom"
            if head == SCRIPT_HEADER:
                return "script"
            if head == TABLE_HEADER:
                return "table"
        return "rom"

    def _ask_drop_kind(self, path: str) -> str | None:
        """Which reading to open a dropped file as, or ``None`` if cancelled."""
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Question)
        box.setWindowTitle(f"{APP_NAME} — Open As")
        box.setText(f"Open {os.path.basename(path)} as:")
        role = QMessageBox.ButtonRole.ActionRole
        buttons = {
            box.addButton("&ROM", role): "rom",
            box.addButton("&Table", role): "table",
            box.addButton("&Font", role): "font",
            box.addButton("&Script", role): "script",
            box.addButton("T&SV / CSV", role): "delimited",
            box.addButton("&PO", role): "po",
        }
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        return buttons.get(box.clickedButton())

    def _open_dropped(self, path: str, kind: str) -> None:
        if kind == "rom":
            self.open_rom(path)
        elif kind == "table":
            self.open_table(path)
        elif kind == "font":
            self.open_font(path)
        else:
            self.import_file(path, kind)
