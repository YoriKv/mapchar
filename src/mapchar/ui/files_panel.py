"""The Files panel: String Data — files with their blocks and bookmarks —
tables, fonts, and under each block, its strings."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QLineEdit,
    QTreeWidgetItem,
    QWidget,
)

from mapchar.core.textmatch import matches_words, words_of
from mapchar.project.workspace import Entry, EntryKind, Workspace
from mapchar.ui.entry_text import label, status_mark, string_preview, tooltip
from mapchar.ui.entry_tree import EntryTree
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.panel import WorkspaceTreePanel
from mapchar.ui.theme import NOTICE_WASH, WARNING_INK
from mapchar.ui.widgets import show_elided_tooltips

if TYPE_CHECKING:
    from mapchar.core.block import StringRecord

GROUPS = {
    EntryKind.FILE: "String Data",
    EntryKind.TABLE: "Tables",
    EntryKind.FONT: "Fonts",
}
# The row markers: a glyph in a palette role. Files and fonts sit under their
# own group headings and carry no mark; a bookmark wears the accent.
MARKERS: dict[EntryKind, tuple[Glyph, QPalette.ColorRole]] = {
    EntryKind.BLOCK: (Glyph.GRID_ROWS, QPalette.ColorRole.Text),
    EntryKind.BOOKMARK: (Glyph.FLAG, QPalette.ColorRole.Highlight),
    EntryKind.TABLE: (Glyph.GRID, QPalette.ColorRole.Text),
}
ICON_SIZE = QSize(13, 16)
STATUS_COL = 1
"""The narrow second column: ``?`` for a missing file, ``!`` for notices."""
SORT_KEYS = ("Name", "Type", "Offset")
"""What **Sort by** offers; Offset only makes sense among a file's children."""

STRING_ROLE = Qt.ItemDataRole.UserRole + 1
"""On a string row: ``(block entry id, string index)``. The entry role stays
empty there, so nothing that acts on entries mistakes a string for one."""
_UNBUILT = object()
"""The stub under a block whose strings are not built: it keeps the expander,
and opening the block replaces it."""


class FilesPanel(ThemedIcons, WorkspaceTreePanel):
    entry_activated = Signal(object)
    """Clicked: show this entry."""
    string_activated = Signal(object, int)
    """A string row clicked: show this block, confined to the string at index."""
    strings_requested = Signal(object)
    """A block the session has not read was opened: read it, so its strings
    can be listed."""
    context_menu_requested = Signal(object, QPoint)
    remove_requested = Signal(list)
    rename_committed = Signal(object, str)
    """An inline rename was committed: entry, new name."""
    reorder_requested = Signal(object, object)
    """Put this entry in front of that one (``None``: last among its siblings)."""
    move_requested = Signal(list, int)
    """Step these entries one place: ``-1`` up, ``+1`` down."""
    cut_requested = Signal(list)
    copy_requested = Signal(list)
    paste_requested = Signal(object)
    duplicate_requested = Signal(list)

    tree_class = EntryTree

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(workspace, parent)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter…")
        self.filter.setClearButtonEnabled(True)
        self.box.insertWidget(0, self.filter)
        self.tree.setHeaderHidden(True)
        self.tree.setColumnCount(2)
        # The name takes whatever the status mark leaves: with the header
        # hidden nothing else would widen it past Qt's default section width.
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(STATUS_COL, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIconSize(ICON_SIZE)
        # A narrow dock cuts the names short; hovering one reads it in full.
        show_elided_tooltips(self.tree)
        self._icons: dict[tuple[Glyph, str], QIcon] = {}
        self._items: dict[int, QTreeWidgetItem] = {}
        self._groups: dict[EntryKind, QTreeWidgetItem] = {}
        self._editing: Entry | None = None
        self._expanded: set[int] = set()
        """The blocks open to their strings, by entry id, so a rebuild — a row
        added, removed or reordered — puts them back open."""
        self._string_keys: dict[int, object] = {}
        self._labels_held = False
        """What each block's string rows were built from, so a refresh that
        changed nothing about the strings leaves the rows alone."""
        self.filter.textChanged.connect(self._apply_filter)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.current_navigated.connect(self._activate)
        self.tree.itemExpanded.connect(self._on_expanded)
        self.tree.itemCollapsed.connect(self._on_collapsed)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.customContextMenuRequested.connect(self._on_menu)
        self.tree.delete_pressed.connect(
            lambda: self._emit_selected(self.remove_requested)
        )
        self.tree.cut_pressed.connect(lambda: self._emit_selected(self.cut_requested))
        self.tree.copy_pressed.connect(lambda: self._emit_selected(self.copy_requested))
        self.tree.duplicate_pressed.connect(
            lambda: self._emit_selected(self.duplicate_requested)
        )
        self.tree.paste_pressed.connect(
            lambda: self.paste_requested.emit(self.entry_of(self.tree.currentItem()))
        )
        self.tree.rename_pressed.connect(self._on_rename_pressed)
        self.tree.move_pressed.connect(self._on_move)
        self.tree.reorder_dropped.connect(self._on_dropped)
        self.tree.itemDelegate().closeEditor.connect(self._on_editor_closed)
        workspace.on_current_changed.append(self._on_current)
        workspace.on_dirty_changed.append(lambda e: self._update_item(e))
        self.rebuild()

    # -- building -------------------------------------------------------

    def rebuild(self) -> None:
        self.tree.clear()
        self._items.clear()
        self._groups.clear()
        self._string_keys.clear()
        self._editing = None
        for kind, title in GROUPS.items():
            group = QTreeWidgetItem([title])
            group.setFlags(Qt.ItemFlag.ItemIsEnabled)
            font = group.font(0)
            font.setBold(True)
            group.setFont(0, font)
            self.tree.addTopLevelItem(group)
            group.setExpanded(True)
            self._groups[kind] = group
        for entry in self.workspace.entries:
            if entry.is_child:
                continue
            item = self._make_item(entry)
            self._groups[entry.kind].addChild(item)
            for child in self.workspace.children(entry):
                item.addChild(self._make_item(child))
            item.setExpanded(True)
        # Blocks open before the rebuild open again, which builds their rows.
        self._expanded &= set(self._items)
        for key in list(self._expanded):
            item = self._items.get(key)
            if item is not None:
                item.setExpanded(True)
        self._apply_filter(self.filter.text())
        self._on_current(self.workspace.current)

    def _make_item(self, entry: Entry) -> QTreeWidgetItem:
        item = QTreeWidgetItem([""])
        item.setData(0, Qt.ItemDataRole.UserRole, id(entry))
        self._items[id(entry)] = item
        self._dress(entry, item)
        return item

    def _dress(self, entry: Entry, item: QTreeWidgetItem) -> None:
        """Put the row's label, marks, wash and tooltip on ``item``."""
        item.setText(0, label(entry))
        item.setIcon(0, self._marker(entry))
        mark, why = status_mark(entry, self.workspace.tables())
        item.setText(STATUS_COL, mark)
        wash = QBrush(NOTICE_WASH) if mark else QBrush()
        for column in (0, STATUS_COL):
            item.setBackground(column, wash)
        tip = tooltip(entry, why)
        item.setToolTip(0, tip)
        item.setToolTip(STATUS_COL, tip)
        if entry.kind is EntryKind.BLOCK:
            self._sync_strings(entry, item)

    # -- a block's strings ----------------------------------------------

    def _sync_strings(self, entry: Entry, item: QTreeWidgetItem) -> None:
        """The rows under a block: its strings while it is open, else one stub
        that keeps the expander.

        Built only while the block is open, since a project's blocks can hold
        tens of thousands of strings between them and a rebuild is frequent.
        A block the session has not read keeps its stub when opened, and the
        opening asks for the read (:attr:`strings_requested`); a block that
        cannot be read at all has nothing to open.
        """
        if entry.missing or entry.config is None:
            self._string_keys.pop(id(entry), None)
            item.takeChildren()
            return
        doc = entry.doc
        if id(entry) not in self._expanded or doc is None:
            self._string_keys.pop(id(entry), None)
            if not (item.childCount() == 1 and self._is_stub(item.child(0))):
                item.takeChildren()
                item.addChild(self._stub())
            return
        key = (id(doc), id(doc.strings), len(doc.strings))
        if self._string_keys.get(id(entry)) == key:
            return
        self._string_keys[id(entry)] = key
        item.takeChildren()
        item.addChildren([self._string_item(entry, rec) for rec in doc.strings])
        if self.filter.text():
            self._apply_filter(self.filter.text())

    @staticmethod
    def _stub() -> QTreeWidgetItem:
        stub = QTreeWidgetItem(["…"])
        stub.setData(0, STRING_ROLE, _UNBUILT)
        stub.setFlags(Qt.ItemFlag.NoItemFlags)
        return stub

    @staticmethod
    def _is_stub(item: QTreeWidgetItem) -> bool:
        return item.data(0, STRING_ROLE) is _UNBUILT

    def _string_item(self, entry: Entry, rec: StringRecord) -> QTreeWidgetItem:
        text = rec.original_text()
        item = QTreeWidgetItem([f"{rec.index}  {string_preview(text)}"])
        item.setData(0, STRING_ROLE, (id(entry), rec.index))
        # Selectable and nothing more: not dragged, not dropped on, not renamed.
        item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        where = f"{rec.start:X}–{rec.end - 1:X} ({rec.length} bytes)"
        item.setToolTip(0, f"{where}\n{text}")
        return item

    def string_of(self, item: QTreeWidgetItem | None) -> tuple[Entry, int] | None:
        """The block and string index a string row stands for, else ``None``."""
        data = item.data(0, STRING_ROLE) if item is not None else None
        if not isinstance(data, tuple):
            return None
        entry = self.workspace.entry_by_id(data[0])
        return None if entry is None else (entry, data[1])

    def _on_expanded(self, item: QTreeWidgetItem) -> None:
        entry = self.entry_of(item)
        if entry is None or entry.kind is not EntryKind.BLOCK:
            return
        self._expanded.add(id(entry))
        if entry.doc is None:
            self.strings_requested.emit(entry)
        self._update_item(entry)

    def _on_collapsed(self, item: QTreeWidgetItem) -> None:
        entry = self.entry_of(item)
        if entry is not None and entry.kind is EntryKind.BLOCK:
            self._expanded.discard(id(entry))
            # The rows go with it, so a block with thousands of strings costs
            # nothing while it is closed — and nothing at the next rebuild.
            self._update_item(entry)

    def _marker(self, entry: Entry) -> QIcon:
        """The row's icon: its kind's mark, or a warning when its file is gone."""
        if entry.missing:
            return self._icon(Glyph.QUESTION, WARNING_INK, "warning")
        spec = MARKERS.get(entry.kind)
        if spec is None:
            return QIcon()
        glyph, role = spec
        color = self.palette().color(QPalette.ColorGroup.Active, role)
        return self._icon(glyph, color, role.name)

    def _icon(self, glyph: Glyph, color: QColor, key: str) -> QIcon:
        """Baked once per glyph and color; dropped on a palette change."""
        icon = self._icons.get((glyph, key))
        if icon is None:
            icon = themed_icon(self, glyph, color, size=ICON_SIZE)
            self._icons[(glyph, key)] = icon
        return icon

    def _bake_icons(self) -> None:
        self._icons.clear()
        self.refresh_labels()

    # -- what a row says ------------------------------------------------

    def _update_item(self, entry: Entry) -> None:
        item = self._items.get(id(entry))
        if item is None:
            return
        blocked = self.tree.signalsBlocked()
        self.tree.blockSignals(True)
        try:
            self._dress(entry, item)
        finally:
            self.tree.blockSignals(blocked)

    def refresh_labels(self) -> None:
        if self._labels_held:
            return
        for entry in self.workspace.entries:
            self._update_item(entry)

    @contextmanager
    def labels_held(self) -> Iterator[None]:
        """Refresh the labels once, on the way out, rather than at every
        :meth:`refresh_labels` inside — for a pass that reads many blocks."""
        held = self._labels_held
        self._labels_held = True
        try:
            yield
        finally:
            self._labels_held = held
            self.refresh_labels()

    # -- selection ------------------------------------------------------

    def selected_entries(self) -> list[Entry]:
        out = []
        for item in self.tree.selectedItems():
            entry = self.entry_of(item)
            if entry is not None:
                out.append(entry)
        return out

    def _emit_selected(self, signal) -> None:
        entries = self.selected_entries()
        if entries:
            signal.emit(entries)

    def has_focus(self) -> bool:
        """Whether the panel owns the keyboard — what makes Ctrl+F the filter."""
        return self.filter.hasFocus() or self.tree.hasFocus()

    def focus_filter(self) -> None:
        """Put the cursor in the filter with its text selected, so a second
        search is typed over the first rather than appended to it."""
        self.filter.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.filter.selectAll()

    def _on_clicked(self, item, column) -> None:
        self._activate(item)

    def _activate(self, item: QTreeWidgetItem) -> None:
        """Show what a row stands for — what both a click on it and an arrow
        key onto it mean."""
        if len(self.tree.selectedItems()) > 1:
            return
        entry = self.entry_of(item)
        if entry is not None:
            self.entry_activated.emit(entry)
            return
        string = self.string_of(item)
        if string is not None:
            # Showing the string selects its row again, over the block's row
            # that making the block current selected.
            self.string_activated.emit(*string)

    def select_entry(self, entry: Entry) -> None:
        """Select ``entry``'s row alone — what a view that left one of its
        strings for the block itself asks for, since the current entry did
        not change and so did not select it."""
        self._on_current(entry)

    def select_string(self, entry: Entry, index: int) -> None:
        """Select the row of string ``index`` under its block alone, opening
        the block to show it — what showing a string from anywhere but a click
        on its row asks for: Back and Forward land on strings too."""
        item = self._items.get(id(entry))
        if item is None:
            return
        if not item.isExpanded():
            item.setExpanded(True)  # builds the rows
        for i in range(item.childCount()):
            child = item.child(i)
            data = child.data(0, STRING_ROLE)
            if isinstance(data, tuple) and data[1] == index:
                self._select_item(child)
                self.tree.scrollToItem(child)
                return

    def _select_item(self, item: QTreeWidgetItem) -> None:
        self.tree.blockSignals(True)
        try:
            self.tree.clearSelection()
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        finally:
            self.tree.blockSignals(False)

    def _on_double(self, item, column) -> None:
        entry = self.entry_of(item)
        if entry is None:
            return
        if entry.kind in (EntryKind.FILE, EntryKind.BLOCK):
            self.begin_rename(entry)
            return
        self.entry_double_clicked.emit(entry)

    def _on_menu(self, pos: QPoint) -> None:
        item = self.tree.itemAt(pos)
        entry = self.entry_of(item)
        if entry is None:
            # A string row's menu is its block's.
            string = self.string_of(item)
            entry = string[0] if string is not None else None
        self.context_menu_requested.emit(entry, self.tree.viewport().mapToGlobal(pos))

    def _on_current(self, entry: Entry | None) -> None:
        item = self._items.get(id(entry)) if entry is not None else None
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        if item is not None:
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)

    # -- renaming -------------------------------------------------------

    def _on_rename_pressed(self) -> None:
        """F2: rename the current row in place, whatever its kind."""
        entry = self.entry_of(self.tree.currentItem())
        if entry is not None:
            self.begin_rename(entry)

    def begin_rename(self, entry: Entry) -> None:
        """Open the inline editor on ``entry``'s row.

        A row opens under the basename of the file it points at, which rarely
        says what the user is looking at — a ROM is not named after the script
        inside it — so the label is free text and the path stays in the tooltip.
        """
        item = self._items.get(id(entry))
        if item is None:
            return
        self._editing = entry
        self.tree.blockSignals(True)
        try:
            item.setText(0, entry.name)  # edit the bare name, not the markers
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
        finally:
            self.tree.blockSignals(False)
        self.tree.editItem(item, 0)

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        # Only a commit of the live inline edit counts: every other setText
        # either arrives with signals blocked or lands here with no edit open.
        entry = self._editing
        if entry is None or column != 0 or self._items.get(id(entry)) is not item:
            return
        self._editing = None
        item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        name = item.text(0).strip()
        if name and name != entry.name:
            self.rename_committed.emit(entry, name)
        else:
            self._update_item(entry)  # empty or unchanged: put the label back

    def _on_editor_closed(self, editor, hint) -> None:
        # A cancelled edit (Escape, focus lost without a commit) never fires
        # itemChanged, so the label and the flag are restored here.
        entry, self._editing = self._editing, None
        if entry is not None:
            item = self._items.get(id(entry))
            if item is not None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            self._update_item(entry)

    # -- order ----------------------------------------------------------

    def siblings(self, entry: Entry) -> list[Entry]:
        """The rows ``entry`` shares a parent with, in the order on screen —
        one file's children, or one group's files."""
        item = self._items.get(id(entry))
        parent = item.parent() if item is not None else None
        if parent is None:
            return [entry]
        out = []
        for i in range(parent.childCount()):
            sibling = self.entry_of(parent.child(i))
            if sibling is not None:
                out.append(sibling)
        return out

    def move_target(self, entry: Entry, delta: int) -> Entry | None | bool:
        """The row a one-step move lands ``entry`` in front of.

        ``False`` when it cannot move — it is already at that end of its group.
        """
        group = self.siblings(entry)
        at = group.index(entry) + delta
        if at < 0 or at >= len(group):
            return False
        # Stepping down means landing in front of the row *after* the one being
        # passed, since this row comes out of the list before it goes back in.
        if delta > 0:
            return group[at + 1] if at + 1 < len(group) else None
        return group[at]

    def _on_move(self, delta: int) -> None:
        entries = self.selected_entries()
        if entries:
            self.move_requested.emit(entries, delta)

    def _on_dropped(self, key, before_key) -> None:
        entry = self.workspace.entry_by_id(key)
        before = self.workspace.entry_by_id(before_key) if before_key else None
        if entry is not None:
            self.reorder_requested.emit(entry, before)

    # -- filtering ------------------------------------------------------

    def _apply_filter(self, text: str) -> None:
        """Hide every row the words do not match, down to a block's strings.

        A matching row keeps its parents visible, and a block one of whose
        strings matches opens to show it. A block's stub is not a row that
        can match: it follows its block.
        """
        words = words_of(text)

        def matches(item: QTreeWidgetItem) -> bool:
            return matches_words(words, item.text(0))

        for group in self._groups.values():
            for i in range(group.childCount()):
                item = group.child(i)
                child_hit = False
                for j in range(item.childCount()):
                    c = item.child(j)
                    hit = matches(c)
                    string_hit = False
                    for k in range(c.childCount()):
                        s = c.child(k)
                        if self._is_stub(s):
                            s.setHidden(not hit)
                            continue
                        found = matches(s)
                        s.setHidden(not found)
                        string_hit = string_hit or found
                    c.setHidden(not (hit or string_hit))
                    if words and string_hit:
                        c.setExpanded(True)
                    child_hit = child_hit or not c.isHidden()
                item.setHidden(not (matches(item) or child_hit))
