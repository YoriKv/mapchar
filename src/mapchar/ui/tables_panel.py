"""The Tables dock: registered table files and the tables inside each."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QTreeWidgetItem, QWidget

from mapchar.project.workspace import Workspace
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.panel import WorkspaceTreePanel


class TablesPanel(ThemedIcons, WorkspaceTreePanel):
    table_chosen = Signal(str)
    """Double-clicked a table id: make it the start table."""
    edit_requested = Signal(object)
    """Double-clicked a table file entry."""

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(workspace, parent)
        self.tree.setHeaderLabels(["Table", "Entries"])
        self.tree.setRootIsDecorated(True)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self._start_id: str | None = None
        self.rebuild()

    def set_start_table(self, table_id: str | None) -> None:
        self._start_id = table_id
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        for entry in self.workspace.table_entries():
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
        """The start table's mark, in the accent."""
        return themed_icon(
            self,
            Glyph.TARGET,
            QPalette.ColorRole.Highlight,
            group=QPalette.ColorGroup.Active,
        )

    def _bake_icons(self) -> None:
        self.rebuild()

    def _on_double(self, item: QTreeWidgetItem, column: int) -> None:
        kind, value = item.data(0, Qt.ItemDataRole.UserRole)
        if kind == "table":
            self.table_chosen.emit(value)
            return
        entry = self.workspace.entry_by_id(value)
        if entry is not None:
            self.edit_requested.emit(entry)
