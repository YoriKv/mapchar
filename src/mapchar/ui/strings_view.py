"""The Strings view: a block's strings beside what its bytes say now.

Presentation only: the window hands it rows and takes back edits. The
Translation cell is a multi-line editor with code completion on ``[``;
Return commits and moves to the next row, Ctrl+Return commits and stays,
Shift+Return inserts the block's newline code, Esc cancels. Leaving the cell
commits too. A text the bytes refuse is kept by the window as the string's
unwritten translation, which the row shows in place of what the bytes say:
Return leaves the editor open on it with the reason. Columns hide
and reorder from the header's context menu. Under the grid, the pane
(:mod:`mapchar.ui.string_pane`) shows the selected string whole, on the same
editor.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from PySide6.QtCore import QEvent, QPoint, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QTextCursor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSplitter,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.block import Status
from mapchar.core.textmatch import matches_words, words_of
from mapchar.ui import settings, theme
from mapchar.ui.bars import FlowLayout
from mapchar.ui.code_editor import CodeEditor, CodeInfo
from mapchar.ui.string_pane import StringPane
from mapchar.ui.widgets import install_column_menu, show_elided_tooltips
from mapchar.ui.window_layout import stored_bytes

(
    COL_INDEX,
    COL_ADDRESS,
    COL_POINTERS,
    COL_ORIGINAL,
    COL_TRANSLATION,
    COL_BYTES,
    COL_STATUS,
    COL_SAME,
    COL_NOTES,
) = range(9)
HEADERS = [
    "#",
    "Address",
    "Pointers",
    "Original",
    "Translation",
    "Bytes",
    "Status",
    "Same",
    "Notes",
]
OVERFLOWS = "overflows box"
"""The one status a row has that :class:`~mapchar.core.block.Status` does not:
the string is finished but does not fit, which is the bytes' answer rather than
anything the record holds."""
UNWRITTEN = "unwritten"
"""The other one: the string keeps a translation its bytes refused, and the row
shows that text rather than what the bytes say."""
MISSES = "misses glossary"
"""Not a status but a filter beside them: the rows whose translation has a
glossary term some other way than the glossary does."""
STATUS_FILTERS = ["all", *(s.value for s in Status), OVERFLOWS, UNWRITTEN, MISSES]
"""What the Status picker offers, in the order it offers it."""
FLAGGED = (Status.REVIEW.value, OVERFLOWS, UNWRITTEN)
"""The statuses Next Flagged steps through: what needs a second look."""
SPLITTER_KEY = "view/strings_splitter"
"""Where the grid and the pane under it are split, remembered per machine."""


def status_matches(wanted: str, status: str, misses: str = "") -> bool:
    """Whether a row of this status passes the Status picker set to ``wanted``;
    ``"all"`` passes everything, and :data:`MISSES` the rows with ``misses``."""
    if wanted == MISSES:
        return bool(misses)
    return wanted == "all" or status == wanted


@dataclass
class RowData:
    index: int
    address: int
    original: str
    translation: str
    """What the bytes say now."""
    used: int
    room: int
    status: str
    """A :class:`~mapchar.core.block.Status` value, :data:`OVERFLOWS` or
    :data:`UNWRITTEN`."""
    notes: str
    pointers: str = ""
    same: int = 0
    """How many other strings of the block have this original."""
    room_note: str = ""
    """What the room is made of, shown on the Bytes cell: a packed block's
    spare is every string's and no two strings' at once, which the two numbers
    alone do not say."""
    unwritten: str | None = None
    """A translation the bytes refused, which the string keeps."""
    problem: str = ""
    """Why they refuse it; nothing once it would go in."""
    misses: str = ""
    """The glossary terms the original holds that the translation has some
    other way than the glossary does; nothing for a string not translated."""

    @property
    def shown(self) -> str:
        """What the Translation cell and the pane's editor open on: the
        unwritten translation where one is kept, else what the bytes say."""
        return self.translation if self.unwritten is None else self.unwritten

    @property
    def unwritten_note(self) -> str:
        """The row's account of an unwritten translation, for a tooltip."""
        if self.unwritten is None:
            return ""
        why = self.problem or "writable now (Write Unwritten Translations)"
        return f"Not written: {why}\nThe bytes say: {self.translation}"


class TranslationDelegate(QStyledItemDelegate):
    """The Translation cell's editor, and the two ways an edit lands.

    Return hands the text to :attr:`StringsView.commit_handler` here, and
    leaving the cell hands it to the same handler through Qt's own
    ``commitData``, which lands in :meth:`setModelData`. A text the bytes
    refuse is kept by the window either way: Return leaves the editor open on
    it with the reason shown, and leaving the cell leaves the row showing it.
    """

    def __init__(self, view: StringsView):
        super().__init__(view)
        self.view = view
        self.codes: list[CodeInfo] = []
        self.newline_code = "[line]"
        self._editor: CodeEditor | None = None
        self._index = None

    def createEditor(self, parent, option, index):
        editor = CodeEditor(self.codes, self.newline_code, parent)
        editor.commit.connect(lambda advance: self._finish(editor, True, advance))
        editor.cancel.connect(lambda: self._finish(editor, False))
        editor.textChanged.connect(
            lambda: self.view.draft_changed.emit(editor.toPlainText())
        )
        self._editor = editor
        self._index = index
        return editor

    def _finish(self, editor: CodeEditor, commit: bool, advance: bool = False) -> None:
        """Return or Esc in the editor.

        A commit goes to the window first: a text the bytes refuse — too long,
        a code that does not encode — keeps the editor open with the reason
        shown, for another try at it. Leaving the cell commits through
        :meth:`setModelData` instead, on the same handler.
        """
        if commit:
            handler = self.view.commit_handler
            if handler is not None:
                data = self.view._row_data(self._index.row()) if self._index else None
                if data is not None:
                    problem = handler(data.index, editor.toPlainText())
                    if problem:
                        self.view.problem_shown.emit(problem)
                        return
                    self.closeEditor.emit(
                        editor, QStyledItemDelegate.EndEditHint.NoHint
                    )
                    self._editor = None
                    if advance:
                        self.view.edit_next_row()
                    return
            self.commitData.emit(editor)
        self.closeEditor.emit(editor, QStyledItemDelegate.EndEditHint.NoHint)
        self._editor = None

    def setEditorData(self, editor, index) -> None:
        """What the bytes say, or the translation the string keeps unwritten."""
        editor.setPlainText(index.data(Qt.ItemDataRole.EditRole) or "")
        editor.moveCursor(QTextCursor.MoveOperation.End)

    def setModelData(self, editor, model, index) -> None:
        """Leaving the cell: the same commit Return makes.

        The window takes the text and says why it would not go in; refused,
        the bytes keep what they say and the row shows the text as unwritten.
        A cell left as it was is not a commit at all.
        """
        text = editor.toPlainText()
        handler = self.view.commit_handler
        data = self.view._row_data(index.row())
        if handler is None or data is None:
            model.setData(index, text, Qt.ItemDataRole.EditRole)
            return
        if text == data.shown:
            return
        problem = handler(data.index, text)
        if problem:
            self.view.problem_shown.emit(problem)

    def eventFilter(self, editor, event) -> bool:  # noqa: N802 - Qt override
        """Qt's commit-on-focus-out, minus the focus-outs that are not leaving.

        The base filter commits and closes the editor whenever it loses the
        focus; the completion popup taking it, or the window going inactive,
        is neither leaving the cell nor a reason to hand the draft to the
        bytes, so the editor stays open on it instead.
        """
        if (
            isinstance(event, QFocusEvent)
            and event.type() == QEvent.Type.FocusOut
            and event.reason()
            in (
                Qt.FocusReason.PopupFocusReason,
                Qt.FocusReason.ActiveWindowFocusReason,
            )
        ):
            return False
        return super().eventFilter(editor, event)

    def destroyEditor(self, editor, index) -> None:
        if editor is self._editor:
            self._editor = None
            self._index = None
        super().destroyEditor(editor, index)

    def current_editor(self) -> CodeEditor | None:
        return self._editor


_LOCKED_FLAGS = QTableWidgetItem().flags() & ~Qt.ItemFlag.ItemIsEditable
"""A cell that cannot be edited in place; worked out once, not per cell."""


class StringsView(QWidget):
    translation_edited = Signal(int, str)
    notes_edited = Signal(int, str)
    row_selected = Signal(int)
    draft_changed = Signal(str)
    problem_shown = Signal(str)
    """A commit the window refused, with why: shown where the draft readout is."""
    revert_requested = Signal(list)
    review_toggled = Signal(list)
    context_menu_requested = Signal(list, QPoint)
    glossary_add_requested = Signal(str, str)
    """Add a term: what is marked in the pane's original, and in its editor."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._rows: list[RowData] = []
        self._by_index: dict[int, RowData] = {}
        self._filling = False
        self.commit_handler: Callable[[int, str], str | None] | None = None
        """What the editor's Return hands the text to: ``None`` when it landed,
        else why it did not, which keeps the editor open."""
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
        self.pane = StringPane()
        self.pane.committer = self._commit_from_pane
        self.splitter = QSplitter(Qt.Orientation.Vertical)
        self.splitter.addWidget(self.table)
        self.splitter.addWidget(self.pane)
        self.splitter.setStretchFactor(0, 3)
        self.splitter.setStretchFactor(1, 1)
        state = stored_bytes(settings().value(SPLITTER_KEY))
        if state is not None:
            self.splitter.restoreState(state)
        self.splitter.splitterMoved.connect(
            lambda *_: settings().setValue(SPLITTER_KEY, self.splitter.saveState())
        )
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addLayout(top)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(self.codes)
        self.filter.textChanged.connect(self._apply_filter)
        self.status_filter.currentIndexChanged.connect(self._apply_filter)
        self.table.itemChanged.connect(self._on_item_changed)
        self.table.itemSelectionChanged.connect(self._on_select)
        self.table.customContextMenuRequested.connect(self._on_menu)
        self.pane.draft_changed.connect(self.draft_changed)
        self.pane.problem_shown.connect(self.problem_shown)
        self.pane.notes_edited.connect(self.notes_edited)
        self.pane.advance_requested.connect(lambda: self.step_row(1))
        self.pane.glossary_add_requested.connect(self.glossary_add_requested)

    # --- rows ------------------------------------------------------------

    def set_codes(self, codes: list[CodeInfo]) -> None:
        """The table set's codes, and buttons for the ones this block uses most."""
        self.delegate.codes = list(codes)
        self.pane.set_codes(self.delegate.codes)
        while self.codes_layout.count():
            item = self.codes_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        most_used = sorted(codes, key=lambda c: (-c.uses, c.label))[:24]
        for code in most_used:
            button = QPushButton(
                code.insertion.rstrip() if code.operands else code.insertion
            )
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            uses = f"{code.uses} use(s) in this block" if code.uses else "unused here"
            button.setToolTip(f"{code.comment}\n{uses}" if code.comment else uses)
            button.clicked.connect(
                lambda _=False, text=code.insertion: self.insert_text(text)
            )
            self.codes_layout.addWidget(button)

    def set_newline_code(self, code: str) -> None:
        """What Shift+Return writes in the Translation cell."""
        self.delegate.newline_code = code
        self.pane.set_newline_code(code)

    def insert_text(self, text: str) -> None:
        """A code button or a glossary term: into the cell being edited, else
        into the pane's editor, which takes the focus so typing carries on
        there."""
        editor = self.delegate.current_editor()
        if editor is None and self.pane.index is not None:
            editor = self.pane.editor
            editor.setFocus()
        if editor is not None:
            editor.insert_code(text)

    def select_span(self, start: int, stop: int) -> None:
        """Mark characters ``start``–``stop`` of the selected string's text in
        the pane's editor: the hit a search stands on."""
        self.pane.select_span(start, stop)

    def selected_text(self) -> tuple[str, str]:
        """What is marked in the pane: in the original, and in the editor."""
        return self.pane.selected_text()

    def set_terms(self, terms) -> None:
        """The glossary's terms, underlined in the pane's original."""
        self.pane.set_terms(terms)

    def set_readout(self, text: str, problem: bool = False) -> None:
        """The pane's byte readout: the draft's budget, or why a commit was
        refused."""
        self.pane.set_readout(text, problem)

    def _commit_from_pane(self, index: int, text: str) -> str | None:
        handler = self.commit_handler
        if handler is None:
            self.translation_edited.emit(index, text)
            return None
        return handler(index, text)

    def _show_in_pane(self) -> None:
        """The pane on the selected row, or on nothing."""
        selected = self.selected_indices()
        self.pane.set_row(self._by_index.get(selected[0]) if selected else None)

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
        self.table.resizeColumnToContents(COL_SAME)
        self.table.setColumnWidth(
            COL_ORIGINAL, max(self.table.columnWidth(COL_ORIGINAL), 260)
        )
        self.table.setColumnWidth(
            COL_TRANSLATION, max(self.table.columnWidth(COL_TRANSLATION), 260)
        )
        self._apply_filter()
        if selected:
            self.select_index(selected[0])
        else:
            self._show_in_pane()

    def rows_by_index(self) -> dict[int, RowData]:
        """The rows the grid holds now, by string index; read, never changed.

        What a caller refreshing a few rows of many compares against, so it
        hands back only the rows that have something else to say.
        """
        return self._by_index

    def update_row(self, data: RowData) -> None:
        for r, existing in enumerate(self._rows):
            if existing.index == data.index:
                self._rows[r] = data
                self._by_index[data.index] = data
                self._filling = True
                self._fill_row(r, data)
                self._filling = False
                if data.index == self.pane.index:
                    self.pane.set_row(data)
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
        tr = item(data.shown.replace("\n", "↵"), True)
        tr.setData(Qt.ItemDataRole.EditRole, data.shown)
        if data.unwritten is not None:
            tr.setForeground(theme.ERROR_INK)
            tr.setToolTip(data.unwritten_note)
        elif data.misses:
            tr.setForeground(theme.WARNING_INK)
            tr.setToolTip(f"Differs from the glossary: {data.misses}")
        self.table.setItem(r, COL_TRANSLATION, tr)
        bytes_item = item(f"{data.used} / {data.room}")
        if data.room_note:
            bytes_item.setToolTip(data.room_note)
        status = item(data.status)
        colour = self._status_colour(data.status)
        if colour is not None:
            status.setForeground(colour)
        if data.unwritten is not None:
            status.setToolTip(data.unwritten_note)
        self.table.setItem(r, COL_BYTES, bytes_item)
        self.table.setItem(r, COL_STATUS, status)
        same = item(f"×{data.same + 1}" if data.same else "")
        if data.same:
            same.setToolTip(
                f"{data.same} other string(s) in this block share this original"
            )
        self.table.setItem(r, COL_SAME, same)
        self.table.setItem(r, COL_NOTES, item(data.notes, True))
        self.table.item(r, COL_INDEX).setData(Qt.ItemDataRole.UserRole, data.index)

    @staticmethod
    def _status_colour(status: str) -> QColor | None:
        if status in (OVERFLOWS, UNWRITTEN):
            return theme.ERROR_INK
        if status == Status.REVIEW.value:
            return theme.WARNING_INK
        if status == Status.DONE.value:
            return theme.DONE_INK
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
                self._show_in_pane()
                return
        self._show_in_pane()

    # --- stepping through the rows ------------------------------------------

    def _visible_rows(self) -> list[int]:
        return [
            r for r in range(self.table.rowCount()) if not self.table.isRowHidden(r)
        ]

    def edit_row(self, r: int) -> None:
        """Select row ``r`` and open its Translation cell."""
        self.table.setCurrentCell(r, COL_TRANSLATION)
        self.table.edit(self.table.model().index(r, COL_TRANSLATION))

    def edit_index(self, index: int) -> None:
        """Open the Translation cell of the string ``index``, wherever its row
        is; nothing when the filter hides it or the block no longer holds it."""
        for r in self._visible_rows():
            data = self._row_data(r)
            if data is not None and data.index == index:
                self.edit_row(r)
                return

    def edit_next_row(self) -> None:
        """Move to the next visible row and open its Translation cell."""
        rows = self._visible_rows()
        current = self.table.currentRow()
        after = [r for r in rows if r > current]
        if after:
            self.edit_row(after[0])

    def step_row(self, delta: int) -> bool:
        """Select the visible row ``delta`` rows on from the current one;
        ``False`` at the end."""
        rows = self._visible_rows()
        current = self.table.currentRow()
        if current not in rows:
            return False
        at = rows.index(current) + delta
        if not 0 <= at < len(rows):
            return False
        self.table.setCurrentCell(rows[at], COL_TRANSLATION)
        self.table.scrollToItem(self.table.item(rows[at], COL_TRANSLATION))
        return True

    def step_to(self, wanted, backwards: bool = False) -> bool:
        """Select the next (or previous) visible row whose data ``wanted``
        accepts, wrapping round; False when there is none."""
        rows = self._visible_rows()
        current = self.table.currentRow()
        if backwards:
            order = [r for r in reversed(rows) if r < current] + [
                r for r in reversed(rows) if r >= current
            ]
        else:
            order = [r for r in rows if r > current] + [r for r in rows if r <= current]
        for r in order:
            data = self._row_data(r)
            if data is not None and wanted(data):
                self.table.setCurrentCell(r, COL_TRANSLATION)
                self.table.scrollToItem(self.table.item(r, COL_TRANSLATION))
                return True
        return False

    def _apply_filter(self) -> None:
        words = words_of(self.filter.text())
        status = self.status_filter.currentText()
        for r in range(self.table.rowCount()):
            d = self._row_data(r)
            if d is None:
                continue
            hidden = not matches_words(
                words, d.original, d.translation, d.unwritten or "", d.notes
            ) or not status_matches(status, d.status, d.misses)
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
        """The selection moved. A draft in the pane lands first, or is kept
        unwritten: either way nothing typed is lost by moving on."""
        idx = self.selected_indices()
        if idx and idx[0] != self.pane.index:
            self.pane.flush()
        self._show_in_pane()
        if idx:
            self.row_selected.emit(idx[0])

    def _on_menu(self, pos: QPoint) -> None:
        self.context_menu_requested.emit(
            self.selected_indices(), self.table.viewport().mapToGlobal(pos)
        )


__all__ = [
    "FLAGGED",
    "MISSES",
    "OVERFLOWS",
    "UNWRITTEN",
    "RowData",
    "StringsView",
    "status_matches",
]
