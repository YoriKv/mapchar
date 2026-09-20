"""The main window's shell: the widgets, the docks, the menus, and the few things
every mixin needs from it.

What is left when every surface has a module of its own (see the package
docstring): the widget tree and the four docks with the signals that wire them,
the shared undo stack together with the guard, the grouping and the reach every
command applies through, the window title and the project's unsaved marker, the
file dialogs, and the error modal. Not one more surface — the shell the mixins
hang off.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtGui import QPalette, QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QDockWidget,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mapchar import APP_NAME
from mapchar.core.address import BANK_PRESETS, HEX_ID
from mapchar.core.bits import Bits
from mapchar.core.document import Document
from mapchar.pipeline.text_view import TextDecode
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import Registry, default_registry
from mapchar.project.formats.textfile import not_utf8, read_text_any
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
from mapchar.ui.find_row import FindRow
from mapchar.ui.glossary_window import GlossaryWindow
from mapchar.ui.glyphs import Glyph
from mapchar.ui.help_dialogs import (
    AboutDialog,
    LegendDialog,
    ShortcutGuide,
    shortcut_sections,
)
from mapchar.ui.hex_panel import HexPanel
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.main_window.autosave import AutosaveMixin
from mapchar.ui.main_window.blocks import BlocksMixin
from mapchar.ui.main_window.capability_sync import CapabilitySyncMixin
from mapchar.ui.main_window.compression import CompressionMixin
from mapchar.ui.main_window.containers import ContainerMixin
from mapchar.ui.main_window.entries import EntriesMixin
from mapchar.ui.main_window.entry_clipboard import EntryClipboardMixin
from mapchar.ui.main_window.files_menu import FilesMenuMixin
from mapchar.ui.main_window.find_replace import FindReplaceMixin
from mapchar.ui.main_window.format_bar import FormatBarMixin
from mapchar.ui.main_window.glossary import GlossaryMixin
from mapchar.ui.main_window.hex_view import HexViewMixin
from mapchar.ui.main_window.history import HistoryMixin
from mapchar.ui.main_window.import_export import ImportExportMixin
from mapchar.ui.main_window.menus import MenuBarMixin
from mapchar.ui.main_window.navigation import CUSTOM_ID, NavigationMixin
from mapchar.ui.main_window.opening import OpeningMixin
from mapchar.ui.main_window.plugins import PluginsMixin
from mapchar.ui.main_window.pointers import PointerDiscoveryMixin
from mapchar.ui.main_window.preview import PreviewMixin
from mapchar.ui.main_window.project_strings import ProjectStringsMixin
from mapchar.ui.main_window.projects import ProjectMixin
from mapchar.ui.main_window.raw_view import RawViewMixin
from mapchar.ui.main_window.refresh import DRAG_REST_MS, RefreshMixin
from mapchar.ui.main_window.relative_search import RelativeSearchMixin
from mapchar.ui.main_window.relocate import RelocateMixin
from mapchar.ui.main_window.search import SearchMixin
from mapchar.ui.main_window.session import SessionMixin
from mapchar.ui.main_window.string_edit import StringEditMixin
from mapchar.ui.main_window.strings_view import StringsViewMixin
from mapchar.ui.main_window.table_editor import TableEditorMixin
from mapchar.ui.main_window.table_files import TableFilesMixin
from mapchar.ui.main_window.text_view import TextViewMixin
from mapchar.ui.main_window.wrap import WrapMixin
from mapchar.ui.main_window.writing import WritingMixin
from mapchar.ui.number_fields import AddressEdit, AddressSpelling, HexEdit
from mapchar.ui.preview_window import PreviewWindow
from mapchar.ui.project_strings_window import ProjectStringsWindow
from mapchar.ui.raw_widget import RawWidget
from mapchar.ui.reading_bar import ReadingBar
from mapchar.ui.scan_window import ScanWindow
from mapchar.ui.search_window import SearchWindow
from mapchar.ui.strings_view import StringsView
from mapchar.ui.table_editor import TableEditor
from mapchar.ui.text_widget import TextWidget
from mapchar.ui.widgets import (
    CommandComboBox,
    CompactComboBox,
    ElidedLabel,
    ModeToggle,
    WrapBar,
)
from mapchar.ui.window_layout import WindowLayout


class MainWindow(
    SessionMixin,
    RefreshMixin,
    TextViewMixin,
    CapabilitySyncMixin,
    FormatBarMixin,
    NavigationMixin,
    HistoryMixin,
    OpeningMixin,
    EntriesMixin,
    FilesMenuMixin,
    EntryClipboardMixin,
    ContainerMixin,
    WritingMixin,
    CompressionMixin,
    PluginsMixin,
    TableFilesMixin,
    TableEditorMixin,
    RawViewMixin,
    BlocksMixin,
    StringsViewMixin,
    StringEditMixin,
    WrapMixin,
    FindReplaceMixin,
    ProjectStringsMixin,
    GlossaryMixin,
    SearchMixin,
    RelativeSearchMixin,
    PointerDiscoveryMixin,
    ImportExportMixin,
    ProjectMixin,
    AutosaveMixin,
    RelocateMixin,
    PreviewMixin,
    HexViewMixin,
    MenuBarMixin,
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
        self._macros: list[str | None] = []
        """The macros :meth:`_macro` has open, outermost first; a text that is
        still there has not been begun, because nothing has been pushed in it."""
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
        """The Text tab's wheel steps down, as ``(from, to, lines)``, so a step
        up retraces one exactly (:mod:`mapchar.ui.main_window.text_view`)."""
        self._text_decode: TextDecode | None = None
        """The Text tab's tokens, kept from one window to the next."""
        self._text_guess = 0
        """How many bytes the Text tab's last window took to fill its box."""
        self._previews: tuple[tuple, Bits, dict[int, str]] | None = None
        """What the pointers' targets read as, by target, with what they were
        read from and through (:meth:`_pointer_preview`)."""
        self._text_up_guess = 0.0
        """How many bytes back a line of the text above the Text tab's window
        was, the last time one was looked for."""
        self._drag_rest = QTimer(self)
        self._drag_rest.setSingleShot(True)
        self._drag_rest.setInterval(DRAG_REST_MS)
        self._drag_rest.timeout.connect(self._on_drag_rest)
        """Restarted by every move of a dragged view, so the refresh the drag
        put off runs once it stops (:meth:`_refresh_view`)."""
        self._selection: tuple[int, int] | None = None
        self._bars_show: tuple | None = None
        """The entry and reading the bars were last loaded with."""
        self._string_bounds: tuple[int, int] | None = None
        """The bytes of the strings last opened as text — a string from the Files
        panel, or a pointer block's in the Strings mode: while the view is
        confined to exactly them, it reads them as text
        (:mod:`mapchar.ui.main_window.blocks`)."""
        self._preview_scheme: str | None = None
        """The compression scheme the Decompressed view is reading the bytes
        through at this moment: the Compression picker's, or, on automatic,
        whichever scheme's signature the view has landed on
        (:mod:`mapchar.ui.main_window.compression`)."""
        self._auto_armed = False
        """Whether :attr:`_preview_scheme` was armed by a signature rather than
        picked, which is the one thing the picker cannot show."""
        self._structures_file: Entry | None = None
        """The file the Decompressed view's structure list was found in; another
        file on screen drops it."""
        self._load_notices: list[str] = []
        """What reading the blocks had to say, kept for the dialog a project
        load ends with (:meth:`~mapchar.ui.main_window.strings_view.
        StringsViewMixin._note_load_problem`)."""
        self._slots_cache: tuple | None = None
        """Where each string's slot ends, with the records and the bytes it was
        worked out from (:meth:`~mapchar.ui.main_window.strings_view.
        StringsViewMixin._string_slots`)."""
        self._same_counts: tuple | None = None
        """How many of the current block's strings share each original, with the
        records counted (:meth:`~mapchar.ui.main_window.strings_view.
        StringsViewMixin._same_originals`)."""
        self._checked_extraction: tuple | None = None
        """The reading a string edit checked its own result against, kept for
        the re-read that follows it (:meth:`~mapchar.ui.main_window.strings_view.
        StringsViewMixin._extract_current`)."""
        self._strings_texts: dict[int, tuple[list, str]] = {}
        """Each block's strings as the project file would spell them, by entry,
        with the records they were spelled from
        (:meth:`~mapchar.ui.main_window.projects.ProjectMixin._snapshot`)."""
        self._rows_patched = False
        """Set while a string edit has refreshed the grid's changed rows itself,
        so the refresh that follows leaves the grid alone."""
        self._reading_consent: Entry | None = None
        """The block whose edited strings the user has agreed to have cut
        afresh, so a run of changes to how it is read asks once
        (:meth:`~mapchar.ui.main_window.blocks.BlocksMixin._confirm_recut`).
        Held for the session and dropped the moment one of its strings is
        edited again."""
        self._step_icons: list[tuple[QPushButton, Glyph]] = []
        # Before anything can make an entry current: the first visit arms the
        # trail's two actions, and _build_menus puts them in the Navigate menu.
        self._init_history()
        self._build_widgets()
        self._build_menus()
        # The File menu's own Export menu, so the Block bar's button and the
        # menu bar can never drift apart: a QMenu is shown from wherever it
        # is asked for rather than owned by one place on screen. After the
        # menus, which the widgets are built before.
        self.block_export.setMenu(self.export_menu)
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
        self._refresh_table_picks()
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

        # How every address field spells a position; the address format sets it.
        self.address_spelling = AddressSpelling(self)
        self.hex_panel = HexPanel(self.address_spelling)
        hex_dock = QDockWidget("Hex", self)
        hex_dock.setObjectName("hex_dock")
        hex_dock.setWidget(self.hex_panel)
        self.addDockWidget(Qt.DockWidgetArea.BottomDockWidgetArea, hex_dock)
        hex_dock.hide()
        self.hex_dock = hex_dock

        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(4, 4, 4, 4)

        # In the editing column rather than the window's toolbar area, which
        # would run across the top of the docks: the bars describe the view
        # under them, as celPix's do.
        format_bar = WrapBar()
        format_bar.setObjectName("format_bar")
        self.format_pick = CommandComboBox("New Table…")
        # Only a loaded table can be edited, so a charset leaves it disabled.
        self.table_edit = QPushButton("Edit…")
        self.table_edit.setToolTip("Open the picked table in the Table Editor")
        # Always on the bar, so nothing shifts when an entry that cannot arm a
        # preview is on screen; what it may hold is then the question.
        self.compression_pick = CompactComboBox()
        self.mode_toggle = ModeToggle((("Strings", False), ("Pointers", True)))
        self.mode_toggle.button(False).setToolTip(
            "Read the bytes as text; on a block, show its strings"
        )
        self.mode_toggle.button(True).setToolTip(
            "Read the bytes as pointers; on a block, show its pointer table"
        )
        self.resolve_pointers = QCheckBox("Follow pointers")
        format_bar.add_group(
            "Table",
            self.format_pick,
            self.table_edit,
            tip="The table or encoding the text is read through",
        )
        format_bar.add_group(
            "Compression",
            self.compression_pick,
            tip="The scheme the Decompressed View reads the bytes through",
        )
        format_bar.add_group("Show as", self.mode_toggle)
        self.resolve_group = format_bar.add_group(
            "",
            self.resolve_pointers,
            tip="Show the string each pointer points to",
        )
        layout.addWidget(format_bar)
        self.format_bar = format_bar
        self.reading_bar = ReadingBar(self.address_spelling)
        self.reading_bar.set_mappings(self.registry.plugins(Stage.MAPPING))
        layout.addWidget(self.reading_bar)
        self._reset_builtin_tables()
        self._fill_compression_pick()

        block_bar = QWidget()
        bl = QHBoxLayout(block_bar)
        bl.setContentsMargins(0, 0, 0, 0)
        self.block_label = ElidedLabel("")
        self.block_export = QPushButton("Export…")
        self.block_export.setToolTip(
            "Write the block's strings to a translator or command file"
        )
        bl.addWidget(self.block_label, 1)
        bl.addWidget(self.block_export)
        self.block_bar = block_bar
        layout.addWidget(block_bar)

        self.tabs = QTabWidget()
        self.raw = RawWidget()
        # The raw view's address column is spelled as every other address is.
        self._sync_raw_addresses()
        self.address_spelling.changed.connect(self._sync_raw_addresses)
        self.text = TextWidget()
        self.strings = StringsView()
        self.tabs.addTab(self.raw, "Hex")
        self.tabs.addTab(self.text, "Text")
        self.tabs.addTab(self.strings, "Strings")
        layout.addWidget(self.tabs, 1)

        nav = QWidget()
        nl = QHBoxLayout(nav)
        nl.setContentsMargins(0, 0, 0, 0)
        self.offset_box = AddressEdit(self.address_spelling)
        self.offset_box.setPlaceholderText("address")
        self.offset_box.setToolTip("The view's address; Enter goes there")
        # How a position is spelled: a flat file offset, one of the console
        # mapping presets, or the three numbers beside the picker
        # (mapchar.core.address).
        self.address_pick = CompactComboBox()
        self.address_pick.addItem("Hex", HEX_ID)
        for preset in BANK_PRESETS:
            self.address_pick.addItem(preset.name, preset.id)
        self.address_pick.addItem("Custom bank…", CUSTOM_ID)
        self.address_pick.setToolTip(
            "Address format: file offset, a console mapping, or custom banks"
        )
        self.custom_bank_row = QWidget()
        cb = QHBoxLayout(self.custom_bank_row)
        cb.setContentsMargins(0, 0, 0, 0)
        self.bank_size_box = HexEdit(4)
        self.addr_base_box = HexEdit(4)
        self.bank_base_box = HexEdit(2)
        for label, box, value, tip in (
            ("Bank size", self.bank_size_box, 0x8000, "Bytes of ROM per bank"),
            (
                "at",
                self.addr_base_box,
                0x8000,
                "In-bank address of a bank's first byte",
            ),
            (
                "from bank",
                self.bank_base_box,
                0,
                "Bank number of the file's first byte",
            ),
        ):
            box.set_value(value)
            box.setToolTip(f"{tip}, in hex")
            cb.addWidget(QLabel(label))
            cb.addWidget(box)
        nl.addWidget(self.address_pick)
        nl.addWidget(self.custom_bank_row)
        nl.addWidget(self.offset_box)
        # The row steps wear the bundled icon font; the byte and page steps
        # stay words, since the font has no mark that says "byte" or "page".
        steps: list[QPushButton] = []
        for text, glyph, delta, tip in (
            ("Home", None, "home", "Start of the view (Home)"),
            ("Pg Up", None, "page-up", "Page up (PgUp)"),
            ("", Glyph.ARROW_UP, "row-up", "Row up, or a line in Text (Up)"),
            ("−B", None, -1, "Byte back (Left or −)"),
            ("+B", None, 1, "Byte forward (Right or +)"),
            ("", Glyph.ARROW_DOWN, "row-down", "Row down, or a line in Text (Down)"),
            ("Pg Dn", None, "page-down", "Page down (PgDn)"),
            ("End", None, "end", "End of the view (End)"),
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
        # The Find bar: the current byte search, under the navigation row as the
        # last row of the editing column, where Ctrl+F puts the keyboard and F3
        # reads from (mapchar.ui.main_window.search).
        find_bar = QWidget()
        find_bar.setObjectName("find_bar")
        fl = QHBoxLayout(find_bar)
        fl.setContentsMargins(0, 0, 0, 0)
        self.find_row = FindRow(chars=40)
        fl.addWidget(QLabel("Find"))
        fl.addWidget(self.find_row, 1)
        layout.addWidget(find_bar)
        self.find_bar = find_bar
        self.setCentralWidget(central)

        self.search_window = SearchWindow(self)
        self.scan_window = ScanWindow(self)
        self.decompress_window = DecompressWindow(self)
        self.preview_window = PreviewWindow(self)
        self.table_editor = TableEditor(self)
        self.find_replace = FindReplaceDialog(self)
        self.project_strings = ProjectStringsWindow(self)
        self.glossary_window = GlossaryWindow(self)

        self._connect_signals()
        self._start_autosave()

    def _connect_signals(self) -> None:
        """Wire every widget the shell owns to the mixin that answers for it.

        One place rather than a trailer on ``_build_widgets``: what a control
        does is the one thing about it not readable from where it is built.
        """
        self.files_panel.entry_activated.connect(self._show_entry)
        self.files_panel.string_activated.connect(self._show_string)
        self.files_panel.group_activated.connect(self._show_group)
        self.files_panel.strings_requested.connect(self._read_block_strings)
        self.files_panel.entry_double_clicked.connect(self._on_entry_double)
        self.files_panel.context_menu_requested.connect(self._files_menu)
        self.files_panel.remove_requested.connect(self._remove_entries)
        self.files_panel.rename_committed.connect(self._commit_rename)
        self.files_panel.reorder_requested.connect(self._reorder_entry)
        self.files_panel.place_requested.connect(self._place_entries)
        self.files_panel.move_requested.connect(self._move_entries)
        self.files_panel.cut_requested.connect(self._cut_entries)
        self.files_panel.copy_requested.connect(self._copy_entries)
        self.files_panel.paste_requested.connect(self._paste_entries)
        self.files_panel.duplicate_requested.connect(self._duplicate_entries)
        self.format_pick.chosen.connect(self._on_format_pick)
        self.compression_pick.currentIndexChanged.connect(self._on_compression_pick)
        self.table_edit.clicked.connect(self._edit_picked_table)
        self.format_pick.command.connect(lambda: self._new_table_dialog(start=True))
        self.mode_toggle.chosen.connect(self._on_mode)
        self.resolve_pointers.toggled.connect(self._on_resolve_pointers)
        self.reading_bar.edited.connect(self._on_reading_edited)
        self.raw.offset_requested.connect(self._go_to)
        self.raw.rows_changed.connect(self._on_raw_rows_changed)
        self.text.selection_changed.connect(self._on_text_selection)
        self.text.fit_changed.connect(self._on_text_fit_changed)
        self.text.shown_changed.connect(self._on_text_shown_changed)
        self.text.scroll_requested.connect(self._on_text_scroll)
        self.text.page_requested.connect(self._step_pages)
        self.text.offset_requested.connect(self._go_to)
        # Letting go settles the view at once, rather than after the timer the
        # last move of the drag started.
        self.text.bar.sliderReleased.connect(self._on_drag_rest)
        self.raw.verticalScrollBar().sliderReleased.connect(self._on_drag_rest)
        self.tabs.currentChanged.connect(self._on_tab_changed)
        self.raw.selection_changed.connect(self._on_selection)
        self.raw.context_menu_requested.connect(self._raw_menu)
        self.offset_box.returnPressed.connect(self._on_offset_typed)
        self.address_pick.currentIndexChanged.connect(self._on_address_format)
        for box in (self.bank_size_box, self.addr_base_box, self.bank_base_box):
            box.editingFinished.connect(self._on_custom_bank)
        self._restore_address_format()
        self.strings.row_selected.connect(self._on_string_row)
        self.strings.translation_edited.connect(self._on_translation_edited)
        self.strings.commit_handler = self._commit_translation
        self.strings.problem_shown.connect(self._on_edit_problem)
        self.strings.notes_edited.connect(self._on_notes_edited)
        self.strings.draft_changed.connect(self._on_draft)
        self.strings.context_menu_requested.connect(self._strings_menu)
        self.search_window.go_to.connect(self._select_bytes)
        self.search_window.build_table.connect(self._build_table_from_hit)
        self.scan_window.go_to.connect(self._select_bytes)
        self.scan_window.new_block.connect(self._block_from_region)
        self.decompress_window.jump_next.connect(self._jump_next_structure)
        self.decompress_window.scan_next.connect(self._scan_next_structure)
        self.decompress_window.to_block.connect(self._structure_to_block)
        self.decompress_window.find_all.connect(self._find_all_structures)
        self.decompress_window.go_to.connect(self._go_to_structure)
        self.preview_window.font_changed.connect(self._on_preview_font_changed)
        self.preview_window.box_changed.connect(self._on_box_changed)
        self.preview_window.wrap_requested.connect(self._wrap_selected)
        self.table_editor.changed.connect(self._on_table_edited)
        self.table_editor.save_requested.connect(
            lambda entry, ask: self._save_table_entry(entry, ask=ask)
        )
        self.table_editor.table_requested.connect(self._edit_table_entry)
        self.table_editor.rename_requested.connect(self._rename_table)
        self.table_editor.includes_chosen.connect(self._on_includes_chosen)
        self.table_editor.inheritance = self._table_inheritance
        self.table_editor.speller = self.address_spelling.format
        self.hex_panel.go_to_requested.connect(self._go_to)
        self.hex_panel.overtype_requested.connect(self.overtype_bytes)
        self.hex_panel.find_requested.connect(self._find_text_or_bytes)
        self.find_row.find_requested.connect(
            lambda _text, backwards: self._find_bytes(again=True, backwards=backwards)
        )
        self.hex_dock.visibilityChanged.connect(lambda v: v and self._sync_hex_panel())
        self.find_replace.find_next.connect(self._fr_find_next)
        self.find_replace.replace_one.connect(self._fr_replace_one)
        self.find_replace.replace_all.connect(self._fr_replace_all)
        self.project_strings.go_to.connect(self._jump_to_string)
        self.project_strings.refresh_requested.connect(self._refresh_project_strings)
        self.glossary_window.changed.connect(self._on_glossary_changed)
        self.glossary_window.insert_requested.connect(self._insert_glossary)
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

    def _sync_raw_addresses(self, *_) -> None:
        """Spell the raw view's address column the way every other address the
        window shows is spelled; flat hex is left to the view, which sizes the
        column to the file."""
        spelling = self.address_spelling
        if spelling.layout is None:
            self.raw.set_address_format(None)
        else:
            self.raw.set_address_format(spelling.format, len(spelling.format(0)))

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
        # A session ended on purpose leaves no copy to recover.
        self._discard_autosave()
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

    @contextmanager
    def _macro(self, text: str):
        """Group every command pushed inside into one undo step.

        A no-op while an apply is running, for the same reason
        :meth:`_push_command` refuses to push then: an apply must leave the stack
        exactly as it found it. ``beginMacro`` is deferred until the first push
        actually lands, so a run that changes nothing — an import with nothing to
        import, a replace-all with no match, a wrap that rewrapped nothing —
        leaves no dead step behind.
        """
        if self._applying_undo:
            yield
            return
        self._macros.append(text)
        try:
            yield
        finally:
            if self._macros.pop() is None:  # something was pushed, so it opened
                self.undo_stack.endMacro()
            if not self._macros and self._defer_project_modified:
                # Held over every push inside, and asked once for the lot.
                self._defer_project_modified = False
                self._refresh_project_modified()

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
        the same one every intermediate ask would have got; inside a macro, once
        after the macro, however many rows a paste pushed.

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
        # The first push inside a macro is what opens it — outermost first — so
        # a macro that ends up pushing nothing costs no step at all.
        for i, text in enumerate(self._macros):
            if text is not None:
                self.undo_stack.beginMacro(text)
                self._macros[i] = None
        self._defer_project_modified = True
        try:
            self.undo_stack.push(command)
        finally:
            if not self._macros:
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

    def _read_text(self, path: str) -> tuple[str | None, list[str]]:
        """The file as text with what reading it had to say, or ``None`` once
        the reason it is not is reported.

        Read as a table file is (:func:`~mapchar.project.formats.textfile.
        read_text_any`): UTF-8, else ``cp932``, else ``latin-1``. A command
        file, an Atlas script or a translator file that came from elsewhere is
        as likely to be Shift-JIS or Latin-1, and one that is says so in the
        notices the import ends with, as a table does in its status line.
        """
        try:
            text, encoding = read_text_any(path)
        except OSError as exc:
            self._error(f"Cannot read {path}: {exc}")
            return None, []
        notice = not_utf8(path, encoding)
        return text, [notice] if notice else []

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
        back over a push and ask it once at the end — and for a dragged view to
        hold it back until the drag settles, since answering it serialises the
        whole project and each move of the drag pushes a command of its own.
        """
        if self._defer_project_modified or self._dragging():
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
