"""The Tables dock: registered table files and the tables inside each."""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from mapchar.project.workspace import Entry, EntryKind, Workspace
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import glyph_icon


class TablesPanel(QWidget):
    table_chosen = Signal(str)
    """Double-clicked a table id: make it the start table."""
    edit_requested = Signal(object)
    """Double-clicked a table file entry."""

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(parent)
        self.workspace = workspace
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Table", "Entries"])
        self.tree.setRootIsDecorated(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self._start_id: str | None = None
        workspace.on_added.append(lambda e: self.rebuild())
        workspace.on_removed.append(lambda e: self.rebuild())
        workspace.on_reset.append(self.rebuild)
        self.rebuild()

    def set_start_table(self, table_id: str | None) -> None:
        self._start_id = table_id
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        for entry in self.workspace.entries:
            if entry.kind is not EntryKind.TABLE:
                continue
            top = QTreeWidgetItem([entry.name, entry.dialect or "native"])
            top.setData(0, Qt.ItemDataRole.UserRole, ("entry", id(entry)))
            self.tree.addTopLevelItem(top)
            for table in entry.tables:
                item = QTreeWidgetItem([f"@{table.id}", str(len(table.entries))])
                item.setData(0, Qt.ItemDataRole.UserRole, ("table", table.id))
                if table.id == self._start_id:
                    item.setIcon(0, self._start_icon())
                    item.setToolTip(0, "The start table")
                top.addChild(item)
            top.setExpanded(True)
        self.tree.resizeColumnToContents(0)

    def _start_icon(self):
        """The start table's mark, in the accent; baked, so a palette change
        rebuilds the tree."""
        color = self.palette().color(
            QPalette.ColorGroup.Active, QPalette.ColorRole.Highlight
        )
        return glyph_icon(Glyph.TARGET, color, ratio=self.devicePixelRatioF())

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() is QEvent.Type.PaletteChange:
            self.rebuild()

    def _on_double(self, item: QTreeWidgetItem, column: int) -> None:
        kind, value = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "table":
            self.table_chosen.emit(value)
        else:
            for entry in self.workspace.entries:
                if id(entry) == value:
                    self.edit_requested.emit(entry)
                    return

    def entry_for_table(self, table_id: str) -> Entry | None:
        for entry in self.workspace.entries:
            if entry.kind is EntryKind.TABLE and any(
                t.id == table_id for t in entry.tables
            ):
                return entry
        return None
