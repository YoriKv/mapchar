"""The Preview window: a string drawn through a font into its text box."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QPalette, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.font import CodeEffect, Effect, TextBox
from mapchar.core.tokens import Token
from mapchar.engines.layout import Layout, layout, unspellable, with_code_effects
from mapchar.ui.font_tab import FontTab
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.number_fields import number_spin
from mapchar.ui.preview_font import preview_font
from mapchar.ui.preview_render import render
from mapchar.ui.tool_window import ToolWindow
from mapchar.ui.widgets import ElidedLabel, show_elided_tooltips


class PreviewWindow(ThemedIcons, ToolWindow):
    font_changed = Signal()
    """The app's preview font changed: redraw whatever was drawn through it."""
    box_changed = Signal(object)
    """A new TextBox value for the current block."""
    wrap_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__("Preview", "preview_window", (640, 480), parent)
        self._box = TextBox()
        self._result: Layout | None = None
        self._source: list[Token] | str | None = None
        """The tokens or script text being previewed; ``None`` until one is."""
        self._page = 0
        self._labels: list[str] = []
        self._defaults: dict[str, CodeEffect] = {}
        """What each code does before the box says otherwise: the effects its
        table entry declares, and the block's line code."""
        self._syncing = False
        layout_ = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout_.addWidget(self.tabs, 1)

        # Preview tab.
        preview = QWidget()
        pv = QVBoxLayout(preview)
        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        # Scrolled, so a big box at a high zoom is panned rather than setting the
        # window's minimum size.
        canvas_scroll = QScrollArea()
        canvas_scroll.setWidget(self.canvas)
        canvas_scroll.setWidgetResizable(True)
        pv.addWidget(canvas_scroll, 1)
        # The status has a line of its own: it is the part of the tab that says
        # whether the string fits, and beside the buttons it had no room.
        self.status = ElidedLabel("")
        pv.addWidget(self.status)
        row = QHBoxLayout()
        self.prev = QPushButton("Page")
        self.next = QPushButton("Page")
        self.prev.setToolTip("Previous page")
        self.next.setToolTip("Next page")
        self.next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._bake_icons()
        self.zoom = number_spin(1, 8, 1, value=3)
        self.grid = QPushButton("Grid")
        self.grid.setCheckable(True)
        self.grid.setToolTip("Draw a pixel grid (zoom 3 or more)")
        self.zoom.setToolTip("Pixels on screen per pixel of the box")
        self.wrap = QPushButton("Wrap Translation")
        self.wrap.setToolTip(
            "Re-break the selected translations to fit the box (needs a newline code)"
        )
        self.copy = QPushButton("Copy Image")
        self.copy.setToolTip("Put the page as drawn on the clipboard")
        row.addWidget(self.prev)
        row.addWidget(self.next)
        row.addWidget(QLabel("Zoom"))
        row.addWidget(self.zoom)
        row.addWidget(self.grid)
        row.addStretch(1)
        row.addWidget(self.wrap)
        row.addWidget(self.copy)
        pv.addLayout(row)
        self.readout = ElidedLabel("")
        self.readout.setToolTip("Bytes the draft encodes to, of the room it has")
        pv.addWidget(self.readout)
        self.tabs.addTab(preview, "Preview")

        # Font tab.
        self.font_tab = FontTab()
        self.font_tab.font_changed.connect(self._on_font_changed)
        self.tabs.addTab(self.font_tab, "Font")

        # Box tab.
        box_tab = QWidget()
        bf = QFormLayout(box_tab)
        self.box_w = number_spin(1, 1024, 3)
        self.box_h = number_spin(1, 1024, 3)
        self.line_h = number_spin(1, 128, 2)
        self.spacing = number_spin(-8, 32, 2)
        self.lines = number_spin(0, 64, 2, special="fit")
        self.chars = number_spin(0, 999, 3, special="off")
        self.chars.setToolTip(
            "Characters a line holds: overflows box and Wrap then count "
            "characters instead of measuring them"
        )
        self.origin_x = number_spin(0, 1024, 3)
        self.origin_y = number_spin(0, 1024, 3)
        for label, w in (
            ("Width", self.box_w),
            ("Height", self.box_h),
            ("Line height", self.line_h),
            ("Letter spacing", self.spacing),
            ("Lines per page", self.lines),
            ("Chars per line", self.chars),
            ("Origin X", self.origin_x),
            ("Origin Y", self.origin_y),
        ):
            bf.addRow(label, w)
        self.tabs.addTab(box_tab, "Box")

        # Codes tab.
        codes_tab = QWidget()
        cv = QVBoxLayout(codes_tab)
        self.codes = QTableWidget(0, 3)
        self.codes.setHorizontalHeaderLabels(["Code", "Effect", "Value"])
        self.codes.horizontalHeader().setStretchLastSection(True)
        show_elided_tooltips(self.codes)
        cv.addWidget(self.codes)
        self.tabs.addTab(codes_tab, "Codes")

        self.prev.clicked.connect(lambda: self._set_page(self._page - 1))
        self.next.clicked.connect(lambda: self._set_page(self._page + 1))
        self.zoom.valueChanged.connect(lambda _: self._paint())
        self.grid.toggled.connect(lambda _: self._paint())
        self.wrap.clicked.connect(self.wrap_requested)
        self.copy.clicked.connect(self._copy)
        for w in (
            self.box_w,
            self.box_h,
            self.line_h,
            self.spacing,
            self.lines,
            self.chars,
            self.origin_x,
            self.origin_y,
        ):
            w.valueChanged.connect(lambda _: self._emit_box())
        self.codes.itemChanged.connect(lambda _: self._emit_box())

    # --- state -----------------------------------------------------------

    def _bake_icons(self) -> None:
        """The page arrows in the theme's button-text color."""
        role = QPalette.ColorRole.ButtonText
        self.prev.setIcon(themed_icon(self, Glyph.ARROW_LEFT, role))
        self.next.setIcon(themed_icon(self, Glyph.ARROW_RIGHT, role))

    def _on_font_changed(self) -> None:
        """The Font tab picked another family: redraw, and tell the window so
        every other surface measured through it catches up."""
        self._paint()
        self.font_changed.emit()

    def update_box(self, box: TextBox) -> None:
        """A new value for the box the fields already show: redraw only."""
        self._box = box
        self._paint()

    def set_box(
        self,
        box: TextBox,
        labels: list[str],
        defaults: dict[str, CodeEffect] | None = None,
    ) -> None:
        """Show ``box``, with a row per code in ``labels``; ``defaults`` is
        what each code does where the box sets nothing for it."""
        self._box = box
        self._labels = labels
        self._defaults = dict(defaults or {})
        self._syncing = True
        try:
            self.box_w.setValue(box.width)
            self.box_h.setValue(box.height)
            self.line_h.setValue(box.line_height)
            self.spacing.setValue(box.letter_spacing)
            self.lines.setValue(box.lines_per_page)
            self.chars.setValue(box.chars_per_line)
            self.origin_x.setValue(box.origin_x)
            self.origin_y.setValue(box.origin_y)
            self.codes.setRowCount(len(labels))
            for r, label in enumerate(labels):
                effect = box.effects.get(label, self._defaults.get(label, CodeEffect()))
                item = QTableWidgetItem(label)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.codes.setItem(r, 0, item)
                combo = QComboBox()
                combo.addItems([e.value for e in Effect])
                combo.setCurrentText(effect.effect.value)
                if label in self._defaults:
                    combo.setToolTip(
                        f"Left to its table or the line code, it is "
                        f"{self._defaults[label].effect.value}; a pick here "
                        "overrides that for this block"
                    )
                combo.currentIndexChanged.connect(lambda _: self._emit_box())
                self.codes.setCellWidget(r, 1, combo)
                self.codes.setItem(r, 2, QTableWidgetItem(str(effect.value)))
        finally:
            self._syncing = False

    def show_string(self, source: list[Token] | str, title: str) -> None:
        self.setWindowTitle(f"Preview — {title}")
        self._source = source
        self._page = 0
        self._paint()

    def _paint(self) -> None:
        source = self._source
        if source is None:
            self.canvas.clear()
            self.status.setText("Select a string to preview.")
            return
        chosen = preview_font()
        font = chosen.measured(source)
        box = with_code_effects(self._box, self._defaults)
        result = self._result = layout(source, font, box)
        image = render(
            result,
            font,
            box,
            chosen.qfont,
            self._page,
            self.zoom.value(),
            self.grid.isChecked(),
        )
        self.canvas.setPixmap(QPixmap.fromImage(image))
        parts = [f"page {self._page + 1}/{result.pages}"]
        if result.overflow_width:
            parts.append("too wide")
        if result.overflow_lines:
            parts.append("too many lines")
        missing = unspellable(source, font)
        if missing:
            parts.append(f"{len(missing)} not in font: " + ", ".join(missing[:8]))
        self.status.setToolTip(
            "The font has no glyph for: " + ", ".join(missing) if missing else ""
        )
        self.status.setText("  ·  ".join(parts))
        self.prev.setEnabled(self._page > 0)
        self.next.setEnabled(self._page + 1 < result.pages)

    def set_readout(self, text: str) -> None:
        """The byte budget of the draft being typed, from the window."""
        self.readout.setText(text)

    def overflows(self) -> bool:
        return bool(self._result and self._result.overflows)

    def _set_page(self, page: int) -> None:
        if self._result is None:
            return
        self._page = max(0, min(page, self._result.pages - 1))
        self._paint()

    def _copy(self) -> None:
        pix = self.canvas.pixmap()
        if pix is not None and not pix.isNull():
            QApplication.clipboard().setPixmap(pix)

    # --- edits -------------------------------------------------------------

    def current_box(self) -> TextBox:
        effects = {}
        for r in range(self.codes.rowCount()):
            label = self.codes.item(r, 0).text()
            combo = self.codes.cellWidget(r, 1)
            value_item = self.codes.item(r, 2)
            value = (
                int(value_item.text())
                if value_item and value_item.text().strip().lstrip("-").isdigit()
                else 0
            )
            eff = Effect(combo.currentText()) if combo else Effect.NONE
            # Only what differs from what the table already says is the box's.
            chosen = CodeEffect(eff, value)
            if chosen != self._defaults.get(label, CodeEffect()):
                effects[label] = chosen
        return replace(
            self._box,
            width=self.box_w.value(),
            height=self.box_h.value(),
            line_height=self.line_h.value(),
            letter_spacing=self.spacing.value(),
            lines_per_page=self.lines.value(),
            chars_per_line=self.chars.value(),
            origin_x=self.origin_x.value(),
            origin_y=self.origin_y.value(),
            effects=effects,
        )

    def _emit_box(self) -> None:
        if self._syncing:
            return
        self.box_changed.emit(self.current_box())


__all__ = ["PreviewWindow"]
