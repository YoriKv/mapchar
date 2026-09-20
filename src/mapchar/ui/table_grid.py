"""The grid behind the Table Editor: its rows, and what draws them.

A table's entries are the model's, not the view's: the sort, the filter and
the spelling of a cell are all done over the entries themselves, so a charset
of tens of thousands of rows costs its entries and no widget. The window
above it (:mod:`mapchar.ui.table_editor`) reaches it through
:meth:`_EntryModel.set_rows`, :meth:`~_EntryModel.set_filter`,
:meth:`~_EntryModel.bits_at`, :meth:`~_EntryModel.row_of` and the
:attr:`~_EntryModel.edited` signal.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QAbstractItemView, QApplication, QTableView

from mapchar.core.bits import format_key
from mapchar.core.table import TokenKind
from mapchar.core.textmatch import matches_words, words_of
from mapchar.ui.table_entry_form import KIND_NAMES, describe

if TYPE_CHECKING:
    from PySide6.QtWidgets import QWidget

    from mapchar.core.table import Entry as TableEntry

KEY, KIND, TEXT, DETAILS, WEIGHT, COMMENT = range(6)
"""The grid's columns."""
HEADERS = ("Key", "Kind", "Text", "Details", "Weight", "Comment")
ORIGIN_ROLE = Qt.ItemDataRole.UserRole + 1
"""On a Key cell: the id of the table an inherited row comes from, else ``None``."""
NO_PARENT = QModelIndex()
"""The invalid index a flat model's counts are asked for."""


class _Row:
    """One grid row: an entry, and the table it is own to when inherited.

    The cells are spelled on first use and kept, so a table on a charset costs
    its entries and nothing else until a row is drawn, sorted on or filtered.
    """

    __slots__ = ("cells", "entry", "origin")

    def __init__(self, entry: TableEntry, origin: str | None = None):
        self.entry = entry
        self.origin = origin
        self.cells: tuple[str, ...] | None = None


class _EntryModel(QAbstractTableModel):
    """The entries behind the grid: the table's own, then — dimmed, naming the
    table they come from — the ones its includes give it.

    Sorting and filtering are the model's, over the entries themselves: the
    default key order is the order they arrive in, an empty filter keeps every
    row, and neither spells a cell the view has not asked to draw.
    """

    edited = Signal(str, int, str)
    """A cell typed over: the row's key bits, the column, and what was typed."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: list[_Row] = []
        """Every row, in the order the chosen column sorts them."""
        self._shown: list[_Row] = []
        """Those of :attr:`_rows` the filter keeps: what the view sees."""
        self._at: dict[str, int] = {}
        self._inherited: dict[str, tuple[TableEntry, str]] = {}
        self._words: list[str] = []
        self._column = KEY
        self._order = Qt.SortOrder.AscendingOrder
        self._dim = QColor()

    # -- what the view asks for ------------------------------------------------

    def rowCount(self, parent: QModelIndex = NO_PARENT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._shown)

    def columnCount(self, parent: QModelIndex = NO_PARENT) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(HEADERS)

    def headerData(  # noqa: N802 - Qt override
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ):
        if (
            role == Qt.ItemDataRole.DisplayRole
            and orientation == Qt.Orientation.Horizontal
        ):
            return HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None
        row = self._shown[index.row()]
        column = index.column()
        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.EditRole):
            return self._cells(row)[column]
        if row.origin is not None:
            if role == Qt.ItemDataRole.ForegroundRole:
                return self._dim
            if role == Qt.ItemDataRole.ToolTipRole:
                return f"From @{row.origin}; editing it gives this table its own entry"
        if column == KEY:
            if role == Qt.ItemDataRole.UserRole:
                return row.entry.bits
            if role == ORIGIN_ROLE:
                return row.origin
        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        if not index.isValid():
            return Qt.ItemFlag.NoItemFlags
        flags = Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable
        entry = self._shown[index.row()].entry
        if index.column() in (TEXT, COMMENT) and entry.kind is not TokenKind.RETURN:
            flags |= Qt.ItemFlag.ItemIsEditable
        return flags

    def setData(  # noqa: N802 - Qt override
        self, index: QModelIndex, value, role: int = Qt.ItemDataRole.EditRole
    ) -> bool:
        """Hand what was typed to the editor, which commits it as an undo step
        and fills the grid again — the row is not changed here."""
        if role != Qt.ItemDataRole.EditRole or not index.isValid():
            return False
        if not self.flags(index) & Qt.ItemFlag.ItemIsEditable:
            return False
        row = self._shown[index.row()]
        self.edited.emit(row.entry.bits, index.column(), str(value))
        return True

    def sort(  # noqa: A003 - Qt override
        self, column: int, order: Qt.SortOrder = Qt.SortOrder.AscendingOrder
    ) -> None:
        self._column, self._order = column, order
        if not self._rows:
            return
        self.layoutAboutToBeChanged.emit()
        kept = [
            (index, self._shown[index.row()])
            for index in self.persistentIndexList()
            if index.isValid()
        ]
        self._sort()
        self._reshow()
        for index, row in kept:
            at = self._at.get(row.entry.bits)
            self.changePersistentIndex(
                index,
                QModelIndex() if at is None else self.index(at, index.column()),
            )
        self.layoutChanged.emit()

    # -- what the editor puts in -----------------------------------------------

    def set_rows(
        self, rows: list[_Row], inherited: dict[str, tuple[TableEntry, str]]
    ) -> None:
        """Replace every row, keeping the sort and the filter in force."""
        self.beginResetModel()
        self._rows = rows
        self._inherited = inherited
        self._dim = QApplication.palette().color(
            QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text
        )
        self._sort()
        self._reshow()
        self.endResetModel()

    def set_filter(self, text: str) -> None:
        """Keep only the rows whose key, text or comment matches ``text``."""
        words = words_of(text)
        if words == self._words:
            return
        self.beginResetModel()
        self._words = words
        self._reshow()
        self.endResetModel()

    def bits_at(self, row: int) -> str:
        return self._shown[row].entry.bits

    def row_of(self, bits: str) -> int | None:
        return self._at.get(bits)

    # -- the rows themselves ---------------------------------------------------

    def _sort(self) -> None:
        """Order every row by the chosen column. The key order is the entries'
        own — by width, then bits — and costs no cell."""
        column = self._column

        def key(row: _Row):
            if column == KEY:
                return len(row.entry.bits), row.entry.bits
            if column == WEIGHT:
                return row.entry.weight
            return self._cells(row)[column]

        self._rows.sort(key=key, reverse=self._order == Qt.SortOrder.DescendingOrder)

    def _reshow(self) -> None:
        if self._words:
            self._shown = [row for row in self._rows if self._matches(row)]
        else:
            self._shown = list(self._rows)
        self._at = {row.entry.bits: at for at, row in enumerate(self._shown)}

    def _matches(self, row: _Row) -> bool:
        cells = self._cells(row)
        return matches_words(self._words, cells[KEY], cells[TEXT], cells[COMMENT])

    def _cells(self, row: _Row) -> tuple[str, ...]:
        cells = row.cells
        if cells is None:
            cells = row.cells = self._spell(row)
        return cells

    def _spell(self, row: _Row) -> tuple[str, ...]:
        """A row's six cells: an entry of the table's own, or — ``origin``
        naming the table it is own to — one an include gives it."""
        entry, origin = row.entry, row.origin
        details = describe(entry)
        hidden = self._inherited.get(entry.bits) if origin is None else None
        if hidden is not None and entry.kind is TokenKind.TEXT and entry.text == "":
            details = f"removes @{hidden[1]}'s entry"
        elif origin is not None:
            details = f"from @{origin}" + (f" · {details}" if details else "")
        return (
            format_key(entry.bits),
            KIND_NAMES[entry.kind],
            # A code reads as the dump shows it; the form holds the bare label.
            f"[{entry.text}]" if entry.kind is TokenKind.CODE else entry.text,
            details,
            str(entry.weight),
            entry.comment.replace("\n", " ⏎ "),
        )


class _EntryGrid(QTableView):
    """The entries grid: Del removes the selected rows when no cell is open."""

    delete_pressed = Signal()

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if (
            event.key() == Qt.Key.Key_Delete
            and self.state() != QAbstractItemView.State.EditingState
        ):
            self.delete_pressed.emit()
            return
        super().keyPressEvent(event)


__all__ = [
    "COMMENT",
    "DETAILS",
    "HEADERS",
    "KEY",
    "KIND",
    "NO_PARENT",
    "ORIGIN_ROLE",
    "TEXT",
    "WEIGHT",
]
