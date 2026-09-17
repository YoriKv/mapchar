"""The base of the docks that show the workspace as a tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QHeaderView,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.ui.widgets import show_elided_tooltips

if TYPE_CHECKING:
    from collections.abc import Sequence

    from mapchar.project.workspace import Entry, Workspace


class WorkspaceTreePanel(QWidget):
    """A dock whose tree follows the workspace.

    The base owns the tree, the subscription that rebuilds it when entries come
    and go, and the item-to-entry lookup; a subclass fills :meth:`rebuild` and
    calls it once its own widgets exist. A row names its entry by ``id()`` in
    the tree item's ``UserRole``. A subclass whose tree has to answer for itself
    — drag reordering, keys of its own — names that tree's class in
    :attr:`tree_class`.
    """

    tree_class: type[QTreeWidget] = QTreeWidget

    entry_double_clicked = Signal(object)
    """A row was double-clicked: the entry it stands for."""

    def __init__(
        self,
        workspace: Workspace,
        parent: QWidget | None = None,
        *,
        on_dirty: bool = False,
    ):
        super().__init__(parent)
        self.workspace = workspace
        self.tree = self.tree_class()
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.addWidget(self.tree)
        self.tree.itemDoubleClicked.connect(self._on_double)
        workspace.on_rows_changed.append(self.rebuild)
        workspace.on_reset.append(self.rebuild)
        if on_dirty:
            workspace.on_dirty_changed.append(lambda e: self.rebuild())

    def set_columns(self, labels: Sequence[str]) -> None:
        """Name the tree's columns and size them: the first gives way as the
        dock narrows, and the rest keep the room their contents need."""
        self.tree.setHeaderLabels(list(labels))
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, len(labels)):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        show_elided_tooltips(self.tree)

    def rebuild(self) -> None:
        raise NotImplementedError

    def _on_double(self, item: QTreeWidgetItem, column: int) -> None:
        entry = self.entry_of(item)
        if entry is not None:
            self.entry_double_clicked.emit(entry)

    def entry_of(self, item: QTreeWidgetItem | None) -> Entry | None:
        """The entry a tree item stands for."""
        if item is None:
            return None
        return self.workspace.entry_by_id(item.data(0, Qt.ItemDataRole.UserRole))
