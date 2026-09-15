"""The menu bar: every row the window offers, and what it calls."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QActionGroup, QKeySequence


class MenuBarMixin:
    """The menu bar: every row the window offers, and what it calls.

    A slice of :class:`~mapchar.ui.main_window.window.MainWindow`, reaching the
    rest of the window only through ``self``.
    """

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
            file_menu, "New &Block", self._new_block, "Ctrl+Shift+B"
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
