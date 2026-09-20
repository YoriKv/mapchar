"""The main window's shell: the class every mixin is a slice of, and the few
things every one of them needs from it.

What is left when every surface has a module of its own (see the package
docstring): the documented state the mixins share, the shared undo stack
together with the guard, the grouping and the reach every command applies
through, the window title and the project's unsaved marker, the file dialogs
and the text IO, and the error modal. The widget tree itself is
:mod:`mapchar.ui.main_window.widget_tree`. Not one more surface — the shell the
mixins hang off.
"""

from __future__ import annotations

import os
from contextlib import contextmanager

from PySide6.QtCore import QTimer
from PySide6.QtGui import QUndoStack
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QWidget,
)

from mapchar import APP_NAME
from mapchar.core.bits import Bits
from mapchar.core.document import Document
from mapchar.pipeline.text_view import TextDecode
from mapchar.plugins.registry import Registry, default_registry
from mapchar.project.entry import Entry
from mapchar.project.formats.textfile import not_utf8, read_text_any
from mapchar.project.workspace import Workspace
from mapchar.ui import settings
from mapchar.ui.dialogs import (
    TextDialog,
)
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons
from mapchar.ui.main_window.autosave import AutosaveMixin
from mapchar.ui.main_window.block_reading import BlockReadingMixin
from mapchar.ui.main_window.blocks import BlocksMixin
from mapchar.ui.main_window.capability_sync import CapabilitySyncMixin
from mapchar.ui.main_window.compression import CompressionMixin
from mapchar.ui.main_window.containers import ContainerMixin
from mapchar.ui.main_window.entries import EntriesMixin
from mapchar.ui.main_window.entry_clipboard import EntryClipboardMixin
from mapchar.ui.main_window.extraction import ExtractionMixin
from mapchar.ui.main_window.files_menu import FilesMenuMixin
from mapchar.ui.main_window.format_bar import FormatBarMixin
from mapchar.ui.main_window.glossary import GlossaryMixin
from mapchar.ui.main_window.help import HelpMixin
from mapchar.ui.main_window.hex_view import HexViewMixin
from mapchar.ui.main_window.history import HistoryMixin
from mapchar.ui.main_window.import_export import ImportExportMixin
from mapchar.ui.main_window.legacy_exchange import LegacyExchangeMixin
from mapchar.ui.main_window.menus import MenuBarMixin
from mapchar.ui.main_window.navigation import NavigationMixin
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
from mapchar.ui.main_window.replacing import FindReplaceMixin
from mapchar.ui.main_window.search import SearchMixin
from mapchar.ui.main_window.session import SessionMixin
from mapchar.ui.main_window.string_edit import StringEditMixin
from mapchar.ui.main_window.string_rows import StringRowsMixin
from mapchar.ui.main_window.strings_menu import StringsMenuMixin
from mapchar.ui.main_window.structure_scan import StructureScanMixin
from mapchar.ui.main_window.table_edits import TableEditorMixin
from mapchar.ui.main_window.table_files import TableFilesMixin
from mapchar.ui.main_window.text_tab import TextViewMixin
from mapchar.ui.main_window.widget_tree import WidgetsMixin
from mapchar.ui.main_window.wrap import WrapMixin
from mapchar.ui.main_window.writing import WritingMixin
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
    StructureScanMixin,
    PluginsMixin,
    TableFilesMixin,
    TableEditorMixin,
    RawViewMixin,
    BlocksMixin,
    ExtractionMixin,
    StringRowsMixin,
    StringsMenuMixin,
    BlockReadingMixin,
    StringEditMixin,
    WrapMixin,
    FindReplaceMixin,
    ProjectStringsMixin,
    GlossaryMixin,
    SearchMixin,
    RelativeSearchMixin,
    PointerDiscoveryMixin,
    ImportExportMixin,
    LegacyExchangeMixin,
    ProjectMixin,
    AutosaveMixin,
    RelocateMixin,
    PreviewMixin,
    HexViewMixin,
    MenuBarMixin,
    WidgetsMixin,
    HelpMixin,
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
        up retraces one exactly (:mod:`mapchar.ui.main_window.text_tab`)."""
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
        load ends with (:meth:`~mapchar.ui.main_window.extraction.
        ExtractionMixin._note_load_problem`)."""
        self._slots_cache: tuple | None = None
        """Where each string's slot ends, with the records and the bytes it was
        worked out from (:meth:`~mapchar.ui.main_window.string_rows.
        StringRowsMixin._string_slots`)."""
        self._same_counts: tuple | None = None
        """How many of the current block's strings share each original, with the
        records counted (:meth:`~mapchar.ui.main_window.string_rows.
        StringRowsMixin._same_originals`)."""
        self._checked_extraction: tuple | None = None
        """The reading a string edit checked its own result against, kept for
        the re-read that follows it (:meth:`~mapchar.ui.main_window.extraction.
        ExtractionMixin._extract_current`)."""
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
