"""The main window: shell, menus, docks, and the entry/refresh cycle."""

from __future__ import annotations

import os

from PySide6.QtCore import QFileSystemWatcher, QPoint, QSettings, Qt
from PySide6.QtGui import QAction, QKeySequence, QPalette, QUndoStack
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
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from mapchar import APP_NAME, __version__
from mapchar.core.bits import Bits
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    FixedSource,
    RangeSource,
    Status,
    WriteMode,
)
from mapchar.core.context import (
    KEY_COMPLETE,
    KEY_CONSUMED,
    KEY_HEADER_SIZE,
    PipelineContext,
)
from mapchar.core.document import Document
from mapchar.core.errors import MapcharError
from mapchar.core.font import Font, TextBox
from mapchar.core.mapping import resolve_mapping
from mapchar.core.table import EntryKind as TableEntryKind
from mapchar.core.table import Table, TableSet
from mapchar.engines.decode import DecodeRules, EndedBy, decode
from mapchar.engines.layout import layout as layout_glyphs
from mapchar.engines.layout import wrap as wrap_text
from mapchar.engines.pointers import discover
from mapchar.engines.relsearch import Hit, entries_from_hit
from mapchar.pipeline.exchange.atlas import read_atlas, write_atlas
from mapchar.pipeline.exchange.cartographer import (
    parse_command_file,
    shift_config,
    write_command_file,
)
from mapchar.pipeline.exchange.script_import import apply_script
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, block_bound, layout_block
from mapchar.pipeline.pipeline import (
    FileRef,
    PathwayConfig,
    deposit,
    encode_for_save,
    load,
)
from mapchar.plugins.base import Stage
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.registry import Registry, default_registry
from mapchar.project.formats.script import parse_script, write_script
from mapchar.project.formats.table_legacy import load_table_text
from mapchar.project.formats.table_native import write_native
from mapchar.project.formats.translator import (
    apply_records,
    read_delimited,
    read_po,
    records_for,
    write_delimited,
    write_po,
)
from mapchar.project.projectfile import (
    LoadedProject,
    ProjectError,
    load_project,
    project_dict,
    save_project,
)
from mapchar.project.workspace import Entry, EntryKind, Workspace
from mapchar.ui.decompress_window import DecompressWindow
from mapchar.ui.dialogs import (
    BlockDialog,
    DiscoveryDialog,
    DumpDialog,
    TextDialog,
    parse_hex,
)
from mapchar.ui.files_panel import FilesPanel
from mapchar.ui.find_replace import FindReplaceDialog
from mapchar.ui.fonts_panel import FontsPanel
from mapchar.ui.glyphs import Glyph
from mapchar.ui.hex_panel import HexPanel
from mapchar.ui.icon_font import glyph_icon
from mapchar.ui.preview_window import PreviewWindow
from mapchar.ui.raw_widget import BYTES_PER_ROW, RawWidget, RowModel
from mapchar.ui.scan_window import ScanWindow
from mapchar.ui.search_window import SearchWindow
from mapchar.ui.strings_view import RowData, StringsView
from mapchar.ui.table_editor import TableEditor
from mapchar.ui.tables_panel import TablesPanel
from mapchar.ui.text_widget import TextWidget, text_model
from mapchar.ui.undo_commands import (
    BytesCommand,
    EntryCommand,
    OffsetCommand,
    StringFieldCommand,
)

MAX_RECENT = 10
TEXT_WINDOW_BYTES = 4096
"""How many bytes the text mode decodes from the offset."""
DISPLAY_MODE_KEY = "view/display_mode"


class MainWindow(QMainWindow):
    def __init__(
        self,
        registry: Registry | None = None,
        parent: QWidget | None = None,
        reload_plugins=None,
        plugin_dir: str | None = None,
        plugin_issues=(),
    ):
        super().__init__(parent)
        self.registry = registry or default_registry()
        self._reload_plugins = reload_plugins
        self.plugin_dir = plugin_dir
        self._plugin_issues = list(plugin_issues)
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
        self.setWindowTitle(APP_NAME)
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

        self.fonts_panel = FontsPanel(self.workspace)
        fonts_dock = QDockWidget("Fonts", self)
        fonts_dock.setObjectName("fonts_dock")
        fonts_dock.setWidget(self.fonts_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, fonts_dock)
        self.tabifyDockWidget(tables_dock, fonts_dock)
        tables_dock.raise_()
        self.fonts_dock = fonts_dock

        self.hex_panel = HexPanel()
        hex_dock = QDockWidget("Hex", self)
        hex_dock.setObjectName("hex_dock")
        hex_dock.setWidget(self.hex_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, hex_dock)
        hex_dock.hide()
        self.hex_dock = hex_dock

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
        self.compression_pick = QComboBox()
        self._fill_compression_pick()
        codecs.addWidget(QLabel(" Container "))
        codecs.addWidget(self.container_pick)
        codecs.addWidget(QLabel("  Reshape "))
        codecs.addWidget(self.reshape_pick)
        codecs.addWidget(QLabel("  Compression "))
        codecs.addWidget(self.compression_pick)
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
        self.strings = StringsView()
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
        # The row steps wear the bundled icon font; the byte and page steps
        # stay words, since the font has no mark that says "byte" or "page".
        self._step_icons: list[tuple[QPushButton, Glyph]] = []
        for text, glyph, delta, tip in (
            ("Home", None, "home", "Start of file (Home)"),
            ("Pg Up", None, "page-up", "Page up (PgUp)"),
            ("", Glyph.ARROW_UP, -BYTES_PER_ROW, "Row up (Up)"),
            ("−B", None, -1, "Byte back (-)"),
            ("+B", None, 1, "Byte forward (+)"),
            ("", Glyph.ARROW_DOWN, BYTES_PER_ROW, "Row down (Down)"),
            ("Pg Dn", None, "page-down", "Page down (PgDn)"),
            ("End", None, "end", "End of file (End)"),
        ):
            b = QPushButton(text)
            b.setToolTip(tip)
            b.setFixedWidth(48)
            b.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            if glyph is not None:
                self._step_icons.append((b, glyph))
            if delta in ("page-up", "page-down"):
                d = -1 if delta == "page-up" else 1
                b.clicked.connect(
                    lambda _=False, d=d: self._move(d * self.raw.visible_bytes())
                )
            elif isinstance(delta, int):
                b.clicked.connect(lambda _=False, d=delta: self._move(d))
            elif delta == "home":
                b.clicked.connect(lambda: self._go_to(0))
            else:
                b.clicked.connect(self._go_end)
            nl.addWidget(b)
        self._bake_icons()
        nl.addStretch(1)
        self.nav_status = QLabel("")
        nl.addWidget(self.nav_status)
        self.nav_bar = nav
        layout.addWidget(nav)
        self.setCentralWidget(central)

        self.search_window = SearchWindow(self)
        self.scan_window = ScanWindow(self)
        self.decompress_window = DecompressWindow(self)
        self.preview_window = PreviewWindow(self)
        self.table_editor = TableEditor(self)
        self.find_replace = FindReplaceDialog(self)

        # Signals.
        self.files_panel.entry_activated.connect(self._activate_entry)
        self.files_panel.entry_double_clicked.connect(self._on_entry_double)
        self.files_panel.context_menu_requested.connect(self._files_menu)
        self.files_panel.remove_requested.connect(self._remove_entries)
        self.tables_panel.table_chosen.connect(self._choose_table)
        self.tables_panel.edit_requested.connect(self._edit_table_entry)
        self.fonts_panel.edit_requested.connect(self._edit_font_entry)
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
        self.strings.row_selected.connect(self._on_string_row)
        self.strings.translation_edited.connect(self._on_translation_edited)
        self.strings.notes_edited.connect(self._on_notes_edited)
        self.strings.draft_changed.connect(self._on_draft)
        self.strings.context_menu_requested.connect(self._strings_menu)
        self.search_window.go_to.connect(self._select_bytes)
        self.search_window.build_table.connect(self._build_table_from_hit)
        self.scan_window.go_to.connect(self._select_bytes)
        self.scan_window.new_block.connect(self._block_from_region)
        self.compression_pick.currentIndexChanged.connect(self._refresh_view)
        self.decompress_window.jump_next.connect(self._jump_next_structure)
        self.decompress_window.scan_next.connect(self._scan_next_structure)
        self.decompress_window.to_block.connect(self._structure_to_block)
        self.preview_window.font_changed.connect(self._on_font_changed)
        self.preview_window.box_changed.connect(self._on_box_changed)
        self.preview_window.wrap_requested.connect(self._wrap_selected)
        self.table_editor.changed.connect(self._on_table_edited)
        self.table_editor.save_requested.connect(self._save_table_entry)
        self.hex_panel.go_to_requested.connect(self._go_to)
        self.hex_panel.overtype_requested.connect(self.overtype_bytes)
        self.hex_panel.find_requested.connect(self._find_text_or_bytes)
        self.hex_dock.visibilityChanged.connect(lambda v: v and self._sync_hex_panel())
        self.find_replace.find_next.connect(self._fr_find_next)
        self.find_replace.replace_one.connect(self._fr_replace_one)
        self.find_replace.replace_all.connect(self._fr_replace_all)
        self.workspace.on_current_changed.append(lambda e: self._update_title())
        self.table_watcher = QFileSystemWatcher(self)
        self.table_watcher.fileChanged.connect(self._on_table_file_changed)
        self.workspace.on_added.append(self._watch_table)
        self.workspace.on_reset.append(self._rewatch_tables)

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
        act(file_menu, "Open Font…", self._open_font_dialog)
        file_menu.addSeparator()
        act(file_menu, "New Block…", self._new_block, "Ctrl+Shift+B")
        act(file_menu, "New Bookmark", self._new_bookmark, "Ctrl+B")
        act(file_menu, "Dump…", self._dump, "Ctrl+D")
        file_menu.addSeparator()
        act(file_menu, "Write", self._write_current, "Ctrl+W")
        act(file_menu, "Write All", self._write_all, "Ctrl+Shift+W")
        file_menu.addSeparator()
        act(file_menu, "New Project", self._new_project, "Ctrl+N")
        act(file_menu, "Open Project…", self._open_project_dialog, "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Open Recent")
        file_menu.addSeparator()
        import_menu = file_menu.addMenu("Import")
        act(import_menu, "Script…", lambda: self._import("script"))
        act(import_menu, "TSV / CSV…", lambda: self._import("delimited"))
        act(import_menu, "PO…", lambda: self._import("po"))
        import_menu.addSeparator()
        act(import_menu, "Cartographer command file…", self._import_cartographer_dialog)
        act(import_menu, "Atlas script…", self._import_atlas_dialog)
        export_menu = file_menu.addMenu("Export")
        act(export_menu, "TSV…", lambda: self._export("tsv"))
        act(export_menu, "CSV…", lambda: self._export("csv"))
        act(export_menu, "PO…", lambda: self._export("po"))
        export_menu.addSeparator()
        act(export_menu, "Atlas script…", self._export_atlas)
        act(export_menu, "Cartographer command file…", self._export_cartographer)
        act(file_menu, "Save Project", self._save_project, "Ctrl+S")
        act(file_menu, "Save Project As…", self._save_project_as, "Ctrl+Shift+S")
        file_menu.addSeparator()
        act(file_menu, "Open plugins folder…", self._open_plugins_folder)
        act(file_menu, "Refresh plugins", self._refresh_plugins, "F5")
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
        act(edit_menu, "Find and Replace…", self._show_find_replace, "Ctrl+H")
        edit_menu.addSeparator()
        act(edit_menu, "Revert Selected Strings", self._revert_selected)
        act(edit_menu, "Toggle Review on Selected", self._toggle_review_selected)
        act(edit_menu, "Copy Original to Empty Translations", self._copy_originals)
        edit_menu.addSeparator()
        act(edit_menu, "Find Pointers…", self._find_pointers, "Ctrl+Shift+P")

        view_menu = bar.addMenu("&View")
        act(view_menu, "Raw", lambda: self.tabs.setCurrentIndex(0), "Ctrl+1")
        act(view_menu, "Strings", lambda: self.tabs.setCurrentIndex(1), "Ctrl+2")
        act(
            view_menu, "Aligned / Text display", self.mode_button.toggle, "Ctrl+Shift+A"
        )
        view_menu.addSeparator()
        act(view_menu, "Table Editor…", self._show_table_editor, "Ctrl+Shift+T")
        act(view_menu, "Preview…", self._show_preview, "Ctrl+P")
        view_menu.addSeparator()
        self.theme_light = act(
            view_menu, "Light theme", lambda: self._set_theme("light")
        )
        self.theme_dark = act(view_menu, "Dark theme", lambda: self._set_theme("dark"))

        search_menu = bar.addMenu("&Search")
        act(search_menu, "Search window…", self._show_search, "Ctrl+Shift+F")
        act(search_menu, "Scan for text…", self._show_scan, "Ctrl+Shift+S")
        act(search_menu, "Find bytes…", self._find_bytes, "Ctrl+F")
        act(search_menu, "Find next", lambda: self._find_bytes(again=True), "F3")

        panels_menu = bar.addMenu("&Panels")
        panels_menu.addAction(self.files_dock.toggleViewAction())
        panels_menu.addAction(self.tables_dock.toggleViewAction())
        panels_menu.addAction(self.fonts_dock.toggleViewAction())
        panels_menu.addAction(self.hex_dock.toggleViewAction())
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
        self.fonts_dock.show()
        self.hex_dock.hide()

    def _set_theme(self, name: str) -> None:
        from mapchar.ui.theme import apply_theme

        apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        self._bake_icons()

    def _bake_icons(self) -> None:
        """Stamp the navigation bar's step arrows in the theme's button-text
        color. Pixmaps, so re-run on a theme switch."""
        color = self.palette().color(QPalette.ColorRole.ButtonText)
        ratio = self.devicePixelRatioF()
        for button, glyph in self._step_icons:
            button.setIcon(glyph_icon(glyph, color, ratio=ratio))

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

    def _open_font_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Font", self._last_dir(), "Images (*.png *.bmp);;All files (*)"
        )
        if path:
            self.open_font(path)

    def open_font(self, path: str) -> Entry:
        self._remember_dir(path)
        entry = Entry(EntryKind.FONT, os.path.basename(path), path, font=Font(path))
        self._push_add(entry)
        return entry

    def _edit_font_entry(self, entry: Entry) -> None:
        """Open the Preview window on this font, binding the current block to it."""
        fonts = self._fonts()
        if entry not in fonts:
            return
        block = self._entry
        if block is not None and block.kind is EntryKind.BLOCK:
            from dataclasses import replace

            box = block.box or TextBox()
            block.box = replace(box, font_index=fonts.index(entry))
            self._sync_preview(force=True)
        else:
            self.preview_window.set_font(entry.font)
        self.preview_window.show()
        self.preview_window.tabs.setCurrentIndex(1)
        self.preview_window.raise_()

    def _fonts(self) -> list[Entry]:
        return [e for e in self.workspace.entries if e.kind is EntryKind.FONT]

    def _bound_font(self, block: Entry | None) -> Entry | None:
        if block is None or block.box is None or block.box.font_index is None:
            return None
        fonts = self._fonts()
        i = block.box.font_index
        return fonts[i] if 0 <= i < len(fonts) else None

    def _show_preview(self) -> None:
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK:
            self._error("Select a block to preview.")
            return
        fonts = self._fonts()
        if entry.box is None:
            entry.box = TextBox(font_index=0 if fonts else None)
        elif entry.box.font_index is None and fonts:
            from dataclasses import replace

            entry.box = replace(entry.box, font_index=0)
        if not fonts:
            self._error("Open a font (File ▸ Open Font…) first.")
        self._sync_preview(force=True)
        self.preview_window.show()
        self.preview_window.raise_()

    def _sync_preview(self, force: bool = False) -> None:
        if not (force or self.preview_window.isVisible()):
            return
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK or entry.doc is None:
            return
        font_entry = self._bound_font(entry)
        self.preview_window.set_font(font_entry.font if font_entry else None)
        tables = self._table_set()
        labels = sorted(
            {lb for t in (tables.tables.values() if tables else ()) for lb in t.labels}
        )
        self.preview_window.set_box(entry.box or TextBox(), labels)
        selected = self.strings.selected_indices()
        rec = self._string(entry, selected[0]) if selected else None
        if rec is None and entry.doc.strings:
            rec = entry.doc.strings[0]
        if rec is not None:
            source = rec.translation if rec.translation is not None else rec.original
            self.preview_window.show_string(source, f"{entry.name} #{rec.index}")

    def _on_font_changed(self, font: Font) -> None:
        font_entry = self._bound_font(self._entry)
        if font_entry is None:
            return
        font_entry.font = font
        self.workspace.stamp(font_entry)
        self.preview_window.set_font(font)
        self._refresh_view()

    def _on_box_changed(self, box: TextBox) -> None:
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK:
            return
        from dataclasses import replace

        entry.box = replace(
            box, font_index=entry.box.font_index if entry.box else box.font_index
        )
        self.workspace.stamp(entry)
        self.preview_window._box = entry.box
        self.preview_window._paint()
        self._refresh_view()

    def _overflow_status(self, rec, entry: Entry) -> bool:
        font_entry = self._bound_font(entry)
        if font_entry is None or font_entry.font is None or entry.box is None:
            return False
        source = rec.translation if rec.translation is not None else rec.original
        return layout_glyphs(source, font_entry.font, entry.box).overflows

    def _wrap_selected(self) -> None:
        entry = self._entry
        font_entry = self._bound_font(entry)
        if (
            entry is None
            or font_entry is None
            or font_entry.font is None
            or entry.box is None
        ):
            self._error("Bind a font and a text box first.")
            return
        newline = next(
            (lb for lb, e in entry.box.effects.items() if e.effect.value == "newline"),
            None,
        )
        page = next(
            (lb for lb, e in entry.box.effects.items() if e.effect.value == "page"),
            None,
        )
        if newline is None:
            self._error("Give one code the 'newline' effect in the Codes tab first.")
            return
        indices = self.strings.selected_indices() or [
            r.index for r in entry.doc.strings[:1]
        ]
        self.undo_stack.beginMacro("Wrap")
        for index in indices:
            rec = self._string(entry, index)
            if rec is None:
                continue
            text = (
                rec.translation if rec.translation is not None else rec.original_text()
            )
            wrapped, _ = wrap_text(text, font_entry.font, entry.box, newline, page)
            if wrapped != text:
                self._on_translation_edited_for(entry, index, wrapped)
        self.undo_stack.endMacro()
        self._sync_preview()

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
                menu.addAction("Container Info…", lambda: self._container_info(entry))
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

    def _container_info(self, entry: Entry) -> None:
        """What the container decoded from the file, from a read of its own."""
        from mapchar.plugins.base import ReadSource

        plugin = self.registry.plugin(Stage.CONTAINER, entry.container_id)
        if plugin is None:
            self._error(f"Container {entry.container_id!r} is not available.")
            return
        try:
            data = FileRef(entry.paths).read()
        except OSError as exc:
            self._error(str(exc))
            return
        ctx = PipelineContext()
        lines = [f"Container: {plugin.info.name} ({plugin.info.id})"]
        try:
            plugin.read(ReadSource(data, entry.paths), ctx)
        except Exception as exc:  # noqa: BLE001 - report, never crash
            lines.append(f"read failed: {exc}")
        describe = getattr(plugin, "describe", None)
        if callable(describe):
            try:
                for key, value in describe(ReadSource(data, entry.paths), ctx).items():
                    lines.append(f"{key}: {value}")
            except Exception as exc:  # noqa: BLE001
                lines.append(f"describe failed: {exc}")
        for key, value in ctx.values.items():
            lines.append(f"{key}: {value}")
        for notice in ctx.notices:
            lines.append(f"notice: {notice}")
        TextDialog(f"Container Info — {entry.name}", "\n".join(lines), self).exec()

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
                f"{entry.name} changed on disk but has edits here. "
                "Reload and lose them?",
            )
            if answer != QMessageBox.StandardButton.Yes:
                return
        self.reload_table(entry)

    def reload_table(self, entry: Entry) -> None:
        if not entry.path:
            return
        try:
            with open(entry.path, encoding="utf-8", errors="replace") as f:
                tf = load_table_text(f.read(), entry.path, entry.dialect)
            for t in tf.tables:
                apply_charset(t, self.registry)
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot reload {entry.name}: {exc}")
            return
        entry.tables = tf.tables
        entry.dialect = tf.dialect
        self.workspace.mark_saved(entry)
        for e in self.workspace.entries:
            if e.doc is not None:
                e.doc.extraction_key = None
        self._refresh_table_picks()
        self.tables_panel.rebuild()
        self._refresh_view()
        self.statusBar().showMessage(f"Reloaded {entry.name}", 4000)

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
                if entry.compression_id:
                    entry.doc = self._load_compressed_block(entry, parent_doc)
                else:
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

    def _load_compressed_block(
        self, entry: Entry, parent_doc: Document
    ) -> Document | None:
        plugin = self.registry.resolve_stage(Stage.COMPRESSION, entry.compression_id)
        bind = getattr(plugin, "bind_tree", None)
        if callable(bind):
            bind(parent_doc.data)
        ctx = PipelineContext()
        end = (
            entry.slice_offset + entry.slice_length
            if entry.slice_length
            else len(parent_doc.data)
        )
        try:
            data = plugin.decompress(parent_doc.data[entry.slice_offset : end], ctx)
        except Exception as exc:  # noqa: BLE001 - a scheme may reject the bytes
            self._error(f"{entry.name}: cannot decompress: {exc}")
            return None
        consumed = ctx.get(KEY_CONSUMED)
        if not entry.slice_length and consumed:
            entry.slice_length = int(consumed)
        from mapchar.plugins.base import writes_back

        can_write = writes_back(plugin, Stage.COMPRESSION)
        doc = Document(data, ctx, parent_doc.writable and can_write)
        doc.missing_plugins = list(parent_doc.missing_plugins)
        if not can_write:
            doc.missing_plugins.append(f"{entry.compression_id} (no compress)")
        return doc

    def _fill_compression_pick(self) -> None:
        self.compression_pick.blockSignals(True)
        self.compression_pick.clear()
        self.compression_pick.addItem("None", None)
        for plugin in self.registry.plugins(Stage.COMPRESSION):
            self.compression_pick.addItem(plugin.info.name, plugin.info.id)
        self.compression_pick.blockSignals(False)

    def _decompress_at(self, doc: Document, offset: int):
        """Run the picked scheme from ``offset``: ``(data, consumed, complete)``."""
        cid = self.compression_pick.currentData()
        if not cid:
            return None
        plugin = self.registry.plugin(Stage.COMPRESSION, cid)
        if plugin is None:
            return None
        bind = getattr(plugin, "bind_tree", None)
        if callable(bind):
            bind(doc.data)
        ctx = PipelineContext()
        try:
            data = plugin.decompress(doc.data[offset:], ctx)
        except Exception:  # noqa: BLE001 - not a structure here
            return None
        if not data:
            return None
        return data, int(ctx.get(KEY_CONSUMED) or 0), bool(ctx.get(KEY_COMPLETE))

    def _refresh_decompress_preview(self, doc: Document, tables) -> None:
        entry = self._entry
        if entry is None or (entry.kind is EntryKind.BLOCK and entry.compression_id):
            self.decompress_window.hide()
            return
        result = self._decompress_at(doc, self._offset)
        if result is None:
            self.decompress_window.hide()
            return
        data, consumed, complete = result
        tokens = []
        if tables is not None:
            bits = Bits(data[:4096])
            pos = 0
            while pos < bits.length:
                r = decode(bits, tables, pos, DecodeRules(end_terminated=True))
                tokens.extend(r.tokens)
                if r.end_bit <= pos or r.ended_by in (EndedBy.DATA, EndedBy.LIMIT):
                    break
                pos = r.end_bit
        model = RowModel(0, data[:4096], tokens, set(), len(data))
        status = (
            f"{consumed:,} compressed bytes at {self._offset:X} → {len(data):,} bytes"
        )
        if not complete:
            status += "  ·  no end marker before the window's edge"
        self.decompress_window.show_result(model, status, complete)
        if not self.decompress_window.isVisible():
            self.decompress_window.show()

    def _jump_next_structure(self) -> None:
        if self._doc is None:
            return
        result = self._decompress_at(self._doc, self._offset)
        if result is not None and result[1] > 0:
            self._go_to(self._offset + result[1])

    def _scan_next_structure(self) -> None:
        doc = self._doc
        if doc is None or not self.compression_pick.currentData():
            return
        start = self._offset + 1
        for at in range(start, doc.size):
            if at % 256 == 0:
                self.statusBar().showMessage(f"Scanning… {at:X}")
                QApplication.processEvents()
            result = self._decompress_at(doc, at)
            if result is not None and result[2] and len(result[0]) >= 16:
                self.statusBar().showMessage(f"Structure at {at:X}", 5000)
                self._go_to(at)
                return
        self.statusBar().showMessage("No further structure found", 5000)

    def _structure_to_block(self) -> None:
        file_entry = self._current_file()
        doc = self._doc
        if file_entry is None or doc is None:
            return
        result = self._decompress_at(doc, self._offset)
        if result is None:
            return
        data, consumed, _ = result
        table_ids = list(self.workspace.tables())
        if not table_ids:
            self._error("Load a table first.")
            return
        cfg = BlockConfig(
            RangeSource(0, len(data)),
            EndToken(),
            self.table_pick.currentData() or table_ids[0],
        )
        dialog = BlockDialog(
            table_ids,
            cfg,
            f"Compressed {self._offset:X}",
            self,
            self.registry.ids(Stage.MAPPING),
        )
        if dialog.exec() != BlockDialog.DialogCode.Accepted:
            return
        entry = Entry(
            EntryKind.BLOCK,
            dialog.name.text().strip() or f"Compressed {self._offset:X}",
            file_entry.path,
            parent=file_entry,
            config=dialog.config(),
            compression_id=self.compression_pick.currentData(),
            slice_offset=self._offset,
            slice_length=consumed,
        )
        self._push_add(entry)
        self._activate_entry(entry)

    def _open_plugins_folder(self) -> None:
        if not self.plugin_dir:
            self._error("No plugin folder is configured.")
            return
        self._reveal(os.path.join(self.plugin_dir, "README.txt"))

    def _refresh_plugins(self) -> None:
        if self._reload_plugins is None:
            self.statusBar().showMessage(
                "Plugins cannot be reloaded in this session", 4000
            )
            return
        project_dir = os.path.dirname(self.project_path) if self.project_path else None
        registry, issues = self._reload_plugins(project_dir)
        self.registry = registry
        self._plugin_issues = list(issues)
        for pick, stage, none_label in (
            (self.container_pick, Stage.CONTAINER, None),
            (self.reshape_pick, Stage.RESHAPE, "None"),
        ):
            pick.blockSignals(True)
            pick.clear()
            if none_label:
                pick.addItem(none_label, None)
            for plugin in self.registry.plugins(stage):
                pick.addItem(plugin.info.name, plugin.info.id)
            pick.blockSignals(False)
        self._fill_compression_pick()
        for e in self.workspace.entries:
            e.doc = None
            if e.kind is EntryKind.TABLE:
                for t in e.tables:
                    if hasattr(t, "_charset_applied"):
                        del t._charset_applied
                    apply_charset(t, self.registry)
        if self._entry is not None:
            self._doc = self._load_document(self._entry)
            self._restore_session()
        self._refresh_view()
        if self._plugin_issues:
            TextDialog(
                "Plugin issues", "\n".join(str(i) for i in self._plugin_issues), self
            ).exec()
        else:
            self.statusBar().showMessage("Plugins refreshed", 3000)

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
            self.strings.set_rows([])
            self.nav_status.setText("")
            self.offset_box.setText("")
            self._update_title()
            return
        total = doc.size
        self._offset = max(0, min(self._offset, max(total - 1, 0)))
        self.offset_box.setText(f"{self._offset:X}")
        tables = self._table_set()
        if is_block:
            self._extract_current(entry, doc, tables)
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
        pointer_bytes: set[int] = set()
        if is_block and entry.doc is not None:
            for rec in entry.doc.strings:
                for p in rec.pointers:
                    for b in range(p.address, p.address + p.size):
                        rel = b - self._offset
                        if 0 <= rel < len(data):
                            pointer_bytes.add(rel)
        self.raw.set_model(
            RowModel(self._offset, data, tokens, string_starts, total, pointer_bytes)
        )
        self._refresh_text_mode(doc, tables)
        self._refresh_decompress_preview(doc, tables)
        if is_block:
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
        self.scan_window.set_source(doc.data, tables)
        self._sync_hex_panel()
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
            ex = extract(doc.data, cfg, tables, self.registry)
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
        entry = self._entry
        tables = self._table_set()
        labels: list[str] = []
        if tables is not None:
            seen = set()
            for t in tables.tables.values():
                for label in t.labels:
                    if label not in seen:
                        seen.add(label)
                        labels.append(label)
        self.strings.set_labels(sorted(labels))
        self.strings.set_rows(self._row_data(entry, doc, tables))

    def _row_data(self, entry, doc: Document, tables) -> list[RowData]:
        cfg = entry.config if entry is not None else None
        result = None
        if cfg is not None and tables is not None and doc.strings:
            result = layout_block(doc.data, cfg, tables, doc.strings, self.registry)
        rows = []
        for rec in doc.strings:
            rows.append(self._row_for(rec, cfg, result, doc.strings))
        return rows

    def _row_for(self, rec, cfg, result, strings=()) -> RowData:
        used, room, problem = rec.length, self._room(rec, cfg, strings), ""
        status = rec.status.value
        if result is not None:
            enc = result.encoded.get(rec.index)
            if enc is not None and enc.problem is None:
                used = len(enc.data)
            problems = [p for p in result.problems if p.index == rec.index]
            if problems:
                problem = problems[0].message
                status = "too long" if problems[0].over else "invalid"
        if status in ("untouched", "edited", "review") and self._entry is not None:
            if self._overflow_status(rec, self._entry):
                status = "overflows box"
        return RowData(
            rec.index,
            rec.start,
            rec.original_text(),
            rec.translation,
            used,
            room,
            status,
            rec.notes,
            problem,
            " ".join(f"{p.address:X}" for p in rec.pointers),
        )

    @staticmethod
    def _room(rec, cfg, strings=()) -> int:
        if cfg is None:
            return rec.length
        if isinstance(cfg.string_type, FixedLength):
            return cfg.string_type.length
        if isinstance(cfg.source, FixedSource):
            return cfg.source.length
        if cfg.effective_write_mode is WriteMode.PACKED:
            return max(block_bound(cfg, list(strings)) - rec.start, 0)
        return rec.length

    def _refresh_string_row(self, entry, index: int) -> None:
        doc = entry.doc
        if doc is None:
            return
        rec = next((r for r in doc.strings if r.index == index), None)
        if rec is None:
            return
        tables = self._table_set()
        result = None
        if tables is not None and entry.config is not None:
            result = layout_block(
                doc.data, entry.config, tables, doc.strings, self.registry
            )
        self.strings.update_row(self._row_for(rec, entry.config, result, doc.strings))
        self._sync_preview()

    # ------------------------------------------------------------------
    # String edits
    # ------------------------------------------------------------------

    def _string(self, entry, index: int):
        if entry is None or entry.doc is None:
            return None
        return next((r for r in entry.doc.strings if r.index == index), None)

    def _on_translation_edited(self, index: int, text: str) -> None:
        entry = self._entry
        rec = self._string(entry, index)
        if rec is None:
            return
        after = text if text.strip() else None
        if after is not None and after.replace("\n", "") == rec.original_text().replace(
            "\n", ""
        ):
            after = None
        if after == rec.translation:
            self._refresh_string_row(entry, index)
            return
        self.undo_stack.push(
            StringFieldCommand(
                self, entry, index, "translation", rec.translation, after
            )
        )

    def _on_notes_edited(self, index: int, text: str) -> None:
        entry = self._entry
        rec = self._string(entry, index)
        if rec is None or text == rec.notes:
            return
        self.undo_stack.push(
            StringFieldCommand(self, entry, index, "notes", rec.notes, text)
        )

    def apply_string_field(self, entry, index: int, field: str, value) -> None:
        rec = self._string(entry, index)
        if rec is None:
            return
        if field == "translation":
            rec.translation = value
            if value is None:
                rec.status = Status.UNTOUCHED
            elif rec.status is Status.UNTOUCHED:
                rec.status = Status.EDITED
        elif field == "notes":
            rec.notes = value
        elif field == "status":
            rec.status = Status(value)
        self.workspace.stamp(entry)
        if entry is self._entry:
            self._refresh_string_row(entry, index)
            self.files_panel.refresh_labels()
        else:
            self._activate_entry(entry)
        self._update_title()

    def _on_draft(self, text: str) -> None:
        entry = self._entry
        tables = self._table_set()
        if entry is None or entry.config is None or tables is None:
            return
        from mapchar.engines.encode import encode

        try:
            r = encode(
                text,
                tables,
                end_terminated=isinstance(entry.config.string_type, EndToken),
                ends=entry.config.strings_per_pointer,
            )
            n = -(-len(r.bits) // 8)
            self.statusBar().showMessage(f"{n} byte(s)")
        except MapcharError as exc:
            self.statusBar().showMessage(str(exc))

    def _revert_selected(self) -> None:
        for index in self.strings.selected_indices():
            rec = self._string(self._entry, index)
            if rec is not None and rec.translation is not None:
                self.undo_stack.push(
                    StringFieldCommand(
                        self, self._entry, index, "translation", rec.translation, None
                    )
                )

    def _toggle_review_selected(self) -> None:
        for index in self.strings.selected_indices():
            rec = self._string(self._entry, index)
            if rec is None:
                continue
            new = Status.EDITED if rec.status is Status.REVIEW else Status.REVIEW
            if new is Status.EDITED and rec.translation is None:
                new = Status.UNTOUCHED
            self.undo_stack.push(
                StringFieldCommand(
                    self, self._entry, index, "status", rec.status.value, new.value
                )
            )

    def _copy_originals(self) -> None:
        entry = self._entry
        if entry is None or entry.doc is None:
            return
        self.undo_stack.beginMacro("Copy originals")
        for rec in entry.doc.strings:
            if rec.translation is None:
                self.undo_stack.push(
                    StringFieldCommand(
                        self, entry, rec.index, "translation", None, rec.original_text()
                    )
                )
        self.undo_stack.endMacro()

    def _strings_menu(self, indices: list[int], pos: QPoint) -> None:
        menu = QMenu(self)
        menu.addAction("Revert to original", self._revert_selected)
        menu.addAction("Toggle review", self._toggle_review_selected)
        if indices:
            rec = self._string(self._entry, indices[0])
            if rec is not None:
                menu.addAction(
                    "Copy original",
                    lambda: QApplication.clipboard().setText(rec.original_text()),
                )
        menu.exec(pos)

    # ------------------------------------------------------------------
    # Writing
    # ------------------------------------------------------------------

    def _dirty_blocks(self) -> list[Entry]:
        return [
            e for e in self.workspace.entries if e.kind is EntryKind.BLOCK and e.dirty
        ]

    def _write_current(self) -> None:
        entry = self._entry
        if entry is None:
            return
        if entry.kind is EntryKind.BLOCK:
            self._write_blocks([entry])
        elif entry.kind is EntryKind.FILE:
            blocks = [b for b in self.workspace.children(entry) if b.dirty]
            if entry.dirty or blocks:
                self._write_blocks(blocks, files=[entry])
            else:
                self.statusBar().showMessage("Nothing to write", 3000)

    def _write_all(self) -> bool:
        blocks = self._dirty_blocks()
        files = [e for e in self.workspace.files() if e.dirty]
        if not blocks and not files:
            self.statusBar().showMessage("Nothing to write", 3000)
            return True
        return self._write_blocks(blocks, files=files)

    def _write_blocks(
        self, blocks: list[Entry], files: list[Entry] | None = None
    ) -> bool:
        """Lay every block out over its file and write the files that changed."""
        by_file: dict[int, tuple[Entry, list[Entry]]] = {}
        for b in blocks:
            if b.parent is None:
                continue
            by_file.setdefault(id(b.parent), (b.parent, []))[1].append(b)
        for f in files or []:
            by_file.setdefault(id(f), (f, []))
        tables = self.workspace.tables()
        ok = True
        for file_entry, file_blocks in by_file.values():
            parent_doc = self._load_document(file_entry)
            if parent_doc is None:
                ok = False
                continue
            new_data = parent_doc.data
            problems: list[str] = []
            written_blocks = []
            for block in file_blocks:
                doc = self._load_document(block)
                if doc is None or block.config is None:
                    continue
                if block.config.table_id not in tables:
                    problems.append(
                        f"{block.name}: table @{block.config.table_id} is not loaded"
                    )
                    continue
                ts = TableSet.build(tables[block.config.table_id], tables)
                self._extract_current(block, doc, ts)
                base = doc.data if block.compression_id else new_data
                res = layout_block(base, block.config, ts, doc.strings, self.registry)
                if not res.ok:
                    for p in res.problems:
                        problems.append(f"{block.name} #{p.index}: {p.message}")
                    continue
                if block.compression_id:
                    new_payload = apply_splices(doc.data, res.splices)
                    packed, problem = self._recompress(block, new_payload)
                    if problem:
                        problems.append(f"{block.name}: {problem}")
                        continue
                    from mapchar.pipeline.insert import Splice

                    new_data = apply_splices(
                        new_data, [Splice(block.slice_offset, packed)]
                    )
                    doc.pending_payload = new_payload  # type: ignore[attr-defined]
                else:
                    new_data = apply_splices(new_data, res.splices)
                written_blocks.append(block)
            if problems:
                TextDialog("Cannot write", "\n".join(problems), self).exec()
                ok = False
                continue
            if not parent_doc.writable:
                self._error(
                    f"{file_entry.name} is view-only (a stage cannot write back)."
                )
                ok = False
                continue
            cfg = PathwayConfig(
                FileRef(file_entry.paths),
                file_entry.container_id,
                file_entry.reshape_id,
                file_entry.compression_id,
            )
            try:
                out = encode_for_save(
                    new_data, cfg, self.registry, parent_doc.raw, parent_doc.ctx
                )
                deposit(out, cfg)
            except (OSError, MapcharError) as exc:
                self._error(f"Cannot write {file_entry.name}: {exc}")
                ok = False
                continue
            parent_doc.data = new_data
            parent_doc.raw = out
            self.workspace.mark_saved(file_entry)
            for block in written_blocks:
                doc = block.doc
                if doc is not None:
                    pending = getattr(doc, "pending_payload", None)
                    if block.compression_id and pending is not None:
                        doc.data = pending
                    else:
                        doc.data = new_data
                    doc.extraction_key = None
                    for rec in doc.strings:
                        if rec.translation is not None:
                            rec.translation = None
                            rec.status = Status.UNTOUCHED
                self.workspace.mark_saved(block)
            for child in self.workspace.children(file_entry):
                if child.doc is not None and not child.compression_id:
                    child.doc.data = new_data
                    child.doc.extraction_key = None
            self.statusBar().showMessage(
                f"Wrote {file_entry.name} ({len(written_blocks)} block(s))", 5000
            )
        self.files_panel.refresh_labels()
        self._refresh_view()
        return ok

    def _recompress(self, block: Entry, payload: bytes) -> tuple[bytes, str | None]:
        """Compress a block's payload into its slot, padding per its spare-room rule."""
        plugin = self.registry.plugin(Stage.COMPRESSION, block.compression_id or "")
        if plugin is None or not callable(getattr(plugin, "compress", None)):
            return b"", f"{block.compression_id} cannot compress"
        try:
            packed = plugin.compress(payload, PipelineContext())
        except Exception as exc:  # noqa: BLE001
            return b"", f"cannot compress: {exc}"
        slot = block.slice_length
        if slot and len(packed) > slot:
            return (
                b"",
                f"compressed data is {len(packed) - slot} byte(s) larger than its slot",
            )
        if slot and len(packed) < slot:
            parent_doc = block.parent.doc if block.parent is not None else None
            if block.spare_room == "keep" and parent_doc is not None:
                old = parent_doc.data[block.slice_offset : block.slice_offset + slot]
                packed = packed + old[len(packed) :]
            else:
                fill = block.config.fill if block.config else 0xFF
                packed = packed + bytes([fill]) * (slot - len(packed))
        return packed, None

    def apply_bytes(self, entry: Entry, offset: int, data: bytes) -> None:
        doc = self._load_document(entry)
        if doc is None:
            return
        buf = bytearray(doc.data)
        buf[offset : offset + len(data)] = data
        doc.data = bytes(buf)
        self.workspace.stamp(entry)
        for child in self.workspace.children(entry):
            if child.doc is not None:
                child.doc.data = doc.data
                child.doc.extraction_key = None
        if self._entry is not entry and self._entry not in self.workspace.children(
            entry
        ):
            self._activate_entry(entry)
        self._refresh_view()

    def overtype_bytes(self, offset: int, data: bytes) -> None:
        file_entry = self._current_file()
        doc = self._doc
        if file_entry is None or doc is None or not data:
            return
        if offset + len(data) > doc.size:
            self._error("The bytes would run past the end of the file.")
            return
        before = doc.data[offset : offset + len(data)]
        if before == data:
            return
        self.undo_stack.push(BytesCommand(self, file_entry, offset, before, data))

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
        self.setWindowTitle(f"{name}{mark}{entry} — {APP_NAME}")

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
        self._sync_hex_panel()
        if self._selection and self.display.currentWidget() is self.text:
            self.text.select_bytes(*self._selection)
        if self._selection and self._doc is not None:
            s, e = self._selection
            for rec in self._doc.strings:
                if rec.start <= s < rec.end:
                    self.strings.select_index(rec.index)
                    break

    def _on_string_row(self, index: int) -> None:
        rec = self._string(self._entry, index)
        if rec is None:
            return
        self._sync_preview()
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
        dialog = BlockDialog(
            table_ids, cfg, f"Block {start:X}", self, self.registry.ids(Stage.MAPPING)
        )
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
            ptr_rec = self._string_for_pointer_at(sel[0])
            if ptr_rec is not None:
                menu.addAction(
                    "Jump to pointer target",
                    lambda: self._select_bytes(ptr_rec.start, ptr_rec.length),
                )
            str_rec = self._string_at(sel[0])
            if str_rec is not None and str_rec.pointers:
                p = str_rec.pointers[0]
                menu.addAction(
                    "Jump to pointer", lambda: self._select_bytes(p.address, p.size)
                )
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

    def _string_at(self, offset: int):
        doc = self._doc
        if doc is None:
            return None
        return next((r for r in doc.strings if r.start <= offset < r.end), None)

    def _string_for_pointer_at(self, offset: int):
        doc = self._doc
        if doc is None:
            return None
        for rec in doc.strings:
            for p in rec.pointers:
                if p.address <= offset < p.address + p.size:
                    return rec
        return None

    def _import_cartographer_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Cartographer command file", self._last_dir(), "*.txt;;*"
        )
        if path:
            self.import_cartographer(path)

    def import_cartographer(self, path: str) -> list[Entry]:
        """Blocks from a command file, with its tables, under the current ROM."""
        file_entry = self._current_file()
        if file_entry is None:
            self._error("Open the ROM the command file describes first.")
            return []
        try:
            with open(path, encoding="utf-8") as f:
                cf = parse_command_file(f.read())
        except (OSError, MapcharError) as exc:
            self._error(f"Cannot import {path}: {exc}")
            return []
        base = os.path.dirname(os.path.abspath(path))
        notices = [n.message for n in cf.notices]
        created: list[Entry] = []
        # Cartographer addresses are file offsets; blocks address the payload
        # the container yields, which drops the file's header.
        doc = self._load_document(file_entry)
        header = int(doc.ctx.get(KEY_HEADER_SIZE, 0) or 0) if doc is not None else 0
        self.undo_stack.beginMacro(f"Import {os.path.basename(path)}")
        try:
            for sub in cf.sub_tables:
                self.open_table(os.path.normpath(os.path.join(base, sub)), "abcde")
            for block in cf.blocks:
                table_path = os.path.normpath(os.path.join(base, block.table_file))
                table_entry = self.open_table(table_path, "abcde")
                table_id = block.table_id
                if table_id is None and table_entry is not None and table_entry.tables:
                    table_id = table_entry.tables[0].id
                if table_id is None:
                    notices.append(f"{block.name}: no table; block skipped")
                    continue
                from dataclasses import replace

                entry = Entry(
                    EntryKind.BLOCK,
                    block.name,
                    file_entry.path,
                    parent=file_entry,
                    config=shift_config(
                        replace(block.config, table_id=table_id), -header
                    ),
                )
                self._push_add(entry)
                created.append(entry)
        finally:
            self.undo_stack.endMacro()
        self._remember_dir(path)
        if created:
            self._activate_entry(created[0])
        message = f"Imported {len(created)} block(s) from {os.path.basename(path)}"
        if notices:
            TextDialog(
                "Cartographer import", message + "\n\n" + "\n".join(notices), self
            ).exec()
        else:
            self.statusBar().showMessage(message, 5000)
        return created

    def _import_atlas_dialog(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Atlas script", self._last_dir(), "*.txt;;*"
        )
        if path:
            self.import_atlas(path)

    def import_atlas(self, path: str) -> int:
        """Translations from an Atlas script into the current block's strings."""
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK or entry.doc is None:
            self._error("Select the block the script belongs to first.")
            return 0
        try:
            with open(path, encoding="utf-8") as f:
                script = read_atlas(f.read())
        except OSError as exc:
            self._error(f"Cannot read {path}: {exc}")
            return 0
        notices = list(script.notices)
        by_pointer = {p.address: r for r in entry.doc.strings for p in r.pointers}
        by_start = {r.start: r for r in entry.doc.strings}
        applied = 0
        self.undo_stack.beginMacro(f"Import {os.path.basename(path)}")
        try:
            for i, item in enumerate(script.strings):
                rec = None
                for addr in item.pointers:
                    rec = by_pointer.get(addr)
                    if rec is not None:
                        break
                if rec is None and item.insert_at is not None:
                    rec = by_start.get(item.insert_at)
                if rec is None:
                    notices.append(f"string {i}: no block string matches its address")
                    continue
                self._on_translation_edited_for(entry, rec.index, item.text)
                applied += 1
        finally:
            self.undo_stack.endMacro()
        self._remember_dir(path)
        self._refresh_view()
        message = f"Imported {applied} string(s) from {os.path.basename(path)}"
        if notices:
            TextDialog(
                "Atlas import", message + "\n\n" + "\n".join(notices), self
            ).exec()
        else:
            self.statusBar().showMessage(message, 5000)
        return applied

    def _on_translation_edited_for(self, entry, index: int, text: str) -> None:
        rec = self._string(entry, index)
        if rec is None:
            return
        after = text if text.strip() else None
        if after is not None and after.replace("\n", "") == rec.original_text().replace(
            "\n", ""
        ):
            after = None
        if after != rec.translation:
            self.undo_stack.push(
                StringFieldCommand(
                    self, entry, index, "translation", rec.translation, after
                )
            )

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

    def _show_scan(self) -> None:
        if self._doc is not None:
            self.scan_window.set_source(self._doc.data, self._table_set())
        self.scan_window.show()
        self.scan_window.raise_()
        self.scan_window.activateWindow()

    def _block_from_region(self, region) -> None:
        """A block over a scanned region, with its guessed terminator as end token."""
        tables = self._table_set()
        if tables is not None and region.terminator is not None:
            bits = format(region.terminator, "08b")
            start = tables.start
            if bits not in start.entries:
                from mapchar.core.table import Entry as TableEntry

                start.add(TableEntry(bits, TableEntryKind.END, "[end]"))
                table_entry = self.tables_panel.entry_for_table(start.id)
                if table_entry is not None:
                    self.workspace.stamp(table_entry)
                self._refresh_view()
        self._new_block(region.start, region.end)

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
        if self._dirty_blocks() or any(e.dirty for e in self.workspace.files()):
            answer = QMessageBox.question(
                self,
                "Unsaved edits",
                f"Write unsaved edits to disk before you {what}?",
                QMessageBox.StandardButton.Save
                | QMessageBox.StandardButton.Discard
                | QMessageBox.StandardButton.Cancel,
            )
            if answer == QMessageBox.StandardButton.Cancel:
                return False
            if answer == QMessageBox.StandardButton.Save and not self._write_all():
                return False
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
            self, "Open Project", self._last_dir(), f"{APP_NAME} projects (*.mapchar)"
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
            f"{APP_NAME} projects (*.mapchar)",
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
    # Hex panel, find and replace, import and export
    # ------------------------------------------------------------------

    def _sync_hex_panel(self) -> None:
        if not self.hex_dock.isVisible():
            return
        doc = self._doc
        if doc is None:
            self.hex_panel.set_data(b"", 0, None)
        else:
            self.hex_panel.set_data(doc.data, self._offset, self._selection)
        self.hex_panel.refresh()

    def _find_text_or_bytes(self, text: str) -> None:
        if not text.strip() or self._doc is None:
            return
        needle = self._needle_from(text.strip())
        if needle is None:
            return
        self._find_last = (needle, (self._selection[0] + 1) if self._selection else 0)
        self._find_bytes(again=True)

    def _show_find_replace(self) -> None:
        self.find_replace.show()
        self.find_replace.raise_()
        self.find_replace.find.setFocus()

    def _fr_targets(self):
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK or entry.doc is None:
            return None, []
        return entry, entry.doc.strings

    @staticmethod
    def _fr_match(hay: str, needle: str, case: bool) -> int:
        return hay.find(needle) if case else hay.lower().find(needle.lower())

    def _fr_find_next(self, needle: str, case: bool) -> None:
        entry, strings = self._fr_targets()
        if not strings or not needle:
            return
        selected = self.strings.selected_indices()
        start = (selected[0] + 1) if selected else 0
        order = [r for r in strings if r.index >= start] + [
            r for r in strings if r.index < start
        ]
        for rec in order:
            text = (
                rec.translation if rec.translation is not None else rec.original_text()
            )
            if self._fr_match(text, needle, case) >= 0:
                self.strings.select_index(rec.index)
                self._on_string_row(rec.index)
                return
        self.statusBar().showMessage("Not found", 3000)

    def _fr_replace_in(
        self, rec, needle: str, replacement: str, case: bool
    ) -> str | None:
        text = rec.translation if rec.translation is not None else rec.original_text()
        if case:
            if needle not in text:
                return None
            return text.replace(needle, replacement)
        import re

        pattern = re.compile(re.escape(needle), re.IGNORECASE)
        if not pattern.search(text):
            return None
        return pattern.sub(lambda m: replacement, text)

    def _fr_replace_one(self, needle: str, replacement: str, case: bool) -> None:
        entry, strings = self._fr_targets()
        selected = self.strings.selected_indices()
        if not selected or not needle:
            self._fr_find_next(needle, case)
            return
        rec = self._string(entry, selected[0])
        new = self._fr_replace_in(rec, needle, replacement, case) if rec else None
        if new is not None:
            self._on_translation_edited(rec.index, new)
        self._fr_find_next(needle, case)

    def _fr_replace_all(self, needle: str, replacement: str, case: bool) -> None:
        entry, strings = self._fr_targets()
        if not strings or not needle:
            return
        self.undo_stack.beginMacro("Replace all")
        n = 0
        for rec in strings:
            new = self._fr_replace_in(rec, needle, replacement, case)
            if new is not None:
                self._on_translation_edited(rec.index, new)
                n += 1
        self.undo_stack.endMacro()
        self.statusBar().showMessage(f"Replaced in {n} string(s)", 4000)

    def _find_pointers(self) -> None:
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK or entry.doc is None:
            self._error("Select a block first.")
            return
        starts = [r.start for r in entry.doc.strings]
        if not starts:
            self._error("The block has no strings.")
            return
        mappings = {
            mid: resolve_mapping(self.registry, mid)
            for mid in self.registry.ids(Stage.MAPPING)
        }
        self.statusBar().showMessage("Looking for pointers…")
        QApplication.processEvents()
        candidates = discover(entry.doc.data, starts, mappings)
        self.statusBar().clearMessage()
        if not candidates:
            self._error("No pointers to these strings were found.")
            return
        dialog = DiscoveryDialog(candidates, self)
        if dialog.exec() != DiscoveryDialog.DialogCode.Accepted:
            return
        chosen = dialog.chosen()
        if chosen is None:
            return
        from dataclasses import replace

        entry.config = replace(entry.config, source=chosen.source())
        entry.doc = None
        self._doc = self._load_document(entry)
        self.files_panel.refresh_labels()
        self._refresh_view()

    def _export_atlas(self) -> None:
        entry = self._entry
        tables = self._table_set()
        if (
            entry is None
            or entry.kind is not EntryKind.BLOCK
            or entry.doc is None
            or tables is None
        ):
            self._error("Select a block with a start table to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Atlas script",
            os.path.join(self._last_dir(), f"{entry.name}.txt"),
            "*.txt",
        )
        if not path:
            return
        table_files = {}
        for e in self.workspace.entries:
            if e.kind is EntryKind.TABLE and any(
                t.id in tables.tables for t in e.tables
            ):
                table_files[e.name if e.name.endswith(".tbl") else e.name + ".tbl"] = [
                    t for t in e.tables if t.id in tables.tables
                ]
        export = write_atlas(
            entry.name, entry.config, entry.doc.strings, tables, table_files
        )
        folder = os.path.dirname(path)
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(export.script)
            for name, text in export.tables.items():
                with open(
                    os.path.join(folder, name), "w", encoding="utf-8", newline="\n"
                ) as f:
                    f.write(text)
        except OSError as exc:
            self._error(f"Cannot write: {exc}")
            return
        self._remember_dir(path)
        message = f"Exported {path} and {len(export.tables)} table file(s)"
        if export.notices:
            TextDialog(
                "Atlas export", message + "\n\n" + "\n".join(export.notices), self
            ).exec()
        else:
            self.statusBar().showMessage(message, 5000)

    def _export_cartographer(self) -> None:
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK or entry.config is None:
            self._error("Select a block to export.")
            return
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Export command file",
            os.path.join(self._last_dir(), f"{entry.name}.txt"),
            "*.txt",
        )
        if not path:
            return
        table_entry = self.tables_panel.entry_for_table(entry.config.table_id)
        table_file = (
            os.path.basename(table_entry.path)
            if table_entry and table_entry.path
            else "main.tbl"
        )
        parent_doc = self._load_document(entry.parent) if entry.parent else None
        header = int(parent_doc.ctx.get(KEY_HEADER_SIZE, 0) or 0) if parent_doc else 0
        text, notes = write_command_file(
            entry.name,
            shift_config(entry.config, header),
            table_file,
            table_id=entry.config.table_id or None,
        )
        if not text:
            self._error("\n".join(notes))
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as exc:
            self._error(f"Cannot write {path}: {exc}")
            return
        self._remember_dir(path)
        if notes:
            TextDialog("Cartographer export", "\n".join(notes), self).exec()
        else:
            self.statusBar().showMessage(f"Exported {path}", 5000)

    def _block_strings_by_name(self, file_entry: Entry | None) -> dict[str, list]:
        out: dict[str, list] = {}
        for e in self.workspace.entries:
            if e.kind is not EntryKind.BLOCK or (
                file_entry and e.parent is not file_entry
            ):
                continue
            doc = self._load_document(e)
            if doc is None or e.config is None:
                continue
            tables = self.workspace.tables()
            ts = None
            if e.config.table_id in tables:
                ts = TableSet.build(tables[e.config.table_id], tables)
            self._extract_current(e, doc, ts)
            out[e.name] = doc.strings
        return out

    def _import(self, kind: str) -> None:
        filters = {
            "script": "Scripts (*.txt);;All files (*)",
            "delimited": "Tables (*.tsv *.csv);;All files (*)",
            "po": "PO files (*.po);;All files (*)",
        }[kind]
        path, _ = QFileDialog.getOpenFileName(self, "Import", self._last_dir(), filters)
        if not path:
            return
        self.import_file(path, kind)

    def import_file(self, path: str, kind: str, force: bool = False) -> None:
        try:
            with open(path, encoding="utf-8") as f:
                text = f.read()
        except OSError as exc:
            self._error(f"Cannot read {path}: {exc}")
            return
        file_entry = self._current_file()
        blocks = self._block_strings_by_name(file_entry)
        before = {
            name: [(r.translation, r.status, r.notes) for r in strs]
            for name, strs in blocks.items()
        }
        try:
            if kind == "script":
                report = apply_script(parse_script(text, path), blocks)
                notices = list(report.notices)
                for name, cfg in report.new_blocks:
                    if file_entry is not None:
                        entry = Entry(
                            EntryKind.BLOCK,
                            name,
                            file_entry.path,
                            parent=file_entry,
                            config=cfg,
                        )
                        self._push_add(entry)
                        notices.append(f"created block {name}")
                applied = report.applied
            else:
                records = read_po(text) if kind == "po" else read_delimited(text)
                report = apply_records(records, blocks, force=force)
                notices, applied = report.skipped, report.applied
        except (MapcharError, ValueError) as exc:
            self._error(f"Cannot import {path}: {exc}")
            return
        # Record the changes as one undo step.
        self.undo_stack.beginMacro(f"Import {os.path.basename(path)}")
        for name, strs in blocks.items():
            entry = next(
                (
                    e
                    for e in self.workspace.entries
                    if e.kind is EntryKind.BLOCK and e.name == name
                ),
                None,
            )
            if entry is None:
                continue
            for rec, (tr, st, notes) in zip(strs, before[name], strict=False):
                new = (rec.translation, rec.status, rec.notes)
                rec.translation, rec.status, rec.notes = tr, st, notes
                if new[0] != tr:
                    self.undo_stack.push(
                        StringFieldCommand(
                            self, entry, rec.index, "translation", tr, new[0]
                        )
                    )
                if new[1] != st:
                    self.undo_stack.push(
                        StringFieldCommand(
                            self, entry, rec.index, "status", st.value, new[1].value
                        )
                    )
                if new[2] != notes:
                    self.undo_stack.push(
                        StringFieldCommand(
                            self, entry, rec.index, "notes", notes, new[2]
                        )
                    )
        self.undo_stack.endMacro()
        self._remember_dir(path)
        self._refresh_view()
        message = f"Imported {applied} string(s) from {os.path.basename(path)}"
        if notices:
            TextDialog(
                "Import notices", message + "\n\n" + "\n".join(notices), self
            ).exec()
        else:
            self.statusBar().showMessage(message, 5000)

    def _export(self, kind: str) -> None:
        entry = self._entry
        if entry is None or entry.kind is not EntryKind.BLOCK or entry.doc is None:
            self._error("Select a block to export.")
            return
        ext = {"tsv": "tsv", "csv": "csv", "po": "po"}[kind]
        suggested = os.path.join(self._last_dir(), f"{entry.name}.{ext}")
        path, _ = QFileDialog.getSaveFileName(self, "Export", suggested, f"*.{ext}")
        if not path:
            return
        self.export_file(path, kind)

    def export_file(self, path: str, kind: str) -> None:
        entry = self._entry
        records = records_for(entry.name, entry.doc.strings)
        if kind == "po":
            rom = (
                os.path.basename(entry.parent.path)
                if entry.parent and entry.parent.path
                else "rom"
            )
            text = write_po(records, rom)
        else:
            text = write_delimited(records, "\t" if kind == "tsv" else ",")
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as exc:
            self._error(f"Cannot write {path}: {exc}")
            return
        self._remember_dir(path)
        self.statusBar().showMessage(
            f"Exported {len(records)} string(s) to {path}", 5000
        )

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
            APP_NAME,
            f"{APP_NAME} {__version__}\nA text viewer and editor for retro-game ROMs.",
        )

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)
