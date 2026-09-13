"""The base of the docks that show the workspace as a tree."""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

if TYPE_CHECKING:
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
        workspace.on_added.append(lambda e: self.rebuild())
        workspace.on_removed.append(lambda e: self.rebuild())
        workspace.on_reset.append(self.rebuild)
        if on_dirty:
            workspace.on_dirty_changed.append(lambda e: self.rebuild())

    def rebuild(self) -> None:
        raise NotImplementedError

    def entry_of(self, item: QTreeWidgetItem | None) -> Entry | None:
        """The entry a tree item stands for."""
        if item is None:
            return None
        return self.workspace.entry_by_id(item.data(0, Qt.ItemDataRole.UserRole))
