"""Layouts for a row of controls whose count is not fixed.

What a bar does when its room runs out: :class:`FlowLayout` wraps its items
onto as many rows as the width needs, :data:`ROW_BREAK` holds a row apart, and
:class:`WrapBar` keeps the height those rows take. ``docs/ui.md`` holds the
rules they implement.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QPoint, QRect, QSize, Qt
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QLayoutItem,
    QStyle,
    QWidget,
)

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


__all__ = ["ROW_BREAK", "FlowLayout", "WrapBar"]
