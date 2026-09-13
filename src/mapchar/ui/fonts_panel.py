"""The Fonts dock: registered glyph sheets and which blocks use them."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from mapchar.project.workspace import EntryKind, Workspace


class FontsPanel(QWidget):
    edit_requested = Signal(object)
    """Double-clicked a font entry: open the Preview window's Font tab."""

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(parent)
        self.workspace = workspace
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Font", "Cell", "Glyphs"])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(self._on_double)
        workspace.on_added.append(lambda e: self.rebuild())
        workspace.on_removed.append(lambda e: self.rebuild())
        workspace.on_reset.append(self.rebuild)
        workspace.on_dirty_changed.append(lambda e: self.rebuild())
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        fonts = [e for e in self.workspace.entries if e.kind is EntryKind.FONT]
        for i, entry in enumerate(fonts):
            f = entry.font
            cell = f"{f.cell_width}×{f.cell_height}" if f else ""
            glyphs = str(len(f.chars) + len(f.glyphs)) if f else ""
            item = QTreeWidgetItem([entry.name, cell, glyphs])
            item.setData(0, Qt.ItemDataRole.UserRole, id(entry))
            item.setToolTip(0, entry.path or "")
            users = [
                b.name
                for b in self.workspace.entries
                if b.kind is EntryKind.BLOCK
                and b.box is not None
                and b.box.font_index == i
            ]
            for name in users:
                item.addChild(QTreeWidgetItem([f"used by {name}", "", ""]))
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)
        self.tree.resizeColumnToContents(0)

    def _on_double(self, item: QTreeWidgetItem, column: int) -> None:
        key = item.data(0, Qt.ItemDataRole.UserRole)
        for entry in self.workspace.entries:
            if id(entry) == key:
                self.edit_requested.emit(entry)
                return
