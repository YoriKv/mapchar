"""Widgets and widget helpers more than one window needs.

Most of them answer one of two questions every surface has to: what a control
does when its room runs out (:class:`FlowLayout`, :class:`ElidedLabel`,
:func:`fit_chars`), and how text it had to cut short can still be read — a
tooltip carrying the whole of it (:class:`ElidedLabel`,
:class:`CompactComboBox`, :func:`show_elided_tooltips`). ``docs/ui.md`` holds
the rules they implement.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QLabel,
    QLayout,
    QLineEdit,
    QProgressDialog,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QWidget,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator, Sequence

T = TypeVar("T")

MONO_FAMILIES = (
    "Cascadia Mono",
    "Consolas",
    "DejaVu Sans Mono",
    "Menlo",
    "Noto Sans Mono CJK JP",
    "Noto Sans CJK JP",
    "Meiryo",
    "Yu Gothic",
    "MS Gothic",
    "Hiragino Sans",
    "monospace",
)
"""Families in fallback order, tried per character: a monospaced face for hex
and Latin text, then faces that draw kana and kanji, so a Japanese decode is
not a row of boxes and the hex is not drawn in a Japanese face's digits."""


def mono_font(point_size: int = 10) -> QFont:
    """The face every byte and decoded-text view draws in: :data:`MONO_FAMILIES`."""
    font = QFont()
    font.setFamilies(MONO_FAMILIES)
    font.setStyleHint(QFont.StyleHint.TypeWriter)
    font.setPointSize(point_size)
    return font


PICKER_WIDTH = 160
"""The closed width of a codec or table picker: one number, so a bar of them
reads as a row whatever names the registry and the project give their items."""


def _joined_tip(full: str, own: str) -> str:
    """What a tooltip over cut-short text says: the whole text, then whatever
    the control already explained, unless that already begins with it."""
    if not own or own == full:
        return full
    if own.startswith(full):
        return own
    return f"{full}\n\n{own}"


class CompactComboBox(QComboBox):
    """A combo box whose closed button is a stated width in pixels.

    A stock combo reserves the width of its longest item, which long plugin,
    table and preset names turn into dead space in a bar — and a bar whose
    width changes as the items do. Only the size *hints* are set, so a layout
    may still stretch one; the open list is widened back to its longest item,
    so every entry stays readable while choosing. A current item too long for
    the closed button is elided there, and its hover tooltip spells it out.
    """

    def __init__(self, width: int = PICKER_WIDTH, parent: QWidget | None = None):
        super().__init__(parent)
        self._width = width

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().sizeHint()
        hint.setWidth(self._width)
        return hint

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        hint.setWidth(self._width)
        return hint

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        view = self.view()
        width = view.sizeHintForColumn(0) + view.verticalScrollBar().sizeHint().width()
        view.setMinimumWidth(max(self.width(), width))
        super().showPopup()

    def current_text_elided(self) -> bool:
        """Whether the closed button has too little room for the current item."""
        option = QStyleOptionComboBox()
        self.initStyleOption(option)
        field = self.style().subControlRect(
            QStyle.ComplexControl.CC_ComboBox,
            option,
            QStyle.SubControl.SC_ComboBoxEditField,
            self,
        )
        room = field.width()
        if not self.itemIcon(self.currentIndex()).isNull():
            room -= self.iconSize().width() + 4
        return self.fontMetrics().horizontalAdvance(self.currentText()) > room

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.current_text_elided():
            QToolTip.showText(
                event.globalPos(), _joined_tip(self.currentText(), self.toolTip()), self
            )
            return True
        return super().event(event)


_COMMAND = "\x00command"
"""The datum of a :class:`CommandComboBox`'s command row; no choice carries it."""


class CommandComboBox(CompactComboBox):
    """A picker whose last row runs a command (``New Table…``) instead of
    being a choice.

    Choosing the row never shows it: the choice before it stays current and
    :attr:`command` fires. :attr:`chosen` stands in for ``currentIndexChanged``,
    firing for a real choice only. A refill clears the row, so the filler puts it
    back with :meth:`add_command_row`.
    """

    chosen = Signal()
    command = Signal()

    def __init__(
        self, label: str, width: int = PICKER_WIDTH, parent: QWidget | None = None
    ):
        super().__init__(width, parent)
        self._label = label
        self._shown = -1
        self.currentIndexChanged.connect(self._on_index)

    def add_command_row(self) -> None:
        was_blocked = self.blockSignals(True)
        self.insertSeparator(self.count())
        self.addItem(self._label, _COMMAND)
        self.blockSignals(was_blocked)

    def setCurrentIndex(self, index: int) -> None:  # noqa: N802 - Qt override
        # A change made with signals blocked never reaches _on_index, so the
        # choice to fall back to is recorded here too.
        super().setCurrentIndex(index)
        # Read back rather than taken from ``index``: the command a change set off
        # may have refilled the list and chosen something else meanwhile.
        if self.currentData() != _COMMAND:
            self._shown = self.currentIndex()

    def _on_index(self, index: int) -> None:
        if self.itemData(index) == _COMMAND:
            was_blocked = self.blockSignals(True)
            super().setCurrentIndex(self._shown)
            self.blockSignals(was_blocked)
            self.command.emit()
            return
        self._shown = index
        self.chosen.emit()


class ElidedLabel(QLabel):
    """A one-line label that cuts its text short with an ellipsis rather than
    widening the layout it sits in.

    A plain ``QLabel`` asks for the width of everything it says, so one long
    status line — a notice, a path, a list of glyphs the font lacks — is enough
    to set the minimum width of the whole window around it. This one asks for
    nothing, draws what fits, and shows the whole text in its tooltip whenever
    some of it was cut. :meth:`text` and :meth:`toolTip` still answer with what
    was set, so code and tests read the label exactly as before.
    """

    def __init__(
        self,
        text: str = "",
        parent: QWidget | None = None,
        mode: Qt.TextElideMode = Qt.TextElideMode.ElideRight,
    ):
        super().__init__(text, parent)
        self._mode = mode
        self.setTextFormat(Qt.TextFormat.PlainText)

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        hint = super().minimumSizeHint()
        hint.setWidth(0)
        return hint

    def is_elided(self) -> bool:
        return (
            self.fontMetrics().horizontalAdvance(self.text())
            > self.contentsRect().width()
        )

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt override
        from PySide6.QtGui import QPainter

        painter = QPainter(self)
        rect = self.contentsRect()
        shown = self.fontMetrics().elidedText(self.text(), self._mode, rect.width())
        self.style().drawItemText(
            painter,
            rect,
            int(self.alignment()),
            self.palette(),
            self.isEnabled(),
            shown,
            self.foregroundRole(),
        )
        painter.end()

    def event(self, event: QEvent) -> bool:
        if event.type() == QEvent.Type.ToolTip and self.text() and self.is_elided():
            QToolTip.showText(
                event.globalPos(), _joined_tip(self.text(), self.toolTip()), self
            )
            return True
        return super().event(event)


class _ElidedItemTips(QObject):
    """The viewport filter behind :func:`show_elided_tooltips`."""

    def __init__(self, view: QAbstractItemView):
        super().__init__(view)
        self._view = view

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.ToolTip:
            return False
        view = self._view
        index = view.indexAt(event.pos())
        if not index.isValid():
            return False
        text = index.data(Qt.ItemDataRole.DisplayRole)
        if not isinstance(text, str) or not text:
            return False
        rect = view.visualRect(index)
        option = QStyleOptionViewItem()
        option.font = view.font()
        small = view.style().pixelMetric(QStyle.PixelMetric.PM_SmallIconSize)
        option.decorationSize = (
            view.iconSize() if view.iconSize().isValid() else QSize(small, small)
        )
        option.rect = rect
        needed = view.itemDelegateForIndex(index).sizeHint(option, index).width()
        if needed <= rect.width():
            return False
        own = index.data(Qt.ItemDataRole.ToolTipRole)
        QToolTip.showText(
            event.globalPos(),
            _joined_tip(text, own if isinstance(own, str) else ""),
            view.viewport(),
            rect,
        )
        return True


def show_elided_tooltips(view: QAbstractItemView) -> QAbstractItemView:
    """Make every cell of ``view`` that is too narrow for its text show the whole
    of it on hover, ahead of any tooltip the cell carries of its own.

    A cell with room for its text keeps the view's usual tooltip behaviour, so
    this can go on any list, tree or table without changing what the ones that
    fit say. Returns ``view``, to wrap a constructor call.
    """
    view.viewport().installEventFilter(_ElidedItemTips(view))
    return view


def fit_chars(widget: QWidget, chars: int) -> QWidget:
    """Give a field a minimum width of ``chars`` average characters plus its
    frame, so no layout can squeeze it to where what it holds cannot be read."""
    metrics = widget.fontMetrics()
    frame = 2 * widget.style().pixelMetric(QStyle.PixelMetric.PM_DefaultFrameWidth)
    widget.setMinimumWidth(metrics.horizontalAdvance("0" * chars) + frame + 12)
    return widget


def hint_field(field: QLineEdit, placeholder: str, tip: str | None = None) -> QLineEdit:
    """Set a field's placeholder, and a tooltip saying the same thing.

    A placeholder is the first text a narrow field cuts short, and it vanishes
    the moment anything is typed; the tooltip is where it can still be read.
    """
    field.setPlaceholderText(placeholder)
    field.setToolTip(tip or placeholder)
    return field


class FlowLayout(QLayout):
    """A layout that lines its items up left to right and wraps them onto as
    many rows as the width needs.

    For a row of controls whose count is not fixed — the code buttons of a
    block — or that is long enough to otherwise set a window's minimum width.
    The minimum it asks for is its widest single item, and the height follows
    the width through ``heightForWidth``.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = -1):
        super().__init__(parent)
        self._items = []
        if parent is None:
            self.setContentsMargins(0, 0, 0, 0)
        self.setSpacing(spacing)

    def addItem(self, item) -> None:  # noqa: N802 - Qt override
        self._items.append(item)

    def count(self) -> int:
        return len(self._items)

    def itemAt(self, index: int):  # noqa: N802 - Qt override
        return self._items[index] if 0 <= index < len(self._items) else None

    def takeAt(self, index: int):  # noqa: N802 - Qt override
        return self._items.pop(index) if 0 <= index < len(self._items) else None

    def expandingDirections(self) -> Qt.Orientation:  # noqa: N802 - Qt override
        return Qt.Orientation(0)

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt override
        return True

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self._arrange(QRect(0, 0, width, 0), apply=False)

    def setGeometry(self, rect: QRect) -> None:  # noqa: N802 - Qt override
        super().setGeometry(rect)
        self._arrange(rect, apply=True)

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        return self.minimumSize()

    def minimumSize(self) -> QSize:  # noqa: N802 - Qt override
        size = QSize()
        for item in self._items:
            size = size.expandedTo(item.minimumSize())
        margins = self.contentsMargins()
        return size + QSize(
            margins.left() + margins.right(), margins.top() + margins.bottom()
        )

    def _gap(self, orientation: Qt.Orientation) -> int:
        if self.spacing() >= 0:
            return self.spacing()
        parent = self.parentWidget()
        if parent is None:
            return 6
        metric = (
            QStyle.PixelMetric.PM_LayoutHorizontalSpacing
            if orientation == Qt.Orientation.Horizontal
            else QStyle.PixelMetric.PM_LayoutVerticalSpacing
        )
        gap = parent.style().pixelMetric(metric, None, parent)
        return gap if gap >= 0 else 6

    def _arrange(self, rect: QRect, apply: bool) -> int:
        margins = self.contentsMargins()
        area = rect.adjusted(
            margins.left(), margins.top(), -margins.right(), -margins.bottom()
        )
        x, y, line = area.x(), area.y(), 0
        across = self._gap(Qt.Orientation.Horizontal)
        down = self._gap(Qt.Orientation.Vertical)
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            if x + hint.width() > area.right() + 1 and line > 0:
                x, y, line = area.x(), y + line + down, 0
            if apply:
                item.setGeometry(QRect(QPoint(x, y), hint))
            x += hint.width() + across
            line = max(line, hint.height())
        return y + line - rect.y() + margins.bottom()


class EscapeCloses:
    """Mixed into a tool window so Esc closes it, the way it closes a dialog.

    Only a press nothing inside the window used reaches here: an open cell
    editor, a completer or a combo's popup spends its own Esc first.
    """

    def keyPressEvent(self, event) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape and event.modifiers() in (
            Qt.KeyboardModifier.NoModifier,
            Qt.KeyboardModifier.KeypadModifier,
        ):
            self.close()
            return
        super().keyPressEvent(event)


class ResultsTable(QTableWidget):
    """A read-only grid of results: whole rows select, and a row is an item.

    The window keeps the list the rows stand for and asks :meth:`pick` which
    of them the selection names.
    """

    def __init__(self, headers: Sequence[str], parent: QWidget | None = None):
        super().__init__(0, len(headers), parent)
        self.setHorizontalHeaderLabels(list(headers))
        self.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.horizontalHeader().setStretchLastSection(True)
        show_elided_tooltips(self)

    def fill(self, rows: Iterable[Sequence[str]]) -> None:
        """Replace every row; columns are sized to what they now hold."""
        rows = list(rows)
        self.setRowCount(len(rows))
        for row, cells in enumerate(rows):
            for col, text in enumerate(cells):
                self.setItem(row, col, QTableWidgetItem(text))
        self.resizeColumnsToContents()

    def current_row(self) -> int | None:
        rows = self.selectionModel().selectedRows()
        return rows[0].row() if rows else None

    def pick(self, items: Sequence[T]) -> T | None:
        """The item the selected row stands for, if a row is selected."""
        row = self.current_row()
        return items[row] if row is not None and row < len(items) else None


class CancellableRun:
    """A run button, a stop button and a progress line over one long search.

    A window builds the three widgets, hands them to :meth:`bind_run`, wraps
    the work in :meth:`running` and passes :meth:`progress` to the engine as
    its progress callback; :attr:`cancelled` says whether Stop was pressed.
    """

    def bind_run(
        self, run: QPushButton, stop: QPushButton, status: QLabel, verb: str
    ) -> None:
        self._run_button = run
        self._stop_button = stop
        self._status_label = status
        self._verb = verb
        self._cancelled = False
        stop.setEnabled(False)
        stop.clicked.connect(self.cancel)

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def cancel(self) -> None:
        self._cancelled = True

    @contextmanager
    def running(self) -> Iterator[None]:
        self._cancelled = False
        self._run_button.setEnabled(False)
        self._stop_button.setEnabled(True)
        try:
            yield
        finally:
            self._run_button.setEnabled(True)
            self._stop_button.setEnabled(False)

    def progress(self, done: int, total: int) -> bool:
        """Report how far the work is; ``False`` asks the engine to stop."""
        self._status_label.setText(f"{self._verb}… {done * 100 // max(total, 1)}%")
        QApplication.processEvents()
        return not self._cancelled


class ModalProgress:
    """A progress bar with a Stop button over one long call, in front of a window.

    :class:`CancellableRun`'s counterpart for work started from a **menu** rather
    than from a tool window that has a run/stop row of its own: the search for
    pointers has nowhere to put those two buttons, and a menu row that freezes the
    window for a minute with no way out is the one thing every long operation here
    is supposed not to do.

    Used as a context manager; :meth:`progress` is what the engine is handed, and
    it pumps the event loop so the Stop button can be clicked at all. The engine
    is asked to stop rather than interrupted, so a cancelled run still returns
    whatever it had found by then.
    """

    def __init__(self, parent: QWidget | None, title: str, label: str) -> None:
        dialog = QProgressDialog(label, "Stop", 0, 1, parent)
        dialog.setWindowTitle(title)
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)  # the work has already started
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        self.dialog = dialog

    def __enter__(self) -> ModalProgress:
        self.dialog.setValue(0)
        return self

    def __exit__(self, *exc: object) -> None:
        self.dialog.reset()
        self.dialog.close()
        self.dialog.deleteLater()

    @property
    def cancelled(self) -> bool:
        return self.dialog.wasCanceled()

    def cancel(self) -> None:
        self.dialog.cancel()

    def progress(self, done: int, total: int) -> bool:
        """Report how far the work is; ``False`` asks the engine to stop."""
        self.dialog.setMaximum(max(total, 1))
        self.dialog.setValue(min(done, max(total, 1)))
        QApplication.processEvents()
        return not self.dialog.wasCanceled()


def fill_pick(
    combo: QComboBox,
    items: Iterable[tuple[str, object]],
    none_label: str | None = None,
    keep_current: bool = True,
) -> None:
    """Refill a combo from ``(label, data)`` pairs without emitting signals.

    ``none_label`` heads the list with a ``None`` datum. With ``keep_current``
    the item carrying the data that showed before stays showing, else the
    first item does.
    """
    current = combo.currentData()
    # Put back the state found, not "unblocked": a caller refilling inside its
    # own block (restoring a session) must not have the rest of its work fire.
    was_blocked = combo.blockSignals(True)
    combo.clear()
    if none_label is not None:
        combo.addItem(none_label, None)
    for label, data in items:
        combo.addItem(label, data)
    if keep_current:
        combo.setCurrentIndex(max(combo.findData(current), 0))
    combo.blockSignals(was_blocked)


def select_data(combo: QComboBox, value: object) -> bool:
    """Show the item carrying ``value``; ``False`` when the combo has none."""
    index = combo.findData(value)
    if index < 0:
        return False
    combo.setCurrentIndex(index)
    return True


__all__ = [
    "PICKER_WIDTH",
    "CancellableRun",
    "CommandComboBox",
    "CompactComboBox",
    "ElidedLabel",
    "EscapeCloses",
    "FlowLayout",
    "MONO_FAMILIES",
    "ModalProgress",
    "ResultsTable",
    "fill_pick",
    "fit_chars",
    "hint_field",
    "mono_font",
    "select_data",
    "show_elided_tooltips",
]
