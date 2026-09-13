"""The main window: shell, menus, docks, and the entry/refresh cycle."""

from __future__ import annotations

import os

from PySide6.QtCore import QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from mapchar import __version__
from mapchar.core.bits import Bits
from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.core.document import Document
from mapchar.core.errors import MapcharError
from mapchar.core.table import Table, TableSet
from mapchar.engines.decode import DecodeRules, EndedBy, decode
from mapchar.engines.relsearch import Hit, entries_from_hit
from mapchar.pipeline.extract import extract
from mapchar.pipeline.pipeline import FileRef, PathwayConfig, load
from mapchar.plugins.base import Stage
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.registry import Registry, default_registry
from mapchar.project.formats.script import write_script
from mapchar.project.formats.table_legacy import load_table_text
from mapchar.project.formats.table_native import write_native
from mapchar.project.projectfile import (
    LoadedProject,
    ProjectError,
    load_project,
    project_dict,
    save_project,
)
from mapchar.project.workspace import Entry, EntryKind, Workspace
from mapchar.ui.dialogs import BlockDialog, DumpDialog, TextDialog, parse_hex
from mapchar.ui.files_panel import FilesPanel
from mapchar.ui.raw_widget import BYTES_PER_ROW, RawWidget, RowModel
from mapchar.ui.search_window import SearchWindow
from mapchar.ui.table_editor import TableEditor
from mapchar.ui.tables_panel import TablesPanel
from mapchar.ui.text_widget import TextWidget, text_model
from mapchar.ui.undo_commands import EntryCommand, OffsetCommand

MAX_RECENT = 10
TEXT_WINDOW_BYTES = 4096
"""How many bytes the text mode decodes from the offset."""
DISPLAY_MODE_KEY = "view/display_mode"


class MainWindow(QMainWindow):
    def __init__(self, registry: Registry | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.registry = registry or default_registry()
        self.workspace = Workspace()
        self.undo_stack = QUndoStack(self)
        self.settings = QSettings("mapchar", "mapchar")
        self.project_path: str | None = None
        self._saved_snapshot: str | None = None
        self._applying_undo = False
        self._doc: Document | None = None
        self._entry: Entry | None = None
        self._offset = 0
        self._selection: tuple[int, int] | None = None
        self._build_widgets()
        self._build_menus()
        self._restore_layout()
        self._update_title()
        self._refresh_view()

    # ------------------------------------------------------------------
    # Widgets
    # ------------------------------------------------------------------

    def _build_widgets(self) -> None:
        self.setWindowTitle("mapchar")
        self.resize(1100, 720)

        self.files_panel = FilesPanel(self.workspace)
        files_dock = QDockWidget("Files", self)
        files_dock.setObjectName("files_dock")
        files_dock.setWidget(self.files_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, files_dock)
        self.files_dock = files_dock

        self.tables_panel = TablesPanel(self.workspace)
        tables_dock = QDockWidget("Tables", self)
        tables_dock.setObjectName("tables_dock")
        tables_dock.setWidget(self.tables_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, tables_dock)
        self.tables_dock = tables_dock

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        codecs = QToolBar("Codecs")
        codecs.setObjectName("codecs_bar")
        codecs.setMovable(False)
        self.container_pick = QComboBox()
        for plugin in self.registry.plugins(Stage.CONTAINER):
            self.container_pick.addItem(plugin.info.name, plugin.info.id)
        self.reshape_pick = QComboBox()
        self.reshape_pick.addItem("None", None)
        for plugin in self.registry.plugins(Stage.RESHAPE):
            self.reshape_pick.addItem(plugin.info.name, plugin.info.id)
        self.table_pick = QComboBox()
        self.table_pick.addItem("(no table)", None)
        codecs.addWidget(QLabel(" Container "))
        codecs.addWidget(self.container_pick)
        codecs.addWidget(QLabel("  Reshape "))
        codecs.addWidget(self.reshape_pick)
        codecs.addWidget(QLabel("  Start table "))
        codecs.addWidget(self.table_pick)
        self.addToolBar(codecs)
        self.codecs_bar = codecs

        block_bar = QWidget()
        bl = QHBoxLayout(block_bar)
        bl.setContentsMargins(0, 0, 0, 0)
        self.block_label = QLabel("")
        self.block_edit = QPushButton("Edit…")
        self.block_dump = QPushButton("Dump…")
        bl.addWidget(self.block_label, 1)
        bl.addWidget(self.block_edit)
        bl.addWidget(self.block_dump)
        self.block_bar = block_bar
        layout.addWidget(block_bar)

        self.tabs = QTabWidget()
        self.raw = RawWidget()
        self.text = TextWidget()
        self.display = QStackedWidget()
        self.display.addWidget(self.raw)
        self.display.addWidget(self.text)
        self.strings = QTableWidget(0, 4)
        self.strings.setHorizontalHeaderLabels(["#", "Address", "Bytes", "Original"])
        self.strings.horizontalHeader().setStretchLastSection(True)
        self.strings.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.strings.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.tabs.addTab(self.display, "Raw")
        self.tabs.addTab(self.strings, "Strings")
        layout.addWidget(self.tabs, 1)

        nav = QWidget()
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(0, 0, 0, 0)
        self.offset_box = QLineEdit()
        self.offset_box.setMaximumWidth(120)
        self.offset_box.setPlaceholderText("offset (hex)")
        self.mode_button = QPushButton("Aligned")
        self.mode_button.setCheckable(True)
        self.mode_button.setToolTip(
            "Switch between the aligned hex/text view and a text box (Ctrl+Shift+A)"
        )
        self.mode_button.setMaximumWidth(80)
        nl.addWidget(self.mode_button)
        nl.addWidget(QLabel("Offset"))
        nl.addWidget(self.offset_box)
        for text, delta, tip in (
            ("⇤", "home", "Start of file (Home)"),
            ("−P", -1, "Page up (PgUp)"),
            ("−R", -BYTES_PER_ROW, "Row up (Up)"),
            ("−B", -1, "Byte back (-)"),
            ("+B", 1, "Byte forward (+)"),
            ("+R", BYTES_PER_ROW, "Row down (Down)"),
            ("+P", 1, "Page down (PgDn)"),
            ("⇥", "end", "End of file (End)"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setMaximumWidth(40)
            if text in ("−P", "+P"):
                b.clicked.connect(
                    lambda _=False, d=delta: self._move(d * self.raw.visible_bytes())
                )
            elif isinstance(delta, int):
                b.clicked.connect(lambda _=False, d=delta: self._move(d))
            elif delta == "home":
                b.clicked.connect(lambda: self._go_to(0))
            else:
                b.clicked.connect(self._go_end)
            nl.addWidget(b)
        nl.addStretch(1)
        self.nav_status = QLabel("")
        nl.addWidget(self.nav_status)
        self.nav_bar = nav
        layout.addWidget(nav)
        self.setCentralWidget(central)

        self.search_window = SearchWindow(self)
        self.table_editor = TableEditor(self)

        # Signals.
        self.files_panel.entry_activated.connect(self._activate_entry)
        self.files_panel.entry_double_clicked.connect(self._on_entry_double)
        self.files_panel.context_menu_requested.connect(self._files_menu)
        self.files_panel.remove_requested.connect(self._remove_entries)
        self.tables_panel.table_chosen.connect(self._choose_table)
        self.tables_panel.edit_requested.connect(self._edit_table_entry)
        self.container_pick.currentIndexChanged.connect(self._on_chain_changed)
        self.reshape_pick.currentIndexChanged.connect(self._on_chain_changed)
        self.table_pick.currentIndexChanged.connect(self._on_table_pick)
        self.block_edit.clicked.connect(self._edit_block)
        self.block_dump.clicked.connect(self._dump)
        self.raw.offset_requested.connect(self._go_to)
        self.text.selection_changed.connect(self._on_text_selection)
        self.mode_button.toggled.connect(self._on_mode_toggled)
        self.mode_button.setChecked(
            str(self.settings.value(DISPLAY_MODE_KEY, "aligned")) == "text"
        )
        self.raw.selection_changed.connect(self._on_selection)
        self.raw.context_menu_requested.connect(self._raw_menu)
        self.offset_box.returnPressed.connect(self._on_offset_typed)
        self.strings.itemSelectionChanged.connect(self._on_string_row)
        self.search_window.go_to.connect(self._select_bytes)
        self.search_window.build_table.connect(self._build_table_from_hit)
        self.table_editor.changed.connect(self._on_table_edited)
        self.table_editor.save_requested.connect(self._save_table_entry)
        self.workspace.on_current_changed.append(lambda e: self._update_title())

    def _build_menus(self) -> None:
        bar = self.menuBar()

        def act(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        file_menu = bar.addMenu("&File")
        act(file_menu, "Open ROM…", self._open_rom_dialog, "Ctrl+Shift+O")
        act(file_menu, "Open Table…", self._open_table_dialog, "Ctrl+T")
        file_menu.addSeparator()
        act(file_menu, "New Block…", self._new_block, "Ctrl+Shift+B")
        act(file_menu, "New Bookmark", self._new_bookmark, "Ctrl+B")
        act(file_menu, "Dump…", self._dump, "Ctrl+D")
        file_menu.addSeparator()
        act(file_menu, "New Project", self._new_project, "Ctrl+N")
        act(file_menu, "Open Project…", self._open_project_dialog, "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Open Recent")
        act(file_menu, "Save Project", self._save_project, "Ctrl+S")
        act(file_menu, "Save Project As…", self._save_project_as, "Ctrl+Shift+S")
        file_menu.addSeparator()
        act(file_menu, "Quit", self.close, "Ctrl+Q")

        edit_menu = bar.addMenu("&Edit")
        undo = self.undo_stack.createUndoAction(self, "Undo")
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        redo = self.undo_stack.createRedoAction(self, "Redo")
        redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        edit_menu.addSeparator()
        act(edit_menu, "Go to Address…", self._go_to_dialog, "Ctrl+G")

        view_menu = bar.addMenu("&View")
        act(view_menu, "Raw", lambda: self.tabs.setCurrentIndex(0), "Ctrl+1")
        act(view_menu, "Strings", lambda: self.tabs.setCurrentIndex(1), "Ctrl+2")
        act(
            view_menu, "Aligned / Text display", self.mode_button.toggle, "Ctrl+Shift+A"
        )
        view_menu.addSeparator()
        act(view_menu, "Table Editor…", self._show_table_editor, "Ctrl+Shift+T")
        view_menu.addSeparator()
        self.theme_light = act(
            view_menu, "Light theme", lambda: self._set_theme("light")
        )
        self.theme_dark = act(view_menu, "Dark theme", lambda: self._set_theme("dark"))

        search_menu = bar.addMenu("&Search")
        act(search_menu, "Search window…", self._show_search, "Ctrl+Shift+F")
        act(search_menu, "Find bytes…", self._find_bytes, "Ctrl+F")
        act(search_menu, "Find next", lambda: self._find_bytes(again=True), "F3")

        panels_menu = bar.addMenu("&Panels")
        panels_menu.addAction(self.files_dock.toggleViewAction())
        panels_menu.addAction(self.tables_dock.toggleViewAction())
        panels_menu.addSeparator()
        act(panels_menu, "Reset Panel Layout", self._reset_layout)

        help_menu = bar.addMenu("&Help")
        act(help_menu, "Shortcuts…", self._show_shortcuts, "F1")
        act(help_menu, "About", self._about)
        self._rebuild_recent()

    # ------------------------------------------------------------------
    # Layout and settings
    # ------------------------------------------------------------------

    def _restore_layout(self) -> None:
        self._factory_state = self.saveState()
        geometry = self.settings.value("window/geometry")
        state = self.settings.value("window/state")
        if geometry:
            self.restoreGeometry(geometry)
        if state:
            self.restoreState(state)

    def _reset_layout(self) -> None:
        self.restoreState(self._factory_state)
        self.files_dock.show()
        self.tables_dock.show()

    def _set_theme(self, name: str) -> None:
        from mapchar.ui.theme import apply_theme

        apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)

    def closeEvent(self, event) -> None:
        if not self._confirm_discard("quit"):
            event.ignore()
            return
        self.settings.setValue("window/geometry", self.saveGeometry())
        self.settings.setValue("window/state", self.saveState())
        super().closeEvent(event)

    # ------------------------------------------------------------------
    # Opening
    # ------------------------------------------------------------------

    def _open_rom_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Open ROM", self._last_dir())
        if path:
            self.open_rom(path)

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", ""))

    def _remember_dir(self, path: str) -> None:
        self.settings.setValue("last_dir", os.path.dirname(path))

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
        plugin = self.registry.detect_container(data, path)
        container_id = plugin.info.id if plugin else "raw"
        entry = Entry(
            EntryKind.FILE, os.path.basename(path), path, container_id=container_id
        )
        self._push_add(entry)
        self._activate_entry(entry)
        return entry

    def _open_table_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Table", self._last_dir(), "Tables (*.tbl *.txt);;All files (*)"
        )
        if path:
            self.open_table(path)

    def open_table(self, path: str, dialect: str | None = None) -> Entry | None:
        self._remember_dir(path)
        existing = self.workspace.find_table(path)
        if existing is not None:
            return existing
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
            tf = load_table_text(text, path, dialect)
            for t in tf.tables:
                apply_charset(t, self.registry)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot load table {path}: {exc}")
            return None
        clash = set(self.workspace.tables()) & {t.id for t in tf.tables}
        if clash:
            self._error(f"Table id(s) already loaded: {', '.join(sorted(clash))}")
            return None
        entry = Entry(
            EntryKind.TABLE,
            os.path.basename(path),
            path,
            dialect=tf.dialect,
            tables=tf.tables,
        )
        self._push_add(entry)
        if tf.notices:
            self.statusBar().showMessage(
                f"{entry.name}: {len(tf.notices)} conversion notice(s);"
                " see Table Editor",
                6000,
            )
            entry.notices = tf.notices  # type: ignore[attr-defined]
        self._refresh_table_picks()
        if self.table_pick.currentData() is None and tf.tables:
            self._choose_table(tf.tables[0].id)
        return entry

    def _add_memory_table(self, table: Table, name: str) -> Entry:
        entry = Entry(EntryKind.TABLE, name, None, dialect="native", tables=[table])
        self._push_add(entry)
        self.workspace.stamp(entry)
        self._refresh_table_picks()
        return entry

    # ------------------------------------------------------------------
    # Entries
    # ------------------------------------------------------------------

    def _push_add(self, entry: Entry) -> None:
        self.undo_stack.push(EntryCommand(self, entry, add=True))

    def _remove_entries(self, entries: list[Entry]) -> None:
        entries = [e for e in entries if e in self.workspace.entries]
        if not entries:
            return
        names = ", ".join(e.name for e in entries)
        children = [c for e in entries for c in self.workspace.children(e)]
        msg = f"Remove {names}?"
        if children:
            msg += f"\nAlso removes: {', '.join(c.name for c in children)}."
        if any(e.dirty for e in entries + children):
            msg += "\nUnsaved edits will be discarded."
        if QMessageBox.question(self, "Remove", msg) != QMessageBox.StandardButton.Yes:
            return
        self.undo_stack.beginMacro("Remove entries")
        for e in entries:
            if e in self.workspace.entries:
                self.undo_stack.push(EntryCommand(self, e, add=False))
        self.undo_stack.endMacro()

    def apply_entry_add(
        self, entry: Entry, index: int | None, children: list[Entry]
    ) -> None:
        self.workspace.add(entry, index)
        for child in children:
            self.workspace.add(child)
        self._refresh_table_picks()
        self._update_title()

    def apply_entry_remove(self, entry: Entry) -> list[Entry]:
        removed = self.workspace.close(entry)
        if self._entry in removed:
            self._entry = None
            self._doc = None
            self._activate_entry(self.workspace.current)
        self._refresh_table_picks()
        self._update_title()
        return removed

    def _on_entry_double(self, entry: Entry) -> None:
        if entry.kind is EntryKind.BOOKMARK:
            self._jump_to_bookmark(entry)
        elif entry.kind is EntryKind.TABLE:
            self._edit_table_entry(entry)
        elif entry.kind is EntryKind.BLOCK:
            self._activate_entry(entry)
            self._edit_block()

    def _files_menu(self, entry: Entry | None, pos: QPoint) -> None:
        menu = QMenu(self)
        if entry is None:
            menu.addAction("Open ROM…", self._open_rom_dialog)
            menu.addAction("Open Table…", self._open_table_dialog)
        else:
            if entry.kind is EntryKind.FILE:
                menu.addAction(
                    "New Block…",
                    lambda: (self._activate_entry(entry), self._new_block()),
                )
                menu.addAction(
                    "New Bookmark",
                    lambda: (self._activate_entry(entry), self._new_bookmark()),
                )
                menu.addAction(
                    "Dump all blocks…",
                    lambda: (self._activate_entry(entry), self._dump(all_blocks=True)),
                )
            if entry.kind is EntryKind.BLOCK:
                menu.addAction(
                    "Edit…", lambda: (self._activate_entry(entry), self._edit_block())
                )
                menu.addAction(
                    "Dump…", lambda: (self._activate_entry(entry), self._dump())
                )
                menu.addAction("Jump to Source", lambda: self._jump_to_source(entry))
            if entry.kind is EntryKind.BOOKMARK:
                menu.addAction(
                    "Jump to Bookmark", lambda: self._jump_to_bookmark(entry)
                )
            if entry.kind is EntryKind.TABLE:
                menu.addAction("Edit…", lambda: self._edit_table_entry(entry))
                menu.addAction(
                    "Save As Native…", lambda: self._save_table_entry(entry, ask=True)
                )
            menu.addAction("Rename…", lambda: self._rename(entry))
            if entry.path:
                menu.addAction("Show in File Manager", lambda: self._reveal(entry.path))
            menu.addSeparator()
            menu.addAction(
                "Remove",
                lambda: self._remove_entries(
                    self.files_panel.selected_entries() or [entry]
                ),
            )
        menu.exec(pos)

    def _rename(self, entry: Entry) -> None:
        name, ok = QInputDialog.getText(self, "Rename", "Name:", text=entry.name)
        if ok and name.strip():
            entry.name = name.strip()
            self.files_panel.refresh_labels()
            self._update_title()

    def _reveal(self, path: str) -> None:
        from PySide6.QtCore import QUrl
        from PySide6.QtGui import QDesktopServices

        QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(path)))

    # ------------------------------------------------------------------
    # Activation and documents
    # ------------------------------------------------------------------

    def _activate_entry(self, entry: Entry | None) -> None:
        if entry is not None and entry.kind is EntryKind.TABLE:
            self.workspace.set_current(entry)
            self._edit_table_entry(entry)
            return
        if entry is not None and entry.kind is EntryKind.BOOKMARK:
            self._jump_to_bookmark(entry)
            return
        if self._entry is not None:
            self._entry.session.offset = self._offset
            self._entry.session.view = (
                "strings" if self.tabs.currentIndex() == 1 else "raw"
            )
        self._entry = entry
        self.workspace.set_current(entry)
        self._doc = self._load_document(entry) if entry is not None else None
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
                    entry.reshape_id,
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
                entry.doc = Document(
                    parent_doc.data, parent_doc.ctx, parent_doc.writable
                )
        except OSError as exc:
            entry.missing = True
            self._error(f"{entry.name}: {exc}")
            return None
        except MapcharError as exc:
            self._error(str(exc))
            return None
        self.files_panel.refresh_labels()
        return entry.doc

    def _restore_session(self) -> None:
        entry = self._entry
        widgets = (self.container_pick, self.reshape_pick, self.table_pick)
        for w in widgets:
            w.blockSignals(True)
        try:
            self._refresh_table_picks()
            if entry is None:
                self._offset = 0
                return
            file_entry = entry.parent if entry.kind is EntryKind.BLOCK else entry
            if file_entry is not None:
                i = self.container_pick.findData(file_entry.container_id)
                self.container_pick.setCurrentIndex(max(i, 0))
                i = self.reshape_pick.findData(file_entry.reshape_id)
                self.reshape_pick.setCurrentIndex(max(i, 0))
            table_id = entry.session.table_id
            if entry.kind is EntryKind.BLOCK and entry.config is not None:
                table_id = entry.config.table_id or table_id
            if table_id is None:
                tables = self.workspace.tables()
                table_id = next(iter(tables), None)
            i = self.table_pick.findData(table_id)
            self.table_pick.setCurrentIndex(max(i, 0))
            self._offset = entry.session.offset
            if (
                entry.kind is EntryKind.BLOCK
                and entry.config is not None
                and not entry.session.offset
            ):
                self._offset = getattr(entry.config.source, "start", 0)
            self.tabs.setCurrentIndex(1 if entry.session.view == "strings" else 0)
        finally:
            for w in widgets:
                w.blockSignals(False)

    def _refresh_table_picks(self) -> None:
        current = self.table_pick.currentData()
        self.table_pick.blockSignals(True)
        self.table_pick.clear()
        self.table_pick.addItem("(no table)", None)
        for tid in self.workspace.tables():
            self.table_pick.addItem(f"@{tid}", tid)
        i = self.table_pick.findData(current)
        self.table_pick.setCurrentIndex(max(i, 0))
        self.table_pick.blockSignals(False)
        self.tables_panel.set_start_table(self.table_pick.currentData())

    def _choose_table(self, table_id: str) -> None:
        i = self.table_pick.findData(table_id)
        if i >= 0:
            self.table_pick.setCurrentIndex(i)

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
                self._entry.doc = None
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
        file_entry.reshape_id = self.reshape_pick.currentData()
        file_entry.doc = None
        for child in self.workspace.children(file_entry):
            child.doc = None
        self._doc = self._load_document(entry)
        self._refresh_view()

    def _table_set(self) -> TableSet | None:
        tid = self.table_pick.currentData()
        tables = self.workspace.tables()
        if not tid or tid not in tables:
            return None
        try:
            return TableSet.build(tables[tid], tables)
        except MapcharError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            return None

    # ------------------------------------------------------------------
    # Refresh
    # ------------------------------------------------------------------

    def _refresh_view(self) -> None:
        doc, entry = self._doc, self._entry
        is_block = entry is not None and entry.kind is EntryKind.BLOCK
        self.block_bar.setVisible(is_block)
        self.tabs.setTabEnabled(1, is_block)
        self.block_edit.setEnabled(is_block)
        self.block_dump.setEnabled(is_block)
        self.codecs_bar.setEnabled(entry is not None)
        if doc is None:
            self.raw.set_model(None)
            self.strings.setRowCount(0)
            self.nav_status.setText("")
            self.offset_box.setText("")
            self._update_title()
            return
        total = doc.size
        self._offset = max(0, min(self._offset, max(total - 1, 0)))
        self.offset_box.setText(f"{self._offset:X}")
        tables = self._table_set()
        window = self.raw.visible_bytes() + BYTES_PER_ROW
        data = doc.data[self._offset : self._offset + window]
        tokens = []
        string_starts: set[int] = set()
        if tables is not None and data:
            bits = Bits(data)
            pos = 0
            while pos < bits.length:
                string_starts.add(pos // 8)
                r = decode(bits, tables, pos, DecodeRules(end_terminated=True))
                tokens.extend(r.tokens)
                if r.end_bit <= pos or r.ended_by in (EndedBy.DATA, EndedBy.LIMIT):
                    break
                pos = r.end_bit
        self.raw.set_model(RowModel(self._offset, data, tokens, string_starts, total))
        self._refresh_text_mode(doc, tables)
        if is_block:
            self._extract_current(entry, doc, tables)
            self._fill_strings(doc)
            cfg = entry.config
            self.block_label.setText(
                f"{entry.name}: "
                f"{cfg.source.__class__.__name__.replace('Source', '')} · "
                f"{cfg.string_type.__class__.__name__} · @{cfg.table_id or '-'} · "
                f"{len(doc.strings)} strings"
            )
        self._update_nav_status()
        self.search_window.set_data(doc.data)
        self._update_title()

    def _refresh_text_mode(self, doc: Document, tables: TableSet | None) -> None:
        if self.display.currentWidget() is not self.text:
            return
        data = doc.data[self._offset : self._offset + TEXT_WINDOW_BYTES]
        tokens = []
        if tables is not None and data:
            bits = Bits(data)
            pos = 0
            while pos < bits.length:
                r = decode(bits, tables, pos, DecodeRules(end_terminated=True))
                tokens.extend(r.tokens)
                if r.end_bit <= pos or r.ended_by in (EndedBy.DATA, EndedBy.LIMIT):
                    break
                pos = r.end_bit
        self.text.set_model(text_model(tokens, self._offset, len(data)))
        if self._selection:
            self.text.select_bytes(*self._selection)

    def _on_mode_toggled(self, text_mode: bool) -> None:
        self.display.setCurrentWidget(self.text if text_mode else self.raw)
        self.mode_button.setText("Text" if text_mode else "Aligned")
        self.settings.setValue(DISPLAY_MODE_KEY, "text" if text_mode else "aligned")
        self._refresh_view()

    def _on_text_selection(self, start: int, end: int) -> None:
        self.raw.set_selection(start, end)
        self._on_selection(start, end)

    def _extract_current(
        self, entry: Entry, doc: Document, tables: TableSet | None
    ) -> None:
        cfg = entry.config
        if cfg is None or tables is None:
            doc.strings = []
            doc.extraction_key = None
            return
        key = (
            cfg,
            tables.start.id,
            id(doc.data),
            sum(len(t.entries) for t in tables.tables.values()),
        )
        if doc.extraction_key == key:
            return
        try:
            ex = extract(doc.data, cfg, tables)
        except NotImplementedError as exc:
            self.statusBar().showMessage(str(exc), 5000)
            doc.strings = []
            return
        old = {s.index: s for s in doc.strings}
        for rec in ex.strings:
            prev = old.get(rec.index)
            if prev is not None:
                rec.translation, rec.status, rec.notes = (
                    prev.translation,
                    prev.status,
                    prev.notes,
                )
        saved = getattr(entry, "pending_strings", None)
        if saved:
            for rec in ex.strings:
                st = saved.get(rec.index)
                if st is not None:
                    rec.translation, rec.status, rec.notes = (
                        st.translation,
                        st.status,
                        st.notes,
                    )
            entry.pending_strings = None  # type: ignore[attr-defined]
        doc.strings = ex.strings
        doc.notices = ex.notices
        doc.extraction_key = key
        self.files_panel.refresh_labels()

    def _fill_strings(self, doc: Document) -> None:
        self.strings.blockSignals(True)
        self.strings.setRowCount(len(doc.strings))
        for row, rec in enumerate(doc.strings):
            cells = [
                str(rec.index),
                f"{rec.start:X}",
                str(rec.length),
                rec.original_text().replace("\n", "↵"),
            ]
            for col, text in enumerate(cells):
                self.strings.setItem(row, col, QTableWidgetItem(text))
        self.strings.resizeColumnToContents(0)
        self.strings.resizeColumnToContents(1)
        self.strings.resizeColumnToContents(2)
        self.strings.blockSignals(False)

    def _update_nav_status(self) -> None:
        doc = self._doc
        if doc is None:
            return
        parts = [f"{doc.size:,} bytes"]
        if self._selection:
            s, e = self._selection
            parts.append(f"selected {s:X}–{e - 1:X} ({e - s} bytes)")
        if doc.missing_plugins:
            parts.append("view-only: missing " + ", ".join(doc.missing_plugins))
        self.nav_status.setText("  ·  ".join(parts))

    def _update_title(self) -> None:
        name = os.path.basename(self.project_path) if self.project_path else "Untitled"
        mark = "*" if self._project_dirty() else ""
        entry = f" — {self._entry.name}" if self._entry else ""
        self.setWindowTitle(f"{name}{mark}{entry} — mapchar")

    # ------------------------------------------------------------------
    # Navigation
    # ------------------------------------------------------------------

    def _go_to(self, offset: int) -> None:
        if self._doc is None:
            return
        offset = max(0, min(offset, max(self._doc.size - 1, 0)))
        if offset == self._offset:
            return
        if not self._applying_undo and self._entry is not None:
            self.undo_stack.push(OffsetCommand(self, self._entry, self._offset, offset))
        else:
            self.apply_offset(self._entry, offset)

    def apply_offset(self, entry: Entry | None, offset: int) -> None:
        if entry is not self._entry and entry is not None:
            self._activate_entry(entry)
        self._offset = offset
        self._refresh_view()

    def _move(self, delta: int) -> None:
        self._go_to(self._offset + delta)

    def _go_end(self) -> None:
        if self._doc is not None:
            self._go_to(max(0, self._doc.size - self.raw.visible_bytes()))

    def _on_offset_typed(self) -> None:
        try:
            self._go_to(parse_hex(self.offset_box.text()))
        except ValueError:
            self.statusBar().showMessage("Not a hex offset", 3000)

    def _go_to_dialog(self) -> None:
        text, ok = QInputDialog.getText(
            self, "Go to Address", "Offset (hex):", text=f"{self._offset:X}"
        )
        if ok:
            try:
                self._go_to(parse_hex(text))
            except ValueError:
                self.statusBar().showMessage("Not a hex offset", 3000)

    def _select_bytes(self, offset: int, length: int) -> None:
        if self._doc is None:
            return
        self._go_to(max(0, offset - BYTES_PER_ROW))
        self.raw.set_selection(offset, offset + length)
        self._on_selection(offset, offset + length)

    def _on_selection(self, start: int, end: int) -> None:
        self._selection = (start, end) if end > start else None
        self._update_nav_status()
        if self._selection and self.display.currentWidget() is self.text:
            self.text.select_bytes(*self._selection)
        if self._selection and self._doc is not None:
            s, e = self._selection
            for row, rec in enumerate(self._doc.strings):
                if rec.start <= s < rec.end:
                    self.strings.blockSignals(True)
                    self.strings.selectRow(row)
                    self.strings.blockSignals(False)
                    break

    def _on_string_row(self) -> None:
        if self._doc is None:
            return
        rows = self.strings.selectionModel().selectedRows()
        if not rows:
            return
        rec = self._doc.strings[rows[0].row()]
        if not (self._offset <= rec.start < self._offset + self.raw.visible_bytes()):
            self._go_to(max(0, rec.start - BYTES_PER_ROW))
        self.raw.set_selection(rec.start, rec.end)
        self._selection = (rec.start, rec.end)
        self._update_nav_status()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        page = self.raw.visible_bytes()
        moves = {
            Qt.Key.Key_PageUp: -page,
            Qt.Key.Key_PageDown: page,
            Qt.Key.Key_Up: -BYTES_PER_ROW,
            Qt.Key.Key_Down: BYTES_PER_ROW,
            Qt.Key.Key_Minus: -1,
            Qt.Key.Key_Plus: 1,
            Qt.Key.Key_Equal: 1,
        }
        if (
            self.focusWidget() in (self.raw, None, self)
            or self.focusWidget() is self.raw.viewport()
        ):
            if key in moves:
                self._move(moves[key])
                return
            if key == Qt.Key.Key_Home:
                self._go_to(0)
                return
            if key == Qt.Key.Key_End:
                self._go_end()
                return
        super().keyPressEvent(event)

    # ------------------------------------------------------------------
    # Blocks and bookmarks
    # ------------------------------------------------------------------

    def _current_file(self) -> Entry | None:
        e = self._entry
        if e is None:
            return None
        return e.parent if e.kind is EntryKind.BLOCK else e

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
        dialog = BlockDialog(table_ids, cfg, f"Block {start:X}", self)
        if dialog.exec() != BlockDialog.DialogCode.Accepted:
            return
        entry = Entry(
            EntryKind.BLOCK,
            dialog.name.text().strip() or f"Block {start:X}",
            file_entry.path,
            parent=file_entry,
            config=dialog.config(),
        )
        self._push_add(entry)
        self._activate_entry(entry)
        self.tabs.setCurrentIndex(1)

    def _edit_block(self) -> None:
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK:
            return
        dialog = BlockDialog(
            list(self.workspace.tables()), entry.config, entry.name, self
        )
        if dialog.exec() != BlockDialog.DialogCode.Accepted:
            return
        entry.name = dialog.name.text().strip() or entry.name
        entry.config = dialog.config()
        entry.doc = None
        self._doc = self._load_document(entry)
        self._restore_session()
        self.files_panel.refresh_labels()
        self._refresh_view()

    def _new_bookmark(self) -> None:
        file_entry = self._current_file()
        if file_entry is None:
            return
        entry = Entry(
            EntryKind.BOOKMARK,
            f"Bookmark {self._offset:X}",
            file_entry.path,
            parent=file_entry,
            bookmark_offset=self._offset,
        )
        entry.session.table_id = self.table_pick.currentData()
        self._push_add(entry)

    def _jump_to_bookmark(self, entry: Entry) -> None:
        if entry.parent is None:
            return
        self._activate_entry(entry.parent)
        if entry.session.table_id:
            self._choose_table(entry.session.table_id)
        self._go_to(entry.bookmark_offset)

    def _jump_to_source(self, entry: Entry) -> None:
        if entry.parent is None or entry.config is None:
            return
        self._activate_entry(entry.parent)
        self._go_to(getattr(entry.config.source, "start", 0))

    def _raw_menu(self, pos: QPoint) -> None:
        if self._doc is None:
            return
        menu = QMenu(self)
        sel = self._selection
        menu.addAction(
            "New Block from Selection…",
            lambda: self._new_block(*sel) if sel else self._new_block(),
        )
        menu.addAction("New Bookmark", self._new_bookmark)
        if sel:
            menu.addAction("Add to Table…", self._add_selection_to_table)
            menu.addAction("Search for Selection", self._search_selection)
            menu.addAction(
                "Copy Hex",
                lambda: QApplication.clipboard().setText(
                    " ".join(f"{b:02X}" for b in self._doc.data[sel[0] : sel[1]])
                ),
            )
            menu.addAction("Copy Text", self._copy_selection_text)
        menu.exec(pos)

    def _add_selection_to_table(self) -> None:
        if not self._selection or self._doc is None:
            return
        s, e = self._selection
        table_entry = self.tables_panel.entry_for_table(
            self.table_pick.currentData() or ""
        )
        if table_entry is None:
            table = Table("main")
            table_entry = self._add_memory_table(table, "new.tbl")
            self._choose_table("main")
        self._edit_table_entry(table_entry)
        self.table_editor.select_table(self.table_pick.currentData())
        from mapchar.core.bits import bytes_to_bits

        self.table_editor.prefill(bytes_to_bits(self._doc.data[s : min(e, s + 4)]))

    def _copy_selection_text(self) -> None:
        if not self._selection or self._doc is None:
            return
        tables = self._table_set()
        if tables is None:
            return
        s, e = self._selection
        r = decode(
            Bits(self._doc.data[s:e]), tables, 0, DecodeRules(end_terminated=False)
        )
        from mapchar.core.tokens import render

        QApplication.clipboard().setText(render(r.tokens))

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def _show_search(self) -> None:
        if self._doc is not None:
            self.search_window.set_data(self._doc.data)
        self.search_window.show()
        self.search_window.raise_()
        self.search_window.activateWindow()

    _find_last: tuple[bytes, int] | None = None

    def _find_bytes(self, again: bool = False) -> None:
        if self._doc is None:
            return
        if again and self._find_last:
            needle, start = self._find_last
        else:
            text, ok = QInputDialog.getText(
                self,
                "Find bytes",
                'Hex bytes, or "quoted text" through the start table:',
            )
            if not ok or not text.strip():
                return
            needle = self._needle_from(text.strip())
            if needle is None:
                return
            start = self._offset + 1
        pos = self._doc.data.find(needle, start)
        if pos < 0:
            pos = self._doc.data.find(needle, 0)
            if pos < 0:
                self.statusBar().showMessage("Not found", 3000)
                return
        self._find_last = (needle, pos + 1)
        self._select_bytes(pos, len(needle))

    def _needle_from(self, text: str) -> bytes | None:
        if text.startswith('"') and text.endswith('"') and len(text) >= 2:
            tables = self._table_set()
            if tables is None:
                self._error("Pick a start table to search for text.")
                return None
            out = ""
            for ch in text[1:-1]:
                bits = next(
                    (b for b, e in tables.start.entries.items() if e.text == ch), None
                )
                if bits is None:
                    self._error(f"{ch!r} is not in the start table.")
                    return None
                out += bits
            if len(out) % 8:
                self._error("The text does not end on a byte boundary.")
                return None
            return int(out, 2).to_bytes(len(out) // 8, "big") if out else b""
        try:
            return bytes.fromhex(text.replace("$", "").replace(" ", ""))
        except ValueError:
            self._error("Not hex bytes.")
            return None

    def _search_selection(self) -> None:
        if not self._selection or self._doc is None:
            return
        s, e = self._selection
        self._find_last = (self._doc.data[s:e], e)
        self._find_bytes(again=True)

    def _build_table_from_hit(self, hit: Hit) -> None:
        entries = entries_from_hit(hit)
        table_id = self.table_pick.currentData()
        target = self.tables_panel.entry_for_table(table_id or "")
        if (
            target is not None
            and QMessageBox.question(
                self,
                "Build table",
                f"Add {len(entries)} entries to @{table_id}? (No creates a new table)",
            )
            == QMessageBox.StandardButton.Yes
        ):
            table = next(t for t in target.tables if t.id == table_id)
            added = 0
            for entry in entries:
                if entry.bits not in table.entries:
                    table.add(entry)
                    added += 1
            self.workspace.stamp(target)
            self.statusBar().showMessage(f"Added {added} entries to @{table_id}", 4000)
        else:
            name = f"relsearch_{hit.offset:X}"
            table = Table(name)
            for entry in entries:
                table.add(entry)
            self._add_memory_table(table, f"{name}.tbl")
            self._choose_table(name)
        self.tables_panel.rebuild()
        self._refresh_view()

    # ------------------------------------------------------------------
    # Tables
    # ------------------------------------------------------------------

    def _show_table_editor(self) -> None:
        entry = self.tables_panel.entry_for_table(self.table_pick.currentData() or "")
        if entry is None:
            tables = [e for e in self.workspace.entries if e.kind is EntryKind.TABLE]
            entry = tables[0] if tables else None
        self._edit_table_entry(entry)

    def _edit_table_entry(self, entry: Entry | None) -> None:
        self.table_editor.set_entry(entry)
        if entry is not None and self.table_pick.currentData():
            self.table_editor.select_table(self.table_pick.currentData())
        notices = getattr(entry, "notices", None) if entry else None
        if notices:
            self.table_editor.status.setText("; ".join(n.message for n in notices[:5]))
        self.table_editor.show()
        self.table_editor.raise_()

    def _on_table_edited(self, entry: Entry) -> None:
        self.workspace.stamp(entry)
        self.tables_panel.rebuild()
        for e in self.workspace.entries:
            if e.doc is not None:
                e.doc.extraction_key = None
        self._refresh_view()

    def _save_table_entry(self, entry: Entry | None, ask: bool = False) -> None:
        if entry is None:
            return
        path = entry.path
        if ask or not path or entry.dialect != "native":
            path, _ = QFileDialog.getSaveFileName(
                self, "Save table as native", path or self._last_dir(), "Tables (*.tbl)"
            )
            if not path:
                return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(write_native(entry.tables))
        except OSError as exc:
            self._error(f"Cannot write {path}: {exc}")
            return
        entry.path = path
        entry.dialect = "native"
        entry.name = os.path.basename(path)
        self.workspace.mark_saved(entry)
        self.files_panel.refresh_labels()
        self.tables_panel.rebuild()
        self.statusBar().showMessage(f"Saved {path}", 4000)

    # ------------------------------------------------------------------
    # Dump
    # ------------------------------------------------------------------

    def _dump(self, all_blocks: bool = False) -> None:
        file_entry = self._current_file()
        if file_entry is None:
            self._error("Open a ROM and create a block first.")
            return
        blocks = [
            e for e in self.workspace.children(file_entry) if e.kind is EntryKind.BLOCK
        ]
        if (
            not all_blocks
            and self._entry is not None
            and self._entry.kind is EntryKind.BLOCK
        ):
            blocks = [self._entry]
        if not blocks:
            self._error("The file has no blocks.")
            return
        dialog = DumpDialog(self)
        dialog.all_blocks.setChecked(all_blocks)
        dialog.all_blocks.setEnabled(
            not all_blocks
            and len(blocks) == 1
            and len(self.workspace.children(file_entry)) > 1
        )
        if dialog.exec() != DumpDialog.DialogCode.Accepted:
            return
        if dialog.all_blocks.isChecked():
            blocks = [
                e
                for e in self.workspace.children(file_entry)
                if e.kind is EntryKind.BLOCK
            ]
        suggested = os.path.join(
            self._last_dir(),
            (blocks[0].name if len(blocks) == 1 else file_entry.name) + ".txt",
        )
        path, _ = QFileDialog.getSaveFileName(
            self, "Dump to script", suggested, "Scripts (*.txt);;All files (*)"
        )
        if not path:
            return
        payload = []
        for block in blocks:
            doc = self._load_document(block)
            if doc is None or block.config is None:
                continue
            tables = self.workspace.tables()
            ts = (
                TableSet.build(tables[block.config.table_id], tables)
                if block.config.table_id in tables
                else None
            )
            self._extract_current(block, doc, ts)
            payload.append((block.name, block.config, doc.strings))
        table_paths = [
            os.path.relpath(e.path, os.path.dirname(path))
            for e in self.workspace.entries
            if e.kind is EntryKind.TABLE and e.path
        ]
        text = write_script(
            payload,
            dialog.dump_mode(),
            rom=os.path.relpath(file_entry.path, os.path.dirname(path))
            if file_entry.path
            else None,
            tables=table_paths,
        )
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as exc:
            self._error(f"Cannot write {path}: {exc}")
            return
        self._remember_dir(path)
        self.statusBar().showMessage(
            f"Dumped {sum(len(p[2]) for p in payload)} strings to {path}", 5000
        )

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    def _project_dirty(self) -> bool:
        if self._saved_snapshot is None:
            return bool(self.workspace.entries)
        return self._snapshot() != self._saved_snapshot

    def _snapshot(self) -> str:
        import json

        base = os.path.dirname(self.project_path) if self.project_path else None
        d = project_dict(self.workspace.entries, None, base)
        return json.dumps(d, sort_keys=True)

    def _confirm_discard(self, what: str) -> bool:
        if not self._project_dirty():
            return True
        answer = QMessageBox.question(
            self,
            "Unsaved project",
            f"Save the project before you {what}?",
            QMessageBox.StandardButton.Save
            | QMessageBox.StandardButton.Discard
            | QMessageBox.StandardButton.Cancel,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self._save_project()
        return answer == QMessageBox.StandardButton.Discard

    def _new_project(self) -> None:
        if not self._confirm_discard("start a new project"):
            return
        self._entry = None
        self._doc = None
        self.workspace.replace([], None)
        self.undo_stack.clear()
        self.project_path = None
        self._saved_snapshot = None
        self._refresh_table_picks()
        self._refresh_view()

    def _open_project_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Project", self._last_dir(), "mapchar projects (*.mapchar)"
        )
        if path:
            self.open_project(path)

    def open_project(self, path: str) -> bool:
        if not self._confirm_discard("open another project"):
            return False
        try:
            loaded: LoadedProject = load_project(path)
        except ProjectError as exc:
            self._error(str(exc))
            return False
        self._entry = None
        self._doc = None
        for e in loaded.entries:
            if e.kind is EntryKind.TABLE and e.path:
                try:
                    with open(e.path, encoding="utf-8", errors="replace") as f:
                        tf = load_table_text(f.read(), e.path, e.dialect)
                    for t in tf.tables:
                        apply_charset(t, self.registry)
                    e.tables = tf.tables
                    e.dialect = tf.dialect
                except (OSError, MapcharError) as exc:
                    e.missing = True
                    loaded.warnings.append(f"{e.name}: {exc}")
        for index, states in loaded.strings.items():
            loaded.entries[index].pending_strings = states  # type: ignore[attr-defined]
        self.workspace.replace(loaded.entries, loaded.current)
        self.undo_stack.clear()
        self.project_path = path
        self._remember_dir(path)
        self._add_recent(path)
        self._saved_snapshot = self._snapshot()
        self._refresh_table_picks()
        if loaded.warnings:
            TextDialog("Project notices", "\n".join(loaded.warnings), self).exec()
        self._activate_entry(
            loaded.current
            or (self.workspace.files()[0] if self.workspace.files() else None)
        )
        return True

    def _save_project(self) -> bool:
        if not self.project_path:
            return self._save_project_as()
        return self._write_project(self.project_path)

    def _save_project_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Project",
            self.project_path or os.path.join(self._last_dir(), "project.mapchar"),
            "mapchar projects (*.mapchar)",
        )
        if not path:
            return False
        return self._write_project(path)

    def _write_project(self, path: str) -> bool:
        if self._entry is not None:
            self._entry.session.offset = self._offset
        try:
            save_project(path, self.workspace.entries, self.workspace.current)
        except OSError as exc:
            self._error(f"Cannot save project: {exc}")
            return False
        self.project_path = path
        self._saved_snapshot = self._snapshot()
        self._add_recent(path)
        self._remember_dir(path)
        self._update_title()
        self.statusBar().showMessage(f"Saved {path}", 4000)
        return True

    def _recent(self) -> list[str]:
        value = self.settings.value("recent", [])
        if isinstance(value, str):
            value = [value]
        return [str(v) for v in value or []]

    def _add_recent(self, path: str) -> None:
        recent = [p for p in self._recent() if p != path]
        recent.insert(0, path)
        self.settings.setValue("recent", recent[:MAX_RECENT])
        self._rebuild_recent()

    def _rebuild_recent(self) -> None:
        self.recent_menu.clear()
        for path in self._recent():
            self.recent_menu.addAction(path, lambda p=path: self.open_project(p))
        self.recent_menu.setEnabled(bool(self._recent()))

    # ------------------------------------------------------------------
    # Help
    # ------------------------------------------------------------------

    def _show_shortcuts(self) -> None:
        lines = []
        for menu in self.menuBar().findChildren(QMenu):
            for action in menu.actions():
                if action.shortcut().toString() and action.text():
                    lines.append(
                        f"{action.shortcut().toString():<14} "
                        f"{action.text().replace('&', '')}"
                    )
        lines += [
            "",
            "Raw view: PgUp/PgDn page · Up/Down row · -/+ byte · Home/End · "
            "drag to select · right-click menu",
        ]
        TextDialog("Shortcuts", "\n".join(lines), self).exec()

    def _about(self) -> None:
        QMessageBox.about(
            self,
            "mapchar",
            f"mapchar {__version__}\nA text viewer and editor for retro-game ROMs.",
        )

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, "mapchar", message)
