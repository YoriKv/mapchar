"""The widget tree the window is built from, and the signals that wire it.

Every surface's widgets in one place rather than each mixin building its own:
what the window *is* reads as one tree, and what each control *does* — the one
thing not readable from where it is built — is the wiring under it. The mixin
that answers for a control owns its behaviour, not its construction.
"""

from __future__ import annotations

from PySide6.QtCore import QFileSystemWatcher, Qt, QTimer
from PySide6.QtWidgets import (
    QCheckBox,
    QDockWidget,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mapchar import APP_NAME
from mapchar.core.address import BANK_PRESETS, HEX_ID
from mapchar.plugins.base import Stage
from mapchar.ui.bars import WrapBar
from mapchar.ui.decompress_window import DecompressWindow
from mapchar.ui.files_panel import FilesPanel
from mapchar.ui.find_replace import FindReplaceDialog
from mapchar.ui.find_row import FindRow
from mapchar.ui.glossary_panel import GlossaryPanel
from mapchar.ui.glyphs import Glyph
from mapchar.ui.hex_panel import HexPanel
from mapchar.ui.main_window.file_watch import CHANGE_REST_MS
from mapchar.ui.main_window.navigation import CUSTOM_ID
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
)


class WidgetsMixin:
    """The widget tree, the docks and the tool windows, and their wiring.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

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

        # Under the Files panel: both are lists the strings are worked from.
        self.glossary_panel = GlossaryPanel()
        glossary_dock = QDockWidget("Glossary", self)
        glossary_dock.setObjectName("glossary_dock")
        glossary_dock.setWidget(self.glossary_panel)
        self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, glossary_dock)
        self.splitDockWidget(files_dock, glossary_dock, Qt.Orientation.Vertical)
        # The tree is what the column is mostly for; a drag says otherwise.
        # The column keeps the width the tree asks for, which sharing it out
        # by height would otherwise let go of.
        self.resizeDocks([files_dock, glossary_dock], [3, 2], Qt.Orientation.Vertical)
        self.resizeDocks(
            [files_dock],
            [self.files_panel.sizeHint().width()],
            Qt.Orientation.Horizontal,
        )
        self.glossary_dock = glossary_dock

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
        self.table_edit.setToolTip("Open the selected table in the Table Editor")
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
            tip="Table or encoding the text is read through",
        )
        format_bar.add_group(
            "Compression",
            self.compression_pick,
            tip="Scheme the Decompressed View reads the bytes through",
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
            "Export the block's strings to a translator or command file"
        )
        bl.addWidget(self.block_label, 1)
        bl.addWidget(self.block_export)
        self.block_bar = block_bar
        layout.addWidget(block_bar)

        self.tabs = QTabWidget()
        # The raw view's address column is spelled as every other address is.
        self.raw = RawWidget(self.address_spelling)
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
        self.offset_box.setToolTip("View address; Enter goes there")
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
        self.find_replace.find_next.connect(self._search_next)
        self.find_replace.replace_one.connect(self._search_replace)
        self.find_replace.replace_all.connect(self._search_replace_all)
        self.project_strings.go_to.connect(self._jump_to_string)
        self.project_strings.refresh_requested.connect(self._refresh_project_strings)
        self.glossary_panel.changed.connect(self._on_glossary_changed)
        self.glossary_panel.insert_requested.connect(self._insert_glossary)
        self.glossary_panel.replace_requested.connect(self._replace_glossary_terms)
        self.glossary_panel.strings_requested.connect(self._show_term_strings)
        self.glossary_panel.uses_requested.connect(self._count_glossary_uses)
        self.glossary_panel.import_requested.connect(self._import_glossary)
        self.glossary_panel.export_requested.connect(self._export_glossary)
        self.glossary_dock.visibilityChanged.connect(
            lambda shown: shown and self._sync_glossary()
        )
        self.strings.glossary_add_requested.connect(self._add_to_glossary)
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
        self.file_watcher = QFileSystemWatcher(self)
        self.file_watcher.fileChanged.connect(self._on_file_changed)
        self._file_change_rest = QTimer(self)
        self._file_change_rest.setSingleShot(True)
        self._file_change_rest.setInterval(CHANGE_REST_MS)
        self._file_change_rest.timeout.connect(self._check_changed_files)
        self.workspace.on_added.append(self._watch_file)
        self.workspace.on_removed.append(self._unwatch_file)
        self.workspace.on_reset.append(self._rewatch_files)

    def _reset_layout(self) -> None:
        """Panels ▸ Reset Panel Layout: the arrangement a fresh install has.

        The factory state is whatever the docks built for themselves, captured by
        :class:`~mapchar.ui.window_layout.WindowLayout` before anything was
        restored — so this is one call rather than a list of docks to show and
        hide that a new dock would fall out of.
        """
        self._window_layout.reset()
