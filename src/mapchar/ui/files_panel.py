"""The Files panel: ROMs with their blocks and bookmarks, tables, fonts."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QPalette
from PySide6.QtWidgets import (
    QAbstractItemView,
    QLineEdit,
    QTreeWidgetItem,
    QWidget,
)

from mapchar.project.workspace import Entry, EntryKind, Workspace
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.panel import WorkspaceTreePanel
from mapchar.ui.theme import WARNING_INK

GROUPS = {EntryKind.FILE: "ROMs", EntryKind.TABLE: "Tables", EntryKind.FONT: "Fonts"}
# The row markers: a glyph in a palette role. ROMs and fonts sit under their
# own group headings and carry no mark; a bookmark wears the accent.
MARKERS: dict[EntryKind, tuple[Glyph, QPalette.ColorRole]] = {
    EntryKind.BLOCK: (Glyph.GRID_ROWS, QPalette.ColorRole.Text),
    EntryKind.BOOKMARK: (Glyph.FLAG, QPalette.ColorRole.Highlight),
    EntryKind.TABLE: (Glyph.GRID, QPalette.ColorRole.Text),
}
ICON_SIZE = QSize(13, 16)


class FilesPanel(ThemedIcons, WorkspaceTreePanel):
    entry_activated = Signal(object)
    """Clicked: show this entry."""
    entry_double_clicked = Signal(object)
    context_menu_requested = Signal(object, QPoint)
    remove_requested = Signal(list)

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(workspace, parent)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter…")
        self.filter.setClearButtonEnabled(True)
        self.box.insertWidget(0, self.filter)
        self.tree.setHeaderHidden(True)
        self.tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.setIconSize(ICON_SIZE)
        self._icons: dict[tuple[Glyph, str], QIcon] = {}
        self._items: dict[int, QTreeWidgetItem] = {}
        self._groups: dict[EntryKind, QTreeWidgetItem] = {}
        self.filter.textChanged.connect(self._apply_filter)
        self.tree.itemClicked.connect(self._on_clicked)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self.tree.customContextMenuRequested.connect(self._on_menu)
        workspace.on_current_changed.append(self._on_current)
        workspace.on_dirty_changed.append(lambda e: self._update_item(e))
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        self._items.clear()
        self._groups.clear()
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
        self._apply_filter(self.filter.text())
        self._on_current(self.workspace.current)

    def _make_item(self, entry: Entry) -> QTreeWidgetItem:
        item = QTreeWidgetItem([self._label(entry)])
        item.setData(0, Qt.ItemDataRole.UserRole, id(entry))
        item.setToolTip(0, self._tooltip(entry))
        item.setIcon(0, self._marker(entry))
        self._items[id(entry)] = item
        return item

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

    @staticmethod
    def _label(entry: Entry) -> str:
        mark = " ●" if entry.dirty else ""
        missing = ""
        extra = ""
        if entry.kind is EntryKind.BLOCK and entry.doc is not None:
            extra = f"  ({len(entry.doc.strings)})"
        if entry.kind is EntryKind.FILE and entry.extra_paths:
            extra = f"  [{1 + len(entry.extra_paths)} files]"
        return f"{entry.name}{extra}{mark}{missing}"

    @staticmethod
    def _tooltip(entry: Entry) -> str:
        lines = [entry.path or "(in memory)"]
        if entry.kind is EntryKind.FILE:
            lines.append(f"container: {entry.container_id}")
        if entry.kind is EntryKind.BLOCK and entry.config is not None:
            lines.append(f"source: {entry.config.source}")
        if entry.kind is EntryKind.TABLE:
            lines.append(f"dialect: {entry.dialect or 'native'}")
            lines.append("tables: " + ", ".join(t.id for t in entry.tables))
        return "\n".join(lines)

    def _update_item(self, entry: Entry) -> None:
        item = self._items.get(id(entry))
        if item is not None:
            item.setText(0, self._label(entry))
            item.setToolTip(0, self._tooltip(entry))
            item.setIcon(0, self._marker(entry))

    def refresh_labels(self) -> None:
        for entry in self.workspace.entries:
            self._update_item(entry)

    def selected_entries(self) -> list[Entry]:
        out = []
        for item in self.tree.selectedItems():
            entry = self.entry_of(item)
            if entry is not None:
                out.append(entry)
        return out

    def _on_clicked(self, item, column) -> None:
        entry = self.entry_of(item)
        if entry is not None and len(self.tree.selectedItems()) <= 1:
            self.entry_activated.emit(entry)

    def _on_double(self, item, column) -> None:
        entry = self.entry_of(item)
        if entry is not None:
            self.entry_double_clicked.emit(entry)

    def _on_menu(self, pos: QPoint) -> None:
        entry = self.entry_of(self.tree.itemAt(pos))
        self.context_menu_requested.emit(entry, self.tree.viewport().mapToGlobal(pos))

    def _on_current(self, entry: Entry | None) -> None:
        item = self._items.get(id(entry)) if entry is not None else None
        self.tree.blockSignals(True)
        self.tree.clearSelection()
        if item is not None:
            item.setSelected(True)
            self.tree.setCurrentItem(item)
        self.tree.blockSignals(False)

    def _apply_filter(self, text: str) -> None:
        words = text.lower().split()

        def matches(item: QTreeWidgetItem) -> bool:
            label = item.text(0).lower()
            return all(w in label for w in words)

        for group in self._groups.values():
            for i in range(group.childCount()):
                item = group.child(i)
                child_hit = False
                for j in range(item.childCount()):
                    c = item.child(j)
                    hit = matches(c)
                    c.setHidden(bool(words) and not hit)
                    child_hit = child_hit or hit
                item.setHidden(bool(words) and not (matches(item) or child_hit))

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Delete:
            entries = self.selected_entries()
            if entries:
                self.remove_requested.emit(entries)
                return
        super().keyPressEvent(event)
