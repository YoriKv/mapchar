"""The Box tab's picture of the box: the page as the Preview draws it, with the
box's edges and its origin there to drag.

The fields under it say the numbers; this shows them. The right and bottom
edges and their corner resize the box, the origin mark moves where the first
character sits, and a caption over the box reads its size and origin. A drag
redraws the text as it goes and lands on the box once, on release, so a resize
is one undo step rather than one per pixel.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from enum import Enum

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QMouseEvent, QPainter, QPaintEvent, QPen
from PySide6.QtWidgets import QWidget

from mapchar.core.font import TextBox
from mapchar.core.numbers import clamp
from mapchar.ui import theme

MARGIN = 20
"""Screen pixels around the box: room for the handles on its edges and the
caption over it."""
GRIP = 6
"""How far from a handle a press still takes it, in screen pixels."""
MAX_SIDE = 1024
"""The widest and tallest a box can be, which the fields under the picture
share."""


class Handle(Enum):
    RIGHT = "right"
    BOTTOM = "bottom"
    CORNER = "corner"
    ORIGIN = "origin"


CURSORS = {
    Handle.RIGHT: Qt.CursorShape.SizeHorCursor,
    Handle.BOTTOM: Qt.CursorShape.SizeVerCursor,
    Handle.CORNER: Qt.CursorShape.SizeFDiagCursor,
    Handle.ORIGIN: Qt.CursorShape.SizeAllCursor,
}


class BoxEditor(QWidget):
    """The box drawn at the Preview's zoom, its edges and origin to drag.

    ``page`` draws the string on screen into a box, or gives ``None`` when
    there is none to draw; the paper is shown bare then.
    """

    box_changed = Signal(object)
    """A drag ended: the box with its new size or origin."""

    def __init__(
        self,
        page: Callable[[TextBox], QImage | None],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self._page = page
        self._box = TextBox()
        self._scale = 3
        self._draft: TextBox | None = None
        """The box as a drag has it so far."""
        self._drag: tuple[Handle, QPoint, TextBox] | None = None
        """The handle held, where it was taken, and the box it was taken from."""
        self._image: QImage | None = None
        self.setMouseTracking(True)
        self.setToolTip(
            "Drag the right or bottom edge to resize the box, or the origin "
            "mark to move where text starts"
        )

    def show_box(self, box: TextBox, scale: int) -> None:
        """Draw ``box`` at ``scale`` screen pixels per box pixel."""
        self._box, self._scale = box, scale
        self._refresh()

    def shown(self) -> TextBox:
        """The box on screen: the one being dragged, else the one set."""
        return self._draft if self._draft is not None else self._box

    def _refresh(self) -> None:
        box = self.shown()
        self._image = self._page(box)
        self.setMinimumSize(
            box.width * self._scale + 2 * MARGIN,
            box.height * self._scale + 2 * MARGIN,
        )
        self.update()

    # --- handles ----------------------------------------------------------

    def _rect(self) -> QRect:
        box, s = self.shown(), self._scale
        return QRect(MARGIN, MARGIN, box.width * s, box.height * s)

    def handle_point(self, handle: Handle) -> QPoint:
        """Where ``handle`` sits on screen, for a press to take it."""
        rect, box, s = self._rect(), self.shown(), self._scale
        if handle is Handle.RIGHT:
            return QPoint(rect.right() + 1, rect.center().y())
        if handle is Handle.BOTTOM:
            return QPoint(rect.center().x(), rect.bottom() + 1)
        if handle is Handle.CORNER:
            return QPoint(rect.right() + 1, rect.bottom() + 1)
        return QPoint(MARGIN + box.origin_x * s, MARGIN + box.origin_y * s)

    def _handle_at(self, pos: QPoint) -> Handle | None:
        def near(point: QPoint) -> bool:
            gap = pos - point
            return max(abs(gap.x()), abs(gap.y())) <= GRIP

        for handle in (Handle.ORIGIN, Handle.CORNER):
            if near(self.handle_point(handle)):
                return handle
        rect = self._rect()
        along_y = MARGIN - GRIP <= pos.y() <= rect.bottom() + 1 + GRIP
        along_x = MARGIN - GRIP <= pos.x() <= rect.right() + 1 + GRIP
        if abs(pos.x() - (rect.right() + 1)) <= GRIP and along_y:
            return Handle.RIGHT
        if abs(pos.y() - (rect.bottom() + 1)) <= GRIP and along_x:
            return Handle.BOTTOM
        return None

    def _dragged(self, pos: QPoint) -> TextBox:
        """The box with the held handle moved to ``pos``."""
        handle, start, box = self._drag
        dx = round((pos.x() - start.x()) / self._scale)
        dy = round((pos.y() - start.y()) / self._scale)
        if handle is Handle.ORIGIN:
            return replace(
                box,
                origin_x=clamp(box.origin_x + dx, 0, MAX_SIDE),
                origin_y=clamp(box.origin_y + dy, 0, MAX_SIDE),
            )
        width, height = box.width, box.height
        if handle in (Handle.RIGHT, Handle.CORNER):
            width = clamp(box.width + dx, 1, MAX_SIDE)
        if handle in (Handle.BOTTOM, Handle.CORNER):
            height = clamp(box.height + dy, 1, MAX_SIDE)
        return replace(box, width=width, height=height)

    # --- mouse -------------------------------------------------------------

    def mousePressEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        left = event.button() is Qt.MouseButton.LeftButton
        handle = self._handle_at(pos) if left else None
        if handle is None:
            super().mousePressEvent(event)
            return
        self._drag = (handle, pos, self._box)
        self._draft = self._box

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        pos = event.position().toPoint()
        if self._drag is None:
            handle = self._handle_at(pos)
            if handle is None:
                self.unsetCursor()
            else:
                self.setCursor(CURSORS[handle])
            return
        self._draft = self._dragged(pos)
        self._refresh()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._drag is None or event.button() is not Qt.MouseButton.LeftButton:
            super().mouseReleaseEvent(event)
            return
        landed = self._dragged(event.position().toPoint())
        self._drag = self._draft = None
        if landed != self._box:
            self.box_changed.emit(landed)
        else:
            self._refresh()

    # --- paint ---------------------------------------------------------------

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        rect, box, s = self._rect(), self.shown(), self._scale
        if self._image is not None:
            painter.drawImage(rect.topLeft(), self._image)
        else:
            painter.fillRect(rect, theme.PREVIEW_PAPER)
        accent = self.palette().highlight().color()
        guide = QColor(accent)
        guide.setAlpha(110)
        # Where each line starts: the origin's row and every line height after
        # it, so a line height that misses the game's rows shows against the
        # text.
        painter.setPen(QPen(guide))
        y = box.origin_y
        for _ in range(box.max_lines):
            y += box.line_height
            if y >= box.height:
                break
            painter.drawLine(rect.left(), MARGIN + y * s, rect.right(), MARGIN + y * s)
        painter.setPen(QPen(accent))
        painter.drawRect(rect.adjusted(-1, -1, 0, 0))
        painter.drawText(
            QRect(MARGIN, 0, max(rect.width(), 240), MARGIN - 3),
            Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
            f"{box.width} × {box.height} px",
        )
        painter.drawText(
            QRect(MARGIN, 0, max(rect.width(), 240), MARGIN - 3),
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
            f"origin {box.origin_x}, {box.origin_y}",
        )
        painter.setBrush(accent)
        for handle in Handle:
            point = self.handle_point(handle)
            edge = theme.PREVIEW_PAPER if handle is Handle.ORIGIN else accent
            painter.setPen(QPen(edge))
            painter.drawRect(QRect(point.x() - 3, point.y() - 3, 6, 6))
        painter.end()


__all__ = ["BoxEditor", "Handle"]
