"""The Tables dock: registered table files and the tables inside each."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QHeaderView, QTreeWidgetItem, QWidget

from mapchar.project.workspace import Workspace
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.panel import WorkspaceTreePanel
from mapchar.ui.widgets import show_elided_tooltips


class TablesPanel(ThemedIcons, WorkspaceTreePanel):
    table_chosen = Signal(str)
    """Double-clicked a table id: make it the start table."""
    edit_requested = Signal(object)
    """Double-clicked a table file entry."""

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(workspace, parent)
        self.tree.setHeaderLabels(["Table", "Entries"])
        self.tree.setRootIsDecorated(True)
        # The name gives way when the dock narrows; the counts keep their room.
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        show_elided_tooltips(self.tree)
        self.tree.itemDoubleClicked.connect(self._on_double)
        self._start_id: str | None = None
        self.rebuild()

    def set_start_table(self, table_id: str | None) -> None:
        self._start_id = table_id
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        for entry in self.workspace.table_entries():
            # The file row counts every entry of its tables, so the column says
            # one thing all the way down; the dialect is the tooltip's.
            total = sum(len(table.entries) for table in entry.tables)
            top = QTreeWidgetItem([entry.name, str(total)])
            top.setData(0, Qt.ItemDataRole.UserRole, ("entry", id(entry)))
            top.setToolTip(
                0,
                f"{entry.path or entry.name}\ndialect: {entry.dialect or 'native'}"
                "\ndouble-click to edit",
            )
            self.tree.addTopLevelItem(top)
            for table in entry.tables:
                item = QTreeWidgetItem([f"@{table.id}", str(len(table.entries))])
                item.setData(0, Qt.ItemDataRole.UserRole, ("table", table.id))
                if table.id == self._start_id:
                    item.setIcon(0, self._start_icon())
                    item.setToolTip(0, "The start table")
                else:
                    item.setToolTip(0, "Double-click to make it the start table")
                top.addChild(item)
            top.setExpanded(True)

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
