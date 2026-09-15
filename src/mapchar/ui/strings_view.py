"""The Strings view: a block's strings beside their translations.

Presentation only: the window hands it rows and takes back edits. The
Translation cell is a multi-line editor with code completion on ``[``;
Ctrl+Return (or Return) commits, Shift+Return inserts the block's newline
code, Esc cancels. Columns hide and reorder from the header's context menu.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QPoint, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QCompleter,
    QHBoxLayout,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.text import fold
from mapchar.ui import theme
from mapchar.ui.widgets import (
    FlowLayout,
    install_column_menu,
    mono_font,
    show_elided_tooltips,
)

(
    COL_INDEX,
    COL_ADDRESS,
    COL_POINTERS,
    COL_ORIGINAL,
    COL_TRANSLATION,
    COL_BYTES,
    COL_STATUS,
    COL_NOTES,
) = range(8)
HEADERS = [
    "#",
    "Address",
    "Pointers",
    "Original",
    "Translation",
    "Bytes",
    "Status",
    "Notes",
]
STATUS_FILTERS = [
    "all",
    "untouched",
    "edited",
    "review",
    "too long",
    "invalid",
    "overflows box",
]


@dataclass(frozen=True)
class CodeInfo:
    """One code of the table set, as the editor offers it."""

    label: str
    operands: str = ""
    """The operand shapes, as the table spells them (``u8``, ``2``…)."""
    uses: int = 0
    """How often the block's strings hold it, for the Insert code buttons."""

    @property
    def completion(self) -> str:
        """What the completion popup lists: the label with its operand shapes."""
        return f"[{self.label}{' ' + self.operands if self.operands else ''}]"

    @property
    def insertion(self) -> str:
        """What typing it inserts: a code with operands is left open to type in."""
        return f"[{self.label} " if self.operands else f"[{self.label}]"


@dataclass
class RowData:
    index: int
    address: int
    original: str
    translation: str | None
    used: int
    room: int
    status: str
    notes: str
    problem: str = ""
    pointers: str = ""


class CodeEditor(QPlainTextEdit):
    """The translation editor: completes ``[labels]``, commits on Ctrl+Return.

    Plain Return commits too — a cell editor's Return belongs to the cell —
    and Shift+Return writes the block's newline code.
    """

    commit = Signal()
    cancel = Signal()

    def __init__(
        self,
        codes: list[CodeInfo],
        newline_code: str = "[line]",
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setFont(mono_font())
        self.setTabChangesFocus(True)
        self.newline_code = newline_code
        """What Shift+Return writes: the block's newline code."""
        self._insertions = {c.completion: c.insertion for c in codes}
        self.completer = QCompleter(
            QStringListModel([c.completion for c in codes]), self
        )
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self._insert_completion)

    def _insert_completion(self, chosen: str) -> None:
        text = self._insertions.get(chosen, chosen)
        cursor = self.textCursor()
        start = self._code_start(cursor)
        if start is not None:
            cursor.setPosition(start)
            cursor.setPosition(
                self.textCursor().position(), QTextCursor.MoveMode.KeepAnchor
            )
        cursor.insertText(text)
        self.setTextCursor(cursor)

    def _code_start(self, cursor: QTextCursor) -> int | None:
        """The ``[`` that opens the code the caret is inside, if any."""
        text = self.toPlainText()[: cursor.position()]
        start = text.rfind("[")
        if start < 0 or "]" in text[start:] or (start > 0 and text[start - 1] == "\\"):
            return None
        return start

    def insert_code(self, code: str) -> None:
        self.textCursor().insertText(code)

    def keyPressEvent(self, event) -> None:
        popup = self.completer.popup()
        if popup.isVisible() and event.key() in (
            Qt.Key.Key_Return,
            Qt.Key.Key_Enter,
            Qt.Key.Key_Tab,
            Qt.Key.Key_Escape,
        ):
            event.ignore()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                # A line break is a code in the ROM, never a literal newline:
                # the script grammar drops those on the way back in.
                self.insertPlainText(self.newline_code)
            else:
                self.commit.emit()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.cancel.emit()
            return
        super().keyPressEvent(event)
        start = self._code_start(self.textCursor())
        if start is None:
            popup.hide()
            return
        prefix = self.toPlainText()[start : self.textCursor().position()]
        self.completer.setCompletionPrefix(prefix)
        if self.completer.completionCount() == 0:
            popup.hide()
            return
        rect = self.cursorRect()
        rect.setWidth(
            popup.sizeHintForColumn(0) + popup.verticalScrollBar().sizeHint().width()
        )
        self.completer.complete(rect)


class TranslationDelegate(QStyledItemDelegate):
    def __init__(self, view: StringsView):
        super().__init__(view)
        self.view = view
        self.codes: list[CodeInfo] = []
        self.newline_code = "[line]"
        self._editor: CodeEditor | None = None

    def createEditor(self, parent, option, index):
        editor = CodeEditor(self.codes, self.newline_code, parent)
        editor.commit.connect(lambda: self._finish(editor, True))
        editor.cancel.connect(lambda: self._finish(editor, False))
        editor.textChanged.connect(
            lambda: self.view.draft_changed.emit(editor.toPlainText())
        )
        self._editor = editor
        return editor

    def _finish(self, editor: CodeEditor, commit: bool) -> None:
        if commit:
            self.commitData.emit(editor)
        self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
        self._editor = None

    def setEditorData(self, editor, index) -> None:
        editor.setPlainText(index.data(Qt.ItemDataRole.EditRole) or "")
        editor.moveCursor(QTextCursor.MoveOperation.End)

    def setModelData(self, editor, model, index) -> None:
        model.setData(index, editor.toPlainText(), Qt.ItemDataRole.EditRole)

    def current_editor(self) -> CodeEditor | None:
        return self._editor


_LOCKED_FLAGS = QTableWidgetItem().flags() & ~Qt.ItemFlag.ItemIsEditable
"""A cell that cannot be edited in place; worked out once, not per cell."""


class StringsView(QWidget):
    translation_edited = Signal(int, str)
    notes_edited = Signal(int, str)
    row_selected = Signal(int)
    draft_changed = Signal(str)
    revert_requested = Signal(list)
    review_toggled = Signal(list)
    context_menu_requested = Signal(list, QPoint)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: list[RowData] = []
        self._by_index: dict[int, RowData] = {}
        self._filling = False
        top = QHBoxLayout()
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("Filter original, translation and notes…")
        self.filter.setClearButtonEnabled(True)
        self.status_filter = QComboBox()
        self.status_filter.addItems(STATUS_FILTERS)
        top.addWidget(self.filter, 1)
        top.addWidget(self.status_filter)
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setWordWrap(False)
        self.table.verticalHeader().hide()
        # Original and Translation are cut short to one line; hovering a cell
        # reads the whole string.
        show_elided_tooltips(self.table)
        header = self.table.horizontalHeader()
        # Whichever column ends the row takes the width left over, so the table
        # never stops short of its right edge.
        header.setStretchLastSection(True)
        header.setSectionsMovable(True)
        # Translation never goes away, being the one column the view is for.
        self.column_menu = install_column_menu(self.table, HEADERS, COL_TRANSLATION)
        self.delegate = TranslationDelegate(self)
        self.table.setItemDelegateForColumn(COL_TRANSLATION, self.delegate)
        # The code buttons wrap onto more rows rather than setting the window's
        # minimum width: a block can use two dozen codes.
        self.codes = QWidget()
        self.codes_layout = FlowLayout(self.codes)
        self.codes_layout.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.table, 1)
        layout.addWidget(self.codes)
        self.filter.textChanged.connect(self._apply_filter)
        self.status_filter.currentIndexChanged.connect(self._apply_filter)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.customContextMenuRequested.connect(self._on_menu)

    # --- rows ------------------------------------------------------------

    def set_codes(self, codes: list[CodeInfo]) -> None:
        """The table set's codes, and buttons for the ones this block uses most."""
        self.delegate.codes = list(codes)
        while self.codes_layout.count():
            item = self.codes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        most_used = sorted(codes, key=lambda c: (-c.uses, c.label))[:24]
        for code in most_used:
            button = QPushButton(code.completion)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.setToolTip(
                f"{code.uses} use(s) in this block" if code.uses else "unused here"
            )
            button.clicked.connect(
                lambda _=False, text=code.insertion: self._insert_code(text)
            )
            self.codes_layout.addWidget(button)

    def set_newline_code(self, code: str) -> None:
        """What Shift+Return writes in the Translation cell."""
        self.delegate.newline_code = code

    def _insert_code(self, code: str) -> None:
        editor = self.delegate.current_editor()
        if editor is not None:
            editor.insert_code(code)
            return
        row = self.table.currentRow()
        if row < 0:
            return
        data = self._row_data(row)
        if data is None:
            return
        current = data.translation if data.translation is not None else data.original
        self.translation_edited.emit(data.index, current + code)

    def set_rows(self, rows: list[RowData], keep_selection: bool = True) -> None:
        selected = self.selected_indices() if keep_selection else []
        self._rows = rows
        self._by_index = {d.index: d for d in rows}
        self._filling = True
        # Signals off too: every cell set is an itemChanged the fill ignores.
        was = self.table.blockSignals(True)
        try:
            self.table.setRowCount(len(rows))
            for r, data in enumerate(rows):
                self._fill_row(r, data)
        finally:
            self.table.blockSignals(was)
        self._filling = False
        self.table.resizeColumnToContents(COL_INDEX)
        self.table.resizeColumnToContents(COL_ADDRESS)
        self.table.resizeColumnToContents(COL_POINTERS)
        self.table.resizeColumnToContents(COL_BYTES)
        self.table.resizeColumnToContents(COL_STATUS)
        self.table.setColumnWidth(
            COL_ORIGINAL, max(self.table.columnWidth(COL_ORIGINAL), 260)
        )
        self.table.setColumnWidth(
            COL_TRANSLATION, max(self.table.columnWidth(COL_TRANSLATION), 260)
        )
        self._apply_filter()
        if selected:
            self.select_index(selected[0])

    def update_row(self, data: RowData) -> None:
        for r, existing in enumerate(self._rows):
            if existing.index == data.index:
                self._rows[r] = data
                self._by_index[data.index] = data
                self._filling = True
                self._fill_row(r, data)
                self._filling = False
                return

    def _fill_row(self, r: int, data: RowData) -> None:
        def item(text: str, editable: bool = False) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            if not editable:
                it.setFlags(_LOCKED_FLAGS)
            return it

        self.table.setItem(r, COL_INDEX, item(str(data.index)))
        self.table.setItem(r, COL_ADDRESS, item(f"{data.address:X}"))
        self.table.setItem(r, COL_POINTERS, item(data.pointers))
        self.table.setItem(r, COL_ORIGINAL, item(data.original.replace("\n", "↵")))
        tr = item(
            "" if data.translation is None else data.translation.replace("\n", "↵"),
            True,
        )
        tr.setData(
            Qt.ItemDataRole.EditRole,
            "" if data.translation is None else data.translation,
        )
        self.table.setItem(r, COL_TRANSLATION, tr)
        bytes_item = item(f"{data.used} / {data.room}")
        status = item(data.status)
        if data.problem:
            status.setToolTip(data.problem)
            bytes_item.setToolTip(data.problem)
        colour = self._status_colour(data.status)
        if colour is not None:
            status.setForeground(colour)
            bytes_item.setForeground(colour)
        self.table.setItem(r, COL_BYTES, bytes_item)
        self.table.setItem(r, COL_STATUS, status)
        self.table.setItem(r, COL_NOTES, item(data.notes, True))
        self.table.item(r, COL_INDEX).setData(Qt.ItemDataRole.UserRole, data.index)

    @staticmethod
    def _status_colour(status: str) -> QColor | None:
        if status in ("too long", "invalid", "overflows box"):
            return theme.ERROR_INK
        if status == "review":
            return theme.WARNING_INK
        return None

    def _row_data(self, row: int) -> RowData | None:
        it = self.table.item(row, COL_INDEX)
        if it is None:
            return None
        return self._by_index.get(it.data(Qt.ItemDataRole.UserRole))

    # --- selection and filter ---------------------------------------------

    def selected_indices(self) -> list[int]:
        rows = sorted({i.row() for i in self.table.selectedItems()})
        out = []
        for r in rows:
            d = self._row_data(r)
            if d is not None:
                out.append(d.index)
        return out

    def select_index(self, index: int) -> None:
        for r in range(self.table.rowCount()):
            it = self.table.item(r, COL_INDEX)
            if it is not None and it.data(Qt.ItemDataRole.UserRole) == index:
                self.table.blockSignals(True)
                self.table.selectRow(r)
                self.table.scrollToItem(it)
                self.table.blockSignals(False)
                return

    def _apply_filter(self) -> None:
        words = fold(self.filter.text()).split()
        status = self.status_filter.currentText()
        for r in range(self.table.rowCount()):
            d = self._row_data(r)
            if d is None:
                continue
            hay = fold(f"{d.original} {d.translation or ''} {d.notes}")
            hidden = bool(words) and not all(w in hay for w in words)
            if status != "all" and d.status != status:
                hidden = True
            self.table.setRowHidden(r, hidden)

    def _on_item_changed(self, item: QTableWidgetItem) -> None:
        if self._filling:
            return
        d = self._row_data(item.row())
        if d is None:
            return
        if item.column() == COL_TRANSLATION:
            text = item.data(Qt.ItemDataRole.EditRole) or ""
            self.translation_edited.emit(d.index, text)
        elif item.column() == COL_NOTES:
            self.notes_edited.emit(d.index, item.text())

    def _on_select(self) -> None:
        idx = self.selected_indices()
        if idx:
            self.row_selected.emit(idx[0])

    def _on_menu(self, pos: QPoint) -> None:
        self.context_menu_requested.emit(
            self.selected_indices(), self.table.viewport().mapToGlobal(pos)
        )


__all__ = ["CodeInfo", "RowData", "StringsView"]
