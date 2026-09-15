"""The Tables dock: the registered tables, one per table file."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QIcon, QPalette, QPixmap
from PySide6.QtWidgets import QStyle, QTreeWidgetItem, QWidget

from mapchar.project.workspace import Workspace
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.panel import WorkspaceTreePanel


class TablesPanel(ThemedIcons, WorkspaceTreePanel):
    table_chosen = Signal(str)
    """Double-clicked a table: make it the start table."""

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(workspace, parent)
        self.set_columns(["Table", "Entries"])
        self.tree.setRootIsDecorated(False)
        self.entry_double_clicked.connect(self._on_chosen)
        self._start_id: str | None = None
        self.rebuild()

    def set_start_table(self, table_id: str | None) -> None:
        self._start_id = table_id
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        for entry in self.workspace.table_entries():
            table = entry.table
            where = f"{entry.path or entry.name}\ndialect: {entry.dialect or 'native'}"
            if table is None:
                item = QTreeWidgetItem([entry.name, ""])
                item.setToolTip(0, f"{where}\nnot loaded")
            else:
                item = QTreeWidgetItem([f"@{table.id}", str(len(table.entries))])
                if table.id == self._start_id:
                    item.setIcon(0, self._start_icon())
                    item.setToolTip(0, f"{where}\nthe start table")
                else:
                    # A blank of the mark's size, so every name starts in line.
                    item.setIcon(0, self._blank_icon())
                    item.setToolTip(
                        0, f"{where}\ndouble-click to make it the start table"
                    )
            item.setData(0, Qt.ItemDataRole.UserRole, id(entry))
            self.tree.addTopLevelItem(item)

    def _start_icon(self):
        """The start table's mark, in the accent."""
        return themed_icon(
            self,
            Glyph.TARGET,
            QPalette.ColorRole.Highlight,
            group=QPalette.ColorGroup.Active,
        )

    def _blank_icon(self) -> QIcon:
        size = self.tree.iconSize()
        if not size.isValid():
            extent = self.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)
            size = QSize(extent, extent)
        blank = QPixmap(size)
        blank.fill(Qt.GlobalColor.transparent)
        return QIcon(blank)

    def _bake_icons(self) -> None:
        self.rebuild()

    def _on_chosen(self, entry) -> None:
        if entry.table is not None:
            self.table_chosen.emit(entry.table.id)
