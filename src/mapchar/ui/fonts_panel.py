"""The Fonts dock: registered glyph sheets and which blocks use them."""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QTreeWidgetItem, QWidget

from mapchar.project.workspace import EntryKind, Workspace
from mapchar.ui.panel import WorkspaceTreePanel


class FontsPanel(WorkspaceTreePanel):
    edit_requested = Signal(object)
    """Double-clicked a font entry: open the Preview window's Font tab."""

    def __init__(self, workspace: Workspace, parent: QWidget | None = None):
        super().__init__(workspace, parent, on_dirty=True)
        self.set_columns(["Font", "Cell", "Glyphs"])
        self.entry_double_clicked.connect(self.edit_requested)
        self.rebuild()

    def rebuild(self) -> None:
        self.tree.clear()
        for i, entry in enumerate(self.workspace.fonts()):
            f = entry.font
            cell = f"{f.cell_width}×{f.cell_height}" if f else ""
            glyphs = str(len(f.chars) + len(f.glyphs)) if f else ""
            item = QTreeWidgetItem([entry.name, cell, glyphs])
            item.setData(0, Qt.ItemDataRole.UserRole, id(entry))
            item.setToolTip(0, f"{entry.path or entry.name}\ndouble-click to edit")
            users = [
                b.name
                for b in self.workspace.of_kind(EntryKind.BLOCK)
                if b.box is not None and b.box.font_index == i
            ]
            for name in users:
                item.addChild(QTreeWidgetItem([f"used by {name}", "", ""]))
            self.tree.addTopLevelItem(item)
            item.setExpanded(True)
