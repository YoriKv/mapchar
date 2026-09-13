"""The Strings view: a block's strings beside their translations.

Presentation only: the window hands it rows and takes back edits. The
Translation cell is a multi-line editor with code completion on ``[``;
Return commits, Shift+Return breaks a line, Esc cancels.
"""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, QStringListModel, Qt, Signal
from PySide6.QtGui import QColor, QFontDatabase, QTextCursor
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

from mapchar.ui import theme

(
    COL_INDEX,
    COL_ADDRESS,
    COL_ORIGINAL,
    COL_TRANSLATION,
    COL_BYTES,
    COL_STATUS,
    COL_NOTES,
) = range(7)
HEADERS = ["#", "Address", "Original", "Translation", "Bytes", "Status", "Notes"]
STATUS_FILTERS = ["all", "untouched", "edited", "review", "too long", "invalid"]


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


class CodeEditor(QPlainTextEdit):
    """The translation editor: completes ``[labels]``, commits on Return."""

    commit = Signal()
    cancel = Signal()

    def __init__(self, labels: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        self.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.setTabChangesFocus(True)
        self.completer = QCompleter(
            QStringListModel([f"[{lb}]" for lb in labels]), self
        )
        self.completer.setWidget(self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.completer.setCompletionMode(QCompleter.CompletionMode.PopupCompletion)
        self.completer.activated.connect(self._insert_completion)

    def _insert_completion(self, text: str) -> None:
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
                self.insertPlainText("\n")
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
        self.labels: list[str] = []
        self._editor: CodeEditor | None = None

    def createEditor(self, parent, option, index):
        editor = CodeEditor(self.labels, parent)
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
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
            | QAbstractItemView.EditTrigger.AnyKeyPressed
        )
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.setWordWrap(False)
        self.delegate = TranslationDelegate(self)
        self.table.setItemDelegateForColumn(COL_TRANSLATION, self.delegate)
        self.codes = QWidget()
        self.codes_layout = QHBoxLayout(self.codes)
        self.codes_layout.setContentsMargins(0, 0, 0, 0)
        self.codes_layout.addStretch(1)
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

    def set_labels(self, labels: list[str]) -> None:
        self.delegate.labels = list(labels)
        while self.codes_layout.count() > 1:
            item = self.codes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        for label in labels[:24]:
            button = QPushButton(f"[{label}]")
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(
                lambda _=False, lb=label: self._insert_code(f"[{lb}]")
            )
            self.codes_layout.insertWidget(self.codes_layout.count() - 1, button)

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
        self._filling = True
        self.table.setRowCount(len(rows))
        for r, data in enumerate(rows):
            self._fill_row(r, data)
        self._filling = False
        self.table.resizeColumnToContents(COL_INDEX)
        self.table.resizeColumnToContents(COL_ADDRESS)
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
                self._filling = True
                self._fill_row(r, data)
                self._filling = False
                return

    def _fill_row(self, r: int, data: RowData) -> None:
        def item(text: str, editable: bool = False) -> QTableWidgetItem:
            it = QTableWidgetItem(text)
            if not editable:
                it.setFlags(it.flags() & ~Qt.ItemFlag.ItemIsEditable)
            return it

        self.table.setItem(r, COL_INDEX, item(str(data.index)))
        self.table.setItem(r, COL_ADDRESS, item(f"{data.address:X}"))
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
        if status in ("too long", "invalid"):
            return theme.ERROR_INK
        if status == "review":
            return theme.WARNING_INK
        return None

    def _row_data(self, row: int) -> RowData | None:
        it = self.table.item(row, COL_INDEX)
        if it is None:
            return None
        index = it.data(Qt.ItemDataRole.UserRole)
        return next((d for d in self._rows if d.index == index), None)

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
        words = self.filter.text().lower().split()
        status = self.status_filter.currentText()
        for r in range(self.table.rowCount()):
            d = self._row_data(r)
            if d is None:
                continue
            hay = f"{d.original} {d.translation or ''} {d.notes}".lower()
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

    def event(self, event) -> bool:
        return super().event(event)


__all__ = ["QEvent", "RowData", "StringsView"]
