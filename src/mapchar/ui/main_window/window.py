"""The main window's shell: the widgets, the docks, the menus, and the few things
every mixin needs from it.

What is left when every surface has a module of its own (see the package
docstring): the widget tree and the four docks, the menu bar, the shared undo
stack together with the guard and the reach every command applies through, the
window title and the project's unsaved marker, the file dialogs, and the error
modal. Not one more surface — the shell the mixins hang off.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from PySide6.QtCore import QFileSystemWatcher, Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence, QPalette, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from mapchar import APP_NAME
from mapchar.core.address import BANK_PRESETS, HEX_ID
from mapchar.core.document import Document
from mapchar.core.text import read_text_any
from mapchar.plugins.registry import Registry, default_registry
from mapchar.project.workspace import (
    Entry,
    Workspace,
)
from mapchar.ui import settings
from mapchar.ui.decompress_window import DecompressWindow
from mapchar.ui.dialogs import (
    TextDialog,
)
from mapchar.ui.files_panel import FilesPanel
from mapchar.ui.find_replace import FindReplaceDialog
from mapchar.ui.fonts_panel import FontsPanel
from mapchar.ui.glyphs import Glyph
from mapchar.ui.help_dialogs import (
    AboutDialog,
    LegendDialog,
    ShortcutGuide,
    shortcut_sections,
)
from mapchar.ui.hex_panel import HexPanel
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.main_window.block_bar import BlockBarMixin
from mapchar.ui.main_window.capability_sync import CapabilitySyncMixin
from mapchar.ui.main_window.codecs_bar import CodecsBarMixin
from mapchar.ui.main_window.compression import CompressionMixin
from mapchar.ui.main_window.containers import ContainerMixin
from mapchar.ui.main_window.dumping import DumpingMixin
from mapchar.ui.main_window.entries import EntriesMixin
from mapchar.ui.main_window.entry_clipboard import EntryClipboardMixin
from mapchar.ui.main_window.find_replace import FindReplaceMixin
from mapchar.ui.main_window.fonts import FontsMixin
from mapchar.ui.main_window.hex_view import HexViewMixin
from mapchar.ui.main_window.history import HistoryMixin
from mapchar.ui.main_window.import_export import ImportExportMixin
from mapchar.ui.main_window.navigation import CUSTOM_ID, NavigationMixin
from mapchar.ui.main_window.opening import OpeningMixin
from mapchar.ui.main_window.plugins import PluginsMixin
from mapchar.ui.main_window.pointers import PointerDiscoveryMixin
from mapchar.ui.main_window.preview import PreviewMixin
from mapchar.ui.main_window.projects import ProjectMixin
from mapchar.ui.main_window.raw_view import RawViewMixin
from mapchar.ui.main_window.refresh import RefreshMixin
from mapchar.ui.main_window.relative_search import RelativeSearchMixin
from mapchar.ui.main_window.search import SearchMixin
from mapchar.ui.main_window.session import SessionMixin
from mapchar.ui.main_window.string_edit import StringEditMixin
from mapchar.ui.main_window.strings_view import StringsViewMixin
from mapchar.ui.main_window.table_editor import TableEditorMixin
from mapchar.ui.main_window.tables_dock import TablesDockMixin
from mapchar.ui.main_window.wrap import WrapMixin
from mapchar.ui.main_window.writing import WritingMixin
from mapchar.ui.preview_window import PreviewWindow
from mapchar.ui.raw_widget import RawWidget
from mapchar.ui.scan_window import ScanWindow
from mapchar.ui.search_window import SearchWindow
from mapchar.ui.strings_view import StringsView
from mapchar.ui.table_editor import TableEditor
from mapchar.ui.tables_panel import TablesPanel
from mapchar.ui.text_widget import TextDecode, TextWidget
from mapchar.ui.widgets import (
    CommandComboBox,
    CompactComboBox,
    ElidedLabel,
    fit_chars,
)
from mapchar.ui.window_layout import WindowLayout


class MainWindow(
    SessionMixin,
    RefreshMixin,
    CapabilitySyncMixin,
    CodecsBarMixin,
    NavigationMixin,
    HistoryMixin,
    OpeningMixin,
    EntriesMixin,
    EntryClipboardMixin,
    ContainerMixin,
    WritingMixin,
    DumpingMixin,
    CompressionMixin,
    PluginsMixin,
    TablesDockMixin,
    TableEditorMixin,
    RawViewMixin,
    BlockBarMixin,
    StringsViewMixin,
    StringEditMixin,
    WrapMixin,
    FindReplaceMixin,
    SearchMixin,
    RelativeSearchMixin,
    PointerDiscoveryMixin,
    ImportExportMixin,
    ProjectMixin,
    PreviewMixin,
    FontsMixin,
    HexViewMixin,
    ThemedIcons,
    QMainWindow,
):
    """The application window: one class, assembled from the mixins above.

    They are listed before ``QMainWindow`` so a mixin's method wins over Qt's
    (``eventFilter``, ``closeEvent``), and in the order the concerns build on
    each other rather than alphabetically — the session and the refresh first,
    because everything else ends in one of them.
    """

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
        self.settings = settings()
        self.project_path: str | None = None
        self._saved_snapshot: str | None = None
        self._applying_undo = False
        self._defer_project_modified = False
        """Set over an undo push, so the project's unsaved marker is answered
        once at the end rather than at each choke point the push passes."""
        self._edit_run = 0
        """Bumped when a run of edits on one string ends, so only the commands of
        one run merge (:class:`~mapchar.ui.undo_commands.StringFieldCommand`)."""
        self._scanning = False
        """True while a structure scan owns the view; navigation keys are inert."""
        self._doc: Document | None = None
        self._entry: Entry | None = None
        self._offset = 0
        self._bounds: tuple[int, int] | None = None
        """The bytes the Hex and Text tabs are confined to — a block's source, one
        string — or ``None`` for the whole document
        (:mod:`mapchar.ui.main_window.navigation`)."""
        self._text_trail: list[tuple[int, int, int]] = []
        self._text_decode: TextDecode | None = None
        """The Text tab's tokens, kept from one window to the next."""
        self._text_guess = 0
        """How many bytes the Text tab's last window took to fill its box."""
        self._text_up_guess = 0.0
        """How many bytes back a line of the text above the Text tab's window
        was, the last time one was looked for."""
        """The Text tab's wheel steps down, as ``(from, to, lines)``, so a step
        up retraces one exactly (:mod:`mapchar.ui.main_window.refresh`)."""
        self._scan_stop = False
        """Set by :meth:`request_scan_stop` to abandon a running structure scan."""
        self._selection: tuple[int, int] | None = None
        self._step_icons: list[tuple[QPushButton, Glyph]] = []
        # Before anything can make an entry current: the first visit arms the
        # trail's two actions, and _build_menus puts them in the Navigate menu.
        self._init_history()
        self._build_widgets()
        self._build_menus()
        # Navigation keys and the back/forward mouse buttons are routed through
        # an application-wide filter rather than shortcuts, so they work wherever
        # the focus is (mapchar.ui.main_window.navigation).
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        # Last, and after every dock is built and placed: what it finds here is
        # the factory layout it hands back to Panels ▸ Reset, and what it applies
        # has to win over the placement each dock did for itself.
        self._window_layout = WindowLayout(self, "window")
        self._window_layout.restore()
        self._update_title()
        self._refresh_view()
        # A plugin that failed the startup scan is a warning the user should
        # see, not a status line lost behind the next message.
        self._alert_plugin_issues()

    def _build_widgets(self) -> None:
        self.setWindowTitle(APP_NAME)
        self.resize(1100, 720)
        self.setAcceptDrops(True)  # drop a file on the window to open it

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
        self.container_pick = CompactComboBox()
        self._fill_container_pick()
        self.table_pick = CommandComboBox("New Table…")
        self.table_pick.addItem("(no table)", None)
        self.table_pick.add_command_row()
        self.compression_pick = CompactComboBox()
        self._fill_compression_pick()
        codecs.addWidget(QLabel(" Container "))
        codecs.addWidget(self.container_pick)
        codecs.addWidget(QLabel("  Compression "))
        codecs.addWidget(self.compression_pick)
        codecs.addWidget(QLabel("  Table "))
        codecs.addWidget(self.table_pick)
        self.addToolBar(codecs)
        self.codecs_bar = codecs

        block_bar = QWidget()
        bl = QHBoxLayout(block_bar)
        bl.setContentsMargins(0, 0, 0, 0)
        self.block_label = ElidedLabel("")
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
        self.strings = StringsView()
        self.tabs.addTab(self.raw, "Hex")
        self.tabs.addTab(self.text, "Text")
        self.tabs.addTab(self.strings, "Strings")
        layout.addWidget(self.tabs, 1)

        nav = QWidget()
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(0, 0, 0, 0)
        self.offset_box = QLineEdit()
        # Room for the longest spelling a format writes ($C0:FFFF, 7FFFFF), and
        # no more: the steps beside it want the width.
        fit_chars(self.offset_box, 9)
        self.offset_box.setMaximumWidth(self.offset_box.minimumWidth())
        self.offset_box.setPlaceholderText("address")
        self.offset_box.setToolTip("The view's position; type an address and Enter")
        # How a position is spelled: a flat file offset, one of the console
        # mapping presets, or the three numbers beside the picker
        # (mapchar.core.address).
        self.address_pick = CompactComboBox()
        self.address_pick.addItem("Hex", HEX_ID)
        for preset in BANK_PRESETS:
            self.address_pick.addItem(preset.name, preset.id)
        self.address_pick.addItem("Custom bank…", CUSTOM_ID)
        self.address_pick.setToolTip(
            "How addresses are written: a flat file offset, a console\n"
            "mapping, or bank numbers of your own."
        )
        self.custom_bank_row = QWidget()
        cb = QHBoxLayout(self.custom_bank_row)
        cb.setContentsMargins(0, 0, 0, 0)
        self.bank_size_box = QLineEdit("8000")
        self.addr_base_box = QLineEdit("8000")
        self.bank_base_box = QLineEdit("0")
        for label, box, tip in (
            ("Bank size", self.bank_size_box, "Bytes of ROM per bank, in hex"),
            ("at", self.addr_base_box, "In-bank address of a bank's first byte"),
            ("from bank", self.bank_base_box, "Bank number of the file's first byte"),
        ):
            box.setMaximumWidth(64)
            box.setToolTip(tip)
            cb.addWidget(QLabel(label))
            cb.addWidget(box)
        nl.addWidget(self.address_pick)
        nl.addWidget(self.custom_bank_row)
        nl.addWidget(self.offset_box)
        # The row steps wear the bundled icon font; the byte and page steps
        # stay words, since the font has no mark that says "byte" or "page".
        steps: list[QPushButton] = []
        for text, glyph, delta, tip in (
            ("Home", None, "home", "Start of file (Home)"),
            ("Pg Up", None, "page-up", "Page up (PgUp)"),
            ("", Glyph.ARROW_UP, "row-up", "Row up, or a line in Text (Up)"),
            ("−B", None, -1, "Byte back (Left or −)"),
            ("+B", None, 1, "Byte forward (Right or +)"),
            ("", Glyph.ARROW_DOWN, "row-down", "Row down, or a line in Text (Down)"),
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
                b.clicked.connect(lambda _=False, d=d: self._step_pages(d))
            elif delta in ("row-up", "row-down"):
                d = -1 if delta == "row-up" else 1
                b.clicked.connect(lambda _=False, d=d: self._step_rows(d))
            elif isinstance(delta, int):
                b.clicked.connect(lambda _=False, d=delta: self._move(d))
            elif delta == "home":
                b.clicked.connect(self._go_home)
            else:
                b.clicked.connect(self._go_end)
            nl.addWidget(b)
            steps.append(b)
        self.step_buttons = tuple(steps)
        self._bake_icons()
        nl.addStretch(1)
        # The file's size, the selection and any view-only notice sit at the
        # status bar's right end rather than in this row, whose steps want the
        # width; elided there, so a long notice can never widen the window.
        self.nav_status = ElidedLabel("")
        self.nav_status.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )
        self.statusBar().addPermanentWidget(self.nav_status)
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
        self.files_panel.entry_activated.connect(self._show_entry)
        self.files_panel.string_activated.connect(self._show_string)
        self.files_panel.strings_requested.connect(self._read_block_strings)
        self.files_panel.entry_double_clicked.connect(self._on_entry_double)
        self.files_panel.context_menu_requested.connect(self._files_menu)
        self.files_panel.remove_requested.connect(self._remove_entries)
        self.files_panel.rename_committed.connect(self._commit_rename)
        self.files_panel.reorder_requested.connect(self._reorder_entry)
        self.files_panel.move_requested.connect(self._move_entries)
        self.files_panel.cut_requested.connect(self._cut_entries)
        self.files_panel.copy_requested.connect(self._copy_entries)
        self.files_panel.paste_requested.connect(self._paste_entries)
        self.files_panel.duplicate_requested.connect(self._duplicate_entries)
        self.tables_panel.table_chosen.connect(self._choose_table)
        self.fonts_panel.edit_requested.connect(self._edit_font_entry)
        self.container_pick.currentIndexChanged.connect(self._on_chain_changed)
        self.table_pick.chosen.connect(self._on_table_pick)
        self.table_pick.command.connect(lambda: self._new_table_dialog(start=True))
        self.block_edit.clicked.connect(self._edit_block)
        self.block_dump.clicked.connect(self._dump)
        self.raw.offset_requested.connect(self._go_to)
        self.raw.rows_changed.connect(self._on_raw_rows_changed)
        self.text.selection_changed.connect(self._on_text_selection)
        self.text.fit_changed.connect(self._on_text_fit_changed)
        self.text.scroll_requested.connect(self._on_text_scroll)
        self.text.page_requested.connect(self._step_pages)
        self.text.offset_requested.connect(self._go_to)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.raw.selection_changed.connect(self._on_selection)
        self.raw.context_menu_requested.connect(self._raw_menu)
        self.offset_box.returnPressed.connect(self._on_offset_typed)
        self.address_pick.currentIndexChanged.connect(self._on_address_format)
        for box in (self.bank_size_box, self.addr_base_box, self.bank_base_box):
            box.editingFinished.connect(self._refresh_view)
        self._restore_address_format()
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
        self.decompress_window.scan_stop.connect(self.request_scan_stop)
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
        self.workspace.on_current_changed.append(self._record_visit)
        self.workspace.on_current_changed.append(lambda e: self._update_title())
        # A closed entry cannot be returned to. The whole trail going is a
        # *project swap* rather than any on_reset — a reorder fires one too —
        # so ProjectMixin wipes it beside the undo stack it clears with it.
        self.workspace.on_removed.append(self._forget_visits)
        self.table_watcher = QFileSystemWatcher(self)
        self.table_watcher.fileChanged.connect(self._on_table_file_changed)
        self.workspace.on_added.append(self._watch_table)
        self.workspace.on_reset.append(self._rewatch_tables)

    def _build_menus(self) -> None:
        """The menu bar. Every row carries a mnemonic, and its label follows the
        capitalisation rule of ``docs/ui.md``; the shortcut guide reads it all
        back from here (:mod:`mapchar.ui.help_dialogs`)."""
        bar = self.menuBar()

        def act(menu, text, slot, shortcut=None):
            a = QAction(text, self)
            if shortcut:
                a.setShortcut(QKeySequence(shortcut))
            a.triggered.connect(slot)
            menu.addAction(a)
            return a

        file_menu = bar.addMenu("&File")
        act(file_menu, "&New Project", self._new_project, "Ctrl+N")
        act(file_menu, "&Open Project…", self._open_project_dialog, "Ctrl+O")
        self.recent_menu = file_menu.addMenu("Open &Recent")
        # Filled each time the File menu opens, not once at build time: the list
        # changes as projects are opened and saved, and rows go stale on disk.
        file_menu.aboutToShow.connect(self._rebuild_recent)
        file_menu.aboutToShow.connect(self._sync_locate_action)
        act(file_menu, "&Save Project", self._save_project, "Ctrl+S")
        act(file_menu, "Save Project &As…", self._save_project_as, "Ctrl+Shift+S")
        self.locate_action = act(
            file_menu, "Locate Missin&g Files…", lambda: self._relocate_missing()
        )
        self.locate_action.setEnabled(False)
        file_menu.addSeparator()
        act(file_menu, "Open RO&M…", self._open_rom_dialog, "Ctrl+Shift+O")
        act(file_menu, "Open &Table…", self._open_table_dialog, "Ctrl+T")
        act(file_menu, "N&ew Table…", lambda: self._new_table_dialog())
        act(file_menu, "Open &Font…", self._open_font_dialog)
        file_menu.addSeparator()
        # Named where :mod:`mapchar.ui.main_window.capability_sync` gates them:
        # what each row applies to is declared in the capability table, not here.
        self.new_block_action = act(
            file_menu, "New &Block…", self._new_block, "Ctrl+Shift+B"
        )
        self.new_bookmark_action = act(
            file_menu, "New Boo&kmark", self._new_bookmark, "Ctrl+B"
        )
        self.container_action = act(
            file_menu, "Edit File &Container…", self._edit_container, "Ctrl+E"
        )
        self.dump_action = act(file_menu, "&Dump…", self._dump, "Ctrl+D")
        file_menu.addSeparator()
        self.write_action = act(file_menu, "&Write", self._write_current, "Ctrl+W")
        act(file_menu, "Write A&ll", self._write_all, "Ctrl+Shift+W")
        file_menu.addSeparator()
        import_menu = file_menu.addMenu("&Import")
        self.import_action = import_menu.menuAction()
        act(import_menu, "&Script…", lambda: self._import("script"))
        act(import_menu, "&TSV / CSV…", lambda: self._import("delimited"))
        act(import_menu, "&PO…", lambda: self._import("po"))
        import_menu.addSeparator()
        act(
            import_menu,
            "&Cartographer Command File…",
            self._import_cartographer_dialog,
        )
        act(import_menu, "&Atlas Script…", self._import_atlas_dialog)
        export_menu = file_menu.addMenu("E&xport")
        self.export_action = export_menu.menuAction()
        act(export_menu, "&TSV…", lambda: self._export("tsv"))
        act(export_menu, "C&SV…", lambda: self._export("csv"))
        act(export_menu, "&PO…", lambda: self._export("po"))
        export_menu.addSeparator()
        act(export_menu, "&Cartographer Command File…", self._export_cartographer)
        act(export_menu, "&Atlas Script…", self._export_atlas)
        file_menu.addSeparator()
        act(file_menu, "Open &Plugins Folder…", self._open_plugins_folder)
        act(file_menu, "Refresh Pl&ugins", self._refresh_plugins, "F5")
        file_menu.addSeparator()
        act(file_menu, "&Quit", self.close, "Ctrl+Q")

        edit_menu = bar.addMenu("&Edit")
        undo = self.undo_stack.createUndoAction(self, "&Undo")
        undo.setShortcut(QKeySequence.StandardKey.Undo)
        redo = self.undo_stack.createRedoAction(self, "&Redo")
        redo.setShortcut(QKeySequence("Ctrl+Shift+Z"))
        # Their labels carry the command they would undo; the guide shows the
        # verb alone.
        undo.setProperty("guideLabel", "Undo")
        redo.setProperty("guideLabel", "Redo")
        edit_menu.addAction(undo)
        edit_menu.addAction(redo)
        edit_menu.addSeparator()
        # Entry Cut/Copy/Paste are scoped to the Files panel rather than to the
        # window: as window actions they would take Ctrl+C and Ctrl+V away from
        # every text field in the app. The menu rows still work by click, since
        # each reads the panel's selection rather than the keyboard focus.
        for text, slot, key in (
            ("Cu&t Entry", self._cut_selection, "Ctrl+X"),
            ("&Copy Entry", self._copy_selection, "Ctrl+C"),
            ("&Paste Entry", self._paste_selection, "Ctrl+V"),
            ("D&uplicate Entry", self._duplicate_selection, None),
        ):
            a = QAction(text, self)
            if key:
                a.setShortcut(QKeySequence(key))
                a.setShortcutContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
                self.files_panel.addAction(a)
            a.triggered.connect(slot)
            edit_menu.addAction(a)
        edit_menu.addSeparator()
        self.find_replace_action = act(
            edit_menu, "&Find and Replace…", self._show_find_replace, "Ctrl+H"
        )
        edit_menu.addSeparator()
        self.string_actions = tuple(
            act(edit_menu, text, slot)
            for text, slot in (
                ("Re&vert Selected Strings", self._revert_selected),
                ("Toggle Revie&w on Selected", self._toggle_review_selected),
                ("Copy &Original to Empty Translations", self._copy_originals),
            )
        )

        view_menu = bar.addMenu("&View")
        self.raw_tab_action = act(
            view_menu, "&Hex", lambda: self._show_view("raw"), "Ctrl+1"
        )
        self.text_tab_action = act(
            view_menu, "Te&xt", lambda: self._show_view("text"), "Ctrl+2"
        )
        self.strings_tab_action = act(
            view_menu, "&Strings", lambda: self._show_view("strings"), "Ctrl+3"
        )
        view_menu.addSeparator()
        act(view_menu, "&Table Editor…", self._show_table_editor, "Ctrl+Shift+T")
        self.preview_action = act(view_menu, "&Preview…", self._show_preview, "Ctrl+P")
        view_menu.addSeparator()
        # One of the two is always the theme in use, so they read as a choice.
        themes = QActionGroup(self)
        current = str(self.settings.value("theme", "light"))
        for attr, text, name in (
            ("theme_light", "&Light Theme", "light"),
            ("theme_dark", "&Dark Theme", "dark"),
        ):
            action = act(view_menu, text, lambda _=False, n=name: self._set_theme(n))
            action.setCheckable(True)
            action.setChecked(name == current)
            themes.addAction(action)
            setattr(self, attr, action)

        navigate_menu = bar.addMenu("&Navigate")
        self._add_history_actions(navigate_menu)
        self.goto_action = act(
            navigate_menu, "&Go to Address…", self._go_to_dialog, "Ctrl+G"
        )
        navigate_menu.addSeparator()
        # Gated with the offset box and the steps: they move the same view, and a
        # row that jumps a document there is not open is a row that does nothing.
        self.ends_actions = (
            act(navigate_menu, "&Start of File", self._go_home, None),
            act(navigate_menu, "&End of File", self._go_end, None),
        )
        # Armed by the refresh rather than the capability table: it is live only
        # while the view is confined, which is a state and not a kind of entry.
        self.whole_action = act(
            navigate_menu, "Show &Whole File", self._show_whole_file, None
        )
        self.whole_action.setEnabled(False)

        search_menu = bar.addMenu("&Search")
        self.search_actions = (
            act(search_menu, "&Search Window…", self._show_search, "Ctrl+Shift+F"),
            # Not Ctrl+Shift+S, which is Save Project As…: two window actions on
            # one sequence is ambiguous to Qt and neither of them then fires.
            act(search_menu, "S&can for Text…", self._show_scan, "Ctrl+Shift+R"),
            act(search_menu, "&Find Bytes…", self._find_bytes, "Ctrl+F"),
            act(
                search_menu,
                "Find &Next",
                lambda: self._find_bytes(again=True),
                "F3",
            ),
            act(
                search_menu,
                "Find &Previous",
                lambda: self._find_bytes(again=True, backwards=True),
                "Shift+F3",
            ),
        )
        search_menu.addSeparator()
        # Gated on its own, not with the row above: the others read bytes, and
        # this one needs a block's strings to look for pointers *to*.
        self.pointers_action = act(
            search_menu, "Find P&ointers…", self._find_pointers, "Ctrl+Shift+P"
        )

        panels_menu = bar.addMenu("&Panels")
        for dock, text in (
            (self.files_dock, "&Files"),
            (self.tables_dock, "&Tables"),
            (self.fonts_dock, "F&onts"),
            (self.hex_dock, "&Hex"),
        ):
            toggle = dock.toggleViewAction()
            toggle.setText(text)
            panels_menu.addAction(toggle)
        panels_menu.addSeparator()
        act(panels_menu, "&Reset Panel Layout", self._reset_layout)

        help_menu = bar.addMenu("&Help")
        act(help_menu, "&Shortcuts…", self._show_shortcuts, "F1")
        act(help_menu, "&Legend…", self._show_legend)
        act(help_menu, "&About", self._about)
        self._rebuild_recent()

    def _reset_layout(self) -> None:
        """Panels ▸ Reset Panel Layout: the arrangement a fresh install has.

        The factory state is whatever the docks built for themselves, captured by
        :class:`~mapchar.ui.window_layout.WindowLayout` before anything was
        restored — so this is one call rather than a list of docks to show and
        hide that a new dock would fall out of.
        """
        self._window_layout.reset()

    def _set_theme(self, name: str) -> None:
        from mapchar.ui.theme import apply_theme

        apply_theme(QApplication.instance(), name)
        self.settings.setValue("theme", name)
        (self.theme_dark if name == "dark" else self.theme_light).setChecked(True)
        self._bake_icons()

    def _bake_icons(self) -> None:
        """Stamp the navigation bar's step arrows in the theme's button-text
        color."""
        for button, glyph in self._step_icons:
            button.setIcon(themed_icon(self, glyph, QPalette.ColorRole.ButtonText))

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        if not self._confirm_discard("quit"):
            event.ignore()
            return
        # The layout is written on a short delay, so a quit inside that delay
        # would otherwise lose the last drag.
        self._window_layout.save()
        # And take the navigation filter back off the application: it was
        # installed on a singleton, so a window that closed while leaving its
        # filter behind would keep answering for a window that is gone.
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().closeEvent(event)

    # -- the undo stack ------------------------------------------------------
    @contextmanager
    def _undo_apply(self):
        """Mark a command's undo/redo application as in progress.

        Applying pokes the same widgets and paths a user gesture does; push sites
        bail while this is set, so an apply can never push a second command.
        """
        self._applying_undo = True
        try:
            yield
        finally:
            self._applying_undo = False

    def _push_command(self, command) -> None:
        """Push onto the session stack (``push()`` runs the command's redo).

        Dirty tracking rides on the commands themselves, not on the stack: one
        stack spans every entry, so ``QUndoStack``'s single clean index cannot
        express per-entry state, and the editing commands carry revision tokens
        instead.

        The *project's* unsaved marker is a different question, and one push
        raises it several times over: the apply refreshes the view, and the push
        moves the stack index, and both are choke points
        :meth:`_refresh_project_modified` hangs off. Answering it costs the whole
        project re-serialised, so it is answered once — after, where the answer is
        the same one every intermediate ask would have got.

        **Nothing is pushed while an apply is running.** An apply restores widgets
        the user's own gestures drive, and every one of those is wired to a slot
        that would push a command of its own; a second command landing inside the
        first one's redo would put the stack a step ahead of the document and make
        the next undo revert the wrong thing. The single guard here is what makes
        that true of every surface, rather than of the surfaces that remembered to
        ask.
        """
        if self._applying_undo:
            return
        self._defer_project_modified = True
        try:
            self.undo_stack.push(command)
        finally:
            self._defer_project_modified = False
            self._refresh_project_modified()

    def _ensure_current(self, entry: Entry | None) -> bool:
        """Make ``entry`` the current view for an entry-scoped command.

        Undoing a change made in another entry first switches back to it, so the
        revert happens where the user can see it. False when activation fails (a
        file that has gone) — the command then skips its apply.
        """
        if entry is None:
            return True
        if self.workspace.current is not entry:
            self._activate_entry(entry)
        return self.workspace.current is entry

    def _ensure_edit_context(self, entry: Entry, view: str, where) -> bool:
        """Return to the entry *and* the view an editing command was made in.

        A translation and a hex overtype are edits to the same entry made on two
        different surfaces, so a step that came back on the other one would
        revert something off screen. The row or the offset comes back with the
        tab, for the same reason: an edit is made at a place as much as it is made
        to a value.
        """
        if not self._ensure_current(entry):
            return False
        self._show_view(view)
        if view == "strings":
            if where is not None:
                self.strings.select_index(where)
        else:
            # Only when it is off screen: a nudge for an edit the user can
            # already see would move the view for nothing. Inside the guard this
            # pushes no command of its own.
            if where is not None and not (
                self._offset <= where < self._offset + self._view_bytes()
            ):
                self._go_to(where)
        return True

    def _last_dir(self) -> str:
        return str(self.settings.value("last_dir", ""))

    def _remember_dir(self, path: str) -> None:
        self.settings.setValue("last_dir", os.path.dirname(path))

    def _pick_open(self, title: str, filters: str = "") -> str | None:
        """Ask for a file to read, starting in the last folder used."""
        path, _ = QFileDialog.getOpenFileName(self, title, self._last_dir(), filters)
        return path or None

    def _pick_save(self, title: str, suggested: str, filters: str = "") -> str | None:
        """Ask where to write; ``suggested`` is a name in the last folder used,
        or a path of its own."""
        if not os.path.isabs(suggested):
            suggested = os.path.join(self._last_dir(), suggested)
        path, _ = QFileDialog.getSaveFileName(self, title, suggested, filters)
        return path or None

    def _read_text(self, path: str) -> str | None:
        """The file as text, or ``None`` once the reason it is not is reported.

        UTF-8 first, because that is what mapchar writes. A command file, an Atlas
        script or a translator file that came from elsewhere is as likely to be
        Shift-JIS or Latin-1, and a decode failure reaching a Qt slot as an
        unhandled ``UnicodeDecodeError`` takes the app down over a file it could
        have read — so the byte that stopped it is reported and the two encodings
        worth trying are offered (:meth:`_ask_encoding`).
        """
        try:
            with open(path, encoding="utf-8") as f:
                return f.read()
        except OSError as exc:
            self._error(f"Cannot read {path}: {exc}")
            return None
        except UnicodeDecodeError as exc:
            encoding = self._ask_encoding(path, exc)
            if encoding is None:
                return None
            try:
                with open(path, encoding=encoding) as f:
                    return f.read()
            except (OSError, UnicodeDecodeError) as retry:
                self._error(f"Cannot read {path} as {encoding}: {retry}")
                return None

    def _ask_encoding(self, path: str, failure: UnicodeDecodeError) -> str | None:
        """Which encoding to re-read ``path`` as, or ``None`` to give up.

        The offered default is whichever of the two actually decodes the whole
        file (:func:`~mapchar.core.text.read_text_any`), so the likely answer is
        one Return away and the other is still one click away.
        """
        try:
            _, likely = read_text_any(path)
        except OSError:
            likely = "cp932"
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle(f"{APP_NAME} — Not UTF-8")
        box.setText(f"{os.path.basename(path)} is not UTF-8 text.")
        box.setInformativeText(
            f"Byte {failure.start} ({failure.object[failure.start]:#04x}) "
            f"is not valid UTF-8: {failure.reason}.\n\n"
            "Read it as one of these instead?"
        )
        shift = box.addButton("Shift-JIS (cp932)", QMessageBox.ButtonRole.AcceptRole)
        latin = box.addButton("Latin-1", QMessageBox.ButtonRole.AcceptRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(latin if likely == "latin-1" else shift)
        box.exec()
        clicked = box.clickedButton()
        if clicked is shift:
            return "cp932"
        if clicked is latin:
            return "latin-1"
        return None

    def _write_text(self, path: str, text: str) -> bool:
        """Write the file as UTF-8 with LF endings; report what stopped it."""
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as exc:
            self._error(f"Cannot write {path}: {exc}")
            return False
        return True

    def _update_title(self) -> None:
        """Re-render the window title from what is currently open.

        The title carries Qt's ``[*]`` placeholder, which
        :meth:`_refresh_project_modified` turns into the platform's unsaved
        marker — a trailing ``*`` here, the close-button dot on macOS — rather
        than a ``*`` written into the string by hand, which would put the mark in
        the middle of the name on the platforms that show it elsewhere.
        """
        name = os.path.basename(self.project_path) if self.project_path else "Untitled"
        entry = f" — {self._entry.name}" if self._entry else ""
        self.setWindowTitle(f"{name}[*]{entry} — {APP_NAME}")
        self._refresh_project_modified()

    def _refresh_project_modified(self) -> None:
        """Re-evaluate the title's unsaved-project marker.

        Called from the choke points every project-visible change passes through.
        A missed one leaves only the *marker* briefly stale — the prompts that
        matter re-ask :meth:`_project_dirty` at the moment they need the answer,
        which is what makes it safe for :meth:`_push_command` to hold the question
        back over a push and ask it once at the end.
        """
        if self._defer_project_modified:
            return
        self.setWindowModified(self._project_dirty())

    def _show_shortcuts(self) -> None:
        """Help ▸ Shortcuts…: the list as the window is actually bound.

        Built from the menu bar plus the surfaces' declared keys
        (:mod:`mapchar.ui.help_dialogs`), so a shortcut that moves is right here
        without a second edit.
        """
        ShortcutGuide(shortcut_sections(self), self).exec()

    def _show_legend(self) -> None:
        """Help ▸ Legend…: what every colour and mark in the views means."""
        LegendDialog(self).exec()

    def _about(self) -> None:
        AboutDialog(self).exec()

    def _error(self, message: str) -> None:
        QMessageBox.warning(self, APP_NAME, message)

    def _ask(self, title: str, message: str) -> bool:
        """A yes/no gate in front of something that discards work."""
        return (
            QMessageBox.question(self, title, message) == QMessageBox.StandardButton.Yes
        )

    def _report(self, title: str, message: str, notices=()) -> None:
        """Say how it went: notices under the message in a dialog, or, with
        none, the message alone in the status bar."""
        notices = list(notices)
        if notices:
            TextDialog(title, message + "\n\n" + "\n".join(notices), self).exec()
        else:
            self.statusBar().showMessage(message, 5000)
