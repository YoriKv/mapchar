"""Widgets and widget helpers more than one window needs.

Most of them answer one of two questions every surface has to: what a control
does when its room runs out (:class:`FlowLayout`, :class:`ElidedLabel`,
:func:`fit_chars`), and how text it had to cut short can still be read — a
tooltip carrying the whole of it (:class:`ElidedLabel`,
:class:`CompactComboBox`, :func:`show_elided_tooltips`). ``docs/ui.md`` holds
the rules they implement.
"""

from __future__ import annotations

import weakref
from contextlib import contextmanager
from functools import partial
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QPalette, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
    QProgressDialog,
    QPushButton,
    QStyle,
    QStyleOptionComboBox,
    QStyleOptionViewItem,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QToolTip,
    QWidget,
)

from mapchar.ui import set_setting_bool, setting_bool

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator, Sequence

    from PySide6.QtWidgets import QDialog, QTableView

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


def setting_toggle(
    label: str, tip: str, key: str, on_toggled: Callable[[bool], None]
) -> QCheckBox:
    """A checkbox on a bar, remembered per machine under ``key`` and on by
    default; ``on_toggled`` is what its switch changes. It takes no focus, so
    clicking it leaves the editor beside it where it was."""
    box = QCheckBox(label)
    box.setToolTip(tip)
    box.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    box.setChecked(setting_bool(key, True))

    def toggled(on: bool) -> None:
        set_setting_bool(key, on)
        on_toggled(on)

    box.toggled.connect(toggled)
    return box


def apply_wrap(edit: QPlainTextEdit, on: bool) -> None:
    """Wrap a text box's lines to its width, or let them run past it.

    Unwrapped, a line wider than the box scrolls sideways, on a bar that is
    there whether or not one is — a bar that came and went would change the
    room for lines with the content.
    """
    edit.setWordWrapMode(
        QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        if on
        else QTextOption.WrapMode.NoWrap
    )
    edit.setHorizontalScrollBarPolicy(
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        if on
        else Qt.ScrollBarPolicy.ScrollBarAlwaysOn
    )


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


ROW_BREAK = "flow_row_break"
"""The property that puts an item of a :class:`FlowLayout`, and what follows
it, on a row of its own: for controls that come and go, so the ones before them
keep a row whose length never changes."""


class FlowLayout(QLayout):
    """A layout that lines its items up left to right and wraps them onto as
    many rows as the width needs.

    For a row of controls whose count is not fixed — the code buttons of a
    block — or that is long enough to otherwise set a window's minimum width.
    The minimum it asks for is its widest single item, and the height follows
    the width through ``heightForWidth``. An item that wraps in turn (a
    :class:`WrapBar` section) is narrowed to the row and as tall as it then
    needs.
    """

    def __init__(self, parent: QWidget | None = None, spacing: int = -1):
        super().__init__(parent)
        self._items = []
        self.fill_rows = False
        """Widen each row's items to share the room left at its end."""
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

    def natural_width(self) -> int:
        """The width the items ask for, whatever of them is showing.

        The widest of the two rows the break rule makes — what comes before the
        first :data:`ROW_BREAK` item, and that item with everything after it —
        since a row of its own is not laid beside the one above it. Every item
        counts, hidden or not: a control that comes and goes would otherwise
        change the width its bar asks for, and everything beside it would move.
        """
        rows: list[list[int]] = [[]]
        broke = False
        for item in self._items:
            widget = item.widget()
            if not broke and widget is not None and widget.property(ROW_BREAK):
                broke = True
                rows.append([])
            # A hidden item measures nothing; the widget under it still knows
            # how wide it would be.
            hint = widget.sizeHint() if widget is not None else item.sizeHint()
            rows[-1].append(hint.width())
        across = self._gap(Qt.Orientation.Horizontal)
        margins = self.contentsMargins()
        widest = max(sum(row) + across * max(len(row) - 1, 0) for row in rows)
        return widest + margins.left() + margins.right()

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
        across = self._gap(Qt.Orientation.Horizontal)
        down = self._gap(Qt.Orientation.Vertical)
        rows: list[list[tuple[QLayoutItem, int]]] = [[]]
        x = area.x()
        broke = False
        for item in self._items:
            if item.isEmpty():
                continue
            hint = item.sizeHint()
            width = max(min(hint.width(), area.width()), item.minimumSize().width())
            widget = item.widget()
            # The first item marked :data:`ROW_BREAK` starts a row of its own
            # with everything after it, whatever room the row before has left.
            breaks = not broke and widget is not None and widget.property(ROW_BREAK)
            if (breaks or x + width > area.right() + 1) and rows[-1]:
                rows.append([])
                x = area.x()
            broke = broke or bool(breaks)
            rows[-1].append((item, width))
            x += width + across
        y = area.y()
        for row in rows:
            if not row:
                continue
            spare = area.width() - sum(w for _, w in row) - across * (len(row) - 1)
            x, line = area.x(), 0
            for index, (item, width) in enumerate(row):
                if self.fill_rows and spare > 0:
                    width += spare // len(row) + (index < spare % len(row))
                height = (
                    item.heightForWidth(width)
                    if item.hasHeightForWidth()
                    else item.sizeHint().height()
                )
                if apply:
                    item.setGeometry(QRect(QPoint(x, y), QSize(width, height)))
                x += width + across
                line = max(line, height)
            y += line + down
        return max(y - down, area.y()) - rect.y() + margins.bottom()


class WrapBar(QWidget):
    """A bar of labelled controls that wraps onto more rows as it narrows, and
    keeps the height those rows need.

    A :class:`FlowLayout` alone asks for one row's height, so a window short of
    room squeezes the rows under it out of sight; this asks for the height its
    rows take at the width it has, and asks again when the width changes.

    Controls come in groups (:meth:`add_group`), and groups can be gathered
    into titled sections (:meth:`add_section`) that sit side by side while
    there is room and wrap their own groups when there is not.
    """

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.flow = FlowLayout(self)
        self.flow.setContentsMargins(0, 0, 0, 0)
        self._height = 0
        self._captions: list[QLabel] = []

    def add_group(
        self, label: str, *widgets: QWidget, tip: str | None = None
    ) -> QWidget:
        """``widgets`` after a caption, as one item that wraps whole."""
        group = QWidget()
        row = QHBoxLayout(group)
        row.setContentsMargins(0, 0, 6, 0)
        row.setSpacing(3)
        if label:
            caption = QLabel(label)
            if tip:
                caption.setToolTip(tip)
            row.addWidget(caption)
        for widget in widgets:
            if tip and not widget.toolTip():
                widget.setToolTip(tip)
            row.addWidget(widget)
        self.flow.addWidget(group)
        return group

    def add_section(self, title: str) -> WrapBar:
        """A framed section captioned ``title``, as one item of this bar; the
        bar inside it takes the section's groups."""
        self.flow.fill_rows = True
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(frame)
        row.setContentsMargins(6, 3, 3, 3)
        row.setSpacing(8)
        caption = QLabel(title)
        font = caption.font()
        font.setBold(True)
        caption.setFont(font)
        row.addWidget(caption)
        self._captions.append(caption)
        # One caption width for every section, so the controls of sections
        # stacked on rows of their own start in one column.
        widest = max(c.sizeHint().width() for c in self._captions)
        for c in self._captions:
            c.setFixedWidth(widest)
        inner = WrapBar()
        row.addWidget(inner, 1)
        self.flow.addWidget(frame)
        return inner

    def minimumSizeHint(self) -> QSize:  # noqa: N802 - Qt override
        width = self.flow.minimumSize().width()
        return QSize(width, self.flow.heightForWidth(max(self.width(), width)))

    def sizeHint(self) -> QSize:  # noqa: N802 - Qt override
        # One row, so a section beside others asks for the room to stay whole.
        width = max(self.flow.natural_width(), self.flow.minimumSize().width())
        return QSize(width, self.flow.heightForWidth(width))

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt override
        return self.flow.heightForWidth(width)

    def event(self, event: QEvent) -> bool:
        # A row shown or hidden, or a new width, changes the rows it takes.
        if event.type() in (QEvent.Type.LayoutRequest, QEvent.Type.Resize):
            height = self.flow.heightForWidth(max(self.width(), 1))
            if height != self._height:
                self._height = height
                self.updateGeometry()
        return super().event(event)


class ModeToggle(QWidget):
    """Buttons side by side, exactly one of them down: a choice of mode that
    changes which controls around it apply.

    :attr:`chosen` fires with the datum of a button the user pressed, when
    it was not the one already down; :meth:`set_value` shows one without
    firing.

    The one down is drawn in the palette's selection colours, because a style
    marks a checked button with a slightly darker bevel that the dark theme's
    surface all but swallows. The colours come from the palette rather than a
    stylesheet, so they follow the theme like any other selection.
    """

    chosen = Signal(object)

    def __init__(
        self, choices: Sequence[tuple[str, object]], parent: QWidget | None = None
    ):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)
        self._data = [data for _, data in choices]
        self._group = QButtonGroup(self)
        for index, (label, _) in enumerate(choices):
            button = QToolButton()
            button.setText(label)
            button.setCheckable(True)
            self._group.addButton(button, index)
            row.addWidget(button)
        self._group.button(0).setChecked(True)
        self._current = 0
        self._group.idClicked.connect(self._on_click)
        self._group.idToggled.connect(lambda *_: self._paint_checked())
        self._paint_checked()

    def _on_click(self, index: int) -> None:
        # Exclusive buttons report a click on the one already down too.
        if index == self._current:
            return
        self._current = index
        self.chosen.emit(self._data[index])

    def _paint_checked(self) -> None:
        """Give the button that is down the selection colours, leaving the
        disabled group alone so a mode that cannot be picked still reads as
        unavailable."""
        base = self.palette()
        highlight = base.color(QPalette.ColorRole.Highlight)
        ink = base.color(QPalette.ColorRole.HighlightedText)
        groups = (QPalette.ColorGroup.Active, QPalette.ColorGroup.Inactive)
        for button in self._group.buttons():
            palette = QPalette(base)
            if button.isChecked():
                for group in groups:
                    palette.setColor(group, QPalette.ColorRole.Button, highlight)
                    palette.setColor(group, QPalette.ColorRole.ButtonText, ink)
            button.setPalette(palette)

    def changeEvent(self, event: QEvent) -> None:  # noqa: N802 - Qt override
        # The buttons carry palettes of their own, so a theme change reaches
        # them only by being painted again.
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange:
            self._paint_checked()

    def value(self) -> object:
        return self._data[self._group.checkedId()]

    def button(self, value: object) -> QToolButton:
        return self._group.button(self._data.index(value))

    def set_value(self, value: object) -> None:
        self._current = self._data.index(value)
        self.button(value).setChecked(True)


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


def carry_undo(window: QWidget, *actions: QAction) -> QWidget:
    """Give ``window`` the session's Undo and Redo, reachable from its fields.

    Adding the actions is not enough on its own: a focused ``QLineEdit``, text
    box or spin box claims Ctrl+Z for its own typing history and accepts the
    ``ShortcutOverride`` Qt offers it, so the window's action never fires. In a
    window whose fields are staged — typed, then applied — that history is no
    use and its claim makes Ctrl+Z look dead, so it is declined
    (:class:`_UndoKeys`) and the key reaches the actions wherever the focus is.
    """
    for action in actions:
        window.addAction(action)
    _UndoKeys.of().carry(window, actions)
    return window


class _UndoKeys(QObject):
    """The application's one filter declining the ``ShortcutOverride`` a field
    of a window that carries Undo raises for that window's keys.

    A ``ShortcutOverride`` goes to the focused widget and a filter sees only the
    object it is installed on, so catching one anywhere in a window means
    filtering the application — and every event delivered to every object in the
    application then crosses into Python here. So there is one filter however
    many windows carry the actions (:meth:`of`), and its first move is to hand
    back every event that is not a shortcut's keys.

    Each window is held weakly and dropped as Qt destroys it, and a window is
    only ever compared by identity, so nothing here outlives a window or reads
    one that has been deleted.
    """

    @classmethod
    def of(cls) -> _UndoKeys:
        """The application's filter, made the first time one is wanted; it is
        its child, so it is looked up there rather than kept in a global that
        could outlive the application it filters."""
        app = QApplication.instance()
        found = app.findChild(cls, options=Qt.FindChildOption.FindDirectChildrenOnly)
        return found if found is not None else cls()

    def __init__(self):
        app = QApplication.instance()
        super().__init__(app)
        self._carried: list[tuple[weakref.ref[QWidget], tuple[QAction, ...]]] = []
        app.installEventFilter(self)

    def carry(self, window: QWidget, actions: Sequence[QAction]) -> None:
        """Decline the keys of ``actions`` for fields of ``window``."""
        held = weakref.ref(window)
        self._carried.append((held, tuple(actions)))
        # The reference itself is what the entry is found by, so forgetting a
        # window never touches the window.
        window.destroyed.connect(partial(self._forget, held))

    def _forget(self, held: weakref.ref[QWidget], *_: object) -> None:
        self._carried = [entry for entry in self._carried if entry[0] is not held]

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        # Whatever is going on in the application arrives here, so what this
        # costs the rest of it is the one comparison on the way out.
        if event.type() != QEvent.Type.ShortcutOverride or not isinstance(obj, QWidget):
            return False
        window = obj.window()
        for held, actions in self._carried:
            if held() is not window:
                continue
            pressed = QKeySequence(event.keyCombination())
            if any(pressed in a.shortcuts() for a in actions):
                # Ignored as well as swallowed: the sender reads the accepted
                # flag, and an accepted override is what stops the shortcut.
                event.ignore()
                return True
        return False


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

    @contextmanager
    def running_as(self, run: QPushButton, verb: str) -> Iterator[None]:
        """One run started from another button, on the same Stop and progress
        line: a window with two long actions has one way out of either."""
        was_run, was_verb = self._run_button, self._verb
        self._run_button, self._verb = run, verb
        try:
            with self.running():
                yield
        finally:
            self._run_button, self._verb = was_run, was_verb

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


def wheel_steps(rest: int, delta: int) -> tuple[int, int]:
    """How many notches a wheel has turned, and what it turned short of one.

    Qt counts a notch as 120 eighths of a degree, and a high-resolution wheel
    reports less than that at a time. Flooring would swallow half a notch one
    way and round half a notch up to a whole one the other, so the count
    truncates towards zero and the remainder is kept for the next turn: pass
    it back as ``rest``.
    """
    total = rest + delta
    steps = (1 if total >= 0 else -1) * (abs(total) // 120)
    return steps, total - steps * 120


def ok_cancel(dialog: QDialog) -> QDialogButtonBox:
    """An Ok and Cancel box wired to ``dialog``."""
    box = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
    )
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


def close_box(dialog: QDialog) -> QDialogButtonBox:
    """A Close box wired to ``dialog``: Close neither accepts nor rejects, so
    either signal closes it."""
    box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    box.accepted.connect(dialog.accept)
    box.rejected.connect(dialog.reject)
    return box


def column_menu(
    view: QTableView,
    headers: Sequence[str],
    pinned: int,
    on_toggle: Callable[[int, bool], None] | None = None,
) -> QMenu:
    """A checkable entry per column of ``view``, hiding and showing it.

    ``pinned`` is the column the view is for, which stays. ``on_toggle`` is
    told of every switch, for a view that remembers its columns.
    """
    menu = QMenu(view)
    for column, name in enumerate(headers):
        action = menu.addAction(name)
        action.setCheckable(True)
        action.setChecked(not view.isColumnHidden(column))
        action.setEnabled(column != pinned)
        action.toggled.connect(
            lambda on, c=column: _toggle_column(view, c, on, on_toggle)
        )
    return menu


def _toggle_column(
    view: QTableView,
    column: int,
    shown: bool,
    on_toggle: Callable[[int, bool], None] | None,
) -> None:
    view.setColumnHidden(column, not shown)
    if on_toggle is not None:
        on_toggle(column, shown)


def install_column_menu(
    view: QTableView,
    headers: Sequence[str],
    pinned: int,
    on_toggle: Callable[[int, bool], None] | None = None,
) -> Callable[[], QMenu]:
    """Give ``view``'s header a right-click menu of its columns, and hand back
    what builds it, for a window that opens it from elsewhere."""
    header = view.horizontalHeader()

    def build() -> QMenu:
        return column_menu(view, headers, pinned, on_toggle)

    header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
    header.customContextMenuRequested.connect(
        lambda pos: build().exec(header.mapToGlobal(pos))
    )
    return build


__all__ = [
    "PICKER_WIDTH",
    "apply_wrap",
    "setting_toggle",
    "CancellableRun",
    "CommandComboBox",
    "CompactComboBox",
    "ElidedLabel",
    "EscapeCloses",
    "FlowLayout",
    "MONO_FAMILIES",
    "ModalProgress",
    "ModeToggle",
    "ResultsTable",
    "WrapBar",
    "carry_undo",
    "close_box",
    "column_menu",
    "fill_pick",
    "fit_chars",
    "hint_field",
    "install_column_menu",
    "mono_font",
    "ok_cancel",
    "select_data",
    "show_elided_tooltips",
    "wheel_steps",
]
