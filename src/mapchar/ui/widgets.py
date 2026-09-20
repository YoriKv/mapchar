"""Widgets and widget helpers more than one window needs.

The theme is a control given less room than its text: how much it asks for
(:func:`fit_chars`, :data:`PICKER_WIDTH`), what it draws when what it has is
too little (:class:`ElidedLabel`, :class:`CompactComboBox`), and how the text
it had to cut short can still be read — a tooltip carrying the whole of it
(:func:`show_elided_tooltips`, :func:`hint_field`). The rest are the small
controls and fillers more than one window builds the same way. Bars that wrap
live in :mod:`mapchar.ui.bars`, and long work's progress in
:mod:`mapchar.ui.progress`; ``docs/ui.md`` holds the rules they all implement.
"""

from __future__ import annotations

import weakref
from functools import partial
from typing import TYPE_CHECKING, TypeVar

from PySide6.QtCore import QEvent, QObject, QSize, Qt, Signal
from PySide6.QtGui import QAction, QFont, QKeySequence, QPalette, QTextOption
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QPlainTextEdit,
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
    from collections.abc import Callable, Iterable, Sequence

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
"""The closed width a picker takes unless it states its own: one number, so a
bar of them reads as a row whatever names the registry and the project give
their items. A picker whose items are shorter or longer than that — a byte
order, a summary line — passes the width it needs to :class:`CompactComboBox`
instead."""


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


def focus_field(field: QLineEdit) -> None:
    """Put the keyboard in a field with what it holds selected, so what is
    typed next replaces it rather than being appended to it.

    Every one of these is reached from a shortcut or a double-click, so the
    focus is a shortcut's however it was asked for: a field styled for one
    looks the same wherever it is opened from.
    """
    field.setFocus(Qt.FocusReason.ShortcutFocusReason)
    field.selectAll()


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
    "CommandComboBox",
    "CompactComboBox",
    "ElidedLabel",
    "EscapeCloses",
    "MONO_FAMILIES",
    "ModeToggle",
    "ResultsTable",
    "carry_undo",
    "close_box",
    "column_menu",
    "fill_pick",
    "fit_chars",
    "focus_field",
    "hint_field",
    "install_column_menu",
    "mono_font",
    "ok_cancel",
    "select_data",
    "show_elided_tooltips",
    "wheel_steps",
]
