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

from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.core.tokens import Token
from mapchar.engines.layout import Layout, layout, unspellable
from mapchar.ui.font_tab import FontTab
from mapchar.ui.glyph_sheet import GlyphSheet
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.number_fields import number_spin
from mapchar.ui.widgets import ElidedLabel, EscapeCloses, show_elided_tooltips
from mapchar.ui.window_layout import remember_layout


class PreviewWindow(EscapeCloses, ThemedIcons, QWidget):
    font_changed = Signal(object)
    """A new Font value for the bound font entry."""
    box_changed = Signal(object)
    """A new TextBox value for the current block."""
    wrap_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Preview")
        # Size and position remembered between runs, like every tool
        # window (:mod:`mapchar.ui.window_layout`).
        self._layout = remember_layout(self, "preview_window")
        self._font: Font | None = None
        self._box = TextBox()
        self._sheet: GlyphSheet | None = None
        self._result: Layout | None = None
        self._source: list[Token] | str | None = None
        """The tokens or script text being previewed; ``None`` until one is."""
        self._page = 0
        self._labels: list[str] = []
        self._table_chars = ""
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
        self.font_tab.font_changed.connect(self.font_changed)
        self.tabs.addTab(self.font_tab, "Font")

        # Box tab.
        box_tab = QWidget()
        bf = QFormLayout(box_tab)
        self.box_w = number_spin(1, 1024, 3)
        self.box_h = number_spin(1, 1024, 3)
        self.line_h = number_spin(1, 128, 2)
        self.spacing = number_spin(-8, 32, 2)
        self.lines = number_spin(0, 64, 2, special="fit")
        self.origin_x = number_spin(0, 1024, 3)
        self.origin_y = number_spin(0, 1024, 3)
        for label, w in (
            ("Width", self.box_w),
            ("Height", self.box_h),
            ("Line height", self.line_h),
            ("Letter spacing", self.spacing),
            ("Lines per page", self.lines),
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
            self.origin_x,
            self.origin_y,
        ):
            w.valueChanged.connect(lambda _: self._emit_box())
        self.codes.itemChanged.connect(lambda _: self._emit_box())
        self.resize(640, 480)

    # --- state -----------------------------------------------------------

    def _bake_icons(self) -> None:
        """The page arrows in the theme's button-text color."""
        role = QPalette.ColorRole.ButtonText
        self.prev.setIcon(themed_icon(self, Glyph.ARROW_LEFT, role))
        self.next.setIcon(themed_icon(self, Glyph.ARROW_RIGHT, role))

    def set_font(self, font: Font | None) -> None:
        """Show ``font``: the Font tab's fields, and the preview through it."""
        self.font_tab.set_font(font)
        self._font, self._sheet = font, self.font_tab.sheet
        self._paint()

    def update_font(self, font: Font | None) -> None:
        """A new value for the font the fields already show: redraw only."""
        self.font_tab.update_font(font)
        self._font, self._sheet = font, self.font_tab.sheet
        self._paint()

    def set_table_chars(self, chars: str) -> None:
        """The start table's single-character text, in key order, for Fill."""
        self.font_tab.set_table_chars(chars)

    def update_box(self, box: TextBox) -> None:
        """A new value for the box the fields already show: redraw only."""
        self._box = box
        self._paint()

    def set_box(self, box: TextBox, labels: list[str]) -> None:
        self._box = box
        self._labels = labels
        self._syncing = True
        try:
            self.box_w.setValue(box.width)
            self.box_h.setValue(box.height)
            self.line_h.setValue(box.line_height)
            self.spacing.setValue(box.letter_spacing)
            self.lines.setValue(box.lines_per_page)
            self.origin_x.setValue(box.origin_x)
            self.origin_y.setValue(box.origin_y)
            self.codes.setRowCount(len(labels))
            for r, label in enumerate(labels):
                effect = box.effects.get(label, CodeEffect())
                item = QTableWidgetItem(label)
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.codes.setItem(r, 0, item)
                combo = QComboBox()
                combo.addItems([e.value for e in Effect])
                combo.setCurrentText(effect.effect.value)
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
        if self._font is None or self._sheet is None or source is None:
            self.canvas.clear()
            self.status.setText("Bind a font to the block (Font tab).")
            return
        self._result = layout(source, self._font, self._box)
        image = self._sheet.render(
            self._result,
            self._box,
            self._page,
            self.zoom.value(),
            self.grid.isChecked(),
        )
        self.canvas.setPixmap(QPixmap.fromImage(image))
        parts = [f"page {self._page + 1}/{self._result.pages}"]
        if self._result.overflow_width:
            parts.append("too wide")
        if self._result.overflow_lines:
            parts.append("too many lines")
        if not self._sheet.ok:
            parts.append("sheet not found")
        missing = unspellable(source, self._font)
        if missing:
            parts.append(f"{len(missing)} not in font: " + ", ".join(missing[:8]))
        self.status.setToolTip(
            "The font has no glyph for: " + ", ".join(missing) if missing else ""
        )
        self.status.setText("  ·  ".join(parts))
        self.prev.setEnabled(self._page > 0)
        self.next.setEnabled(self._page + 1 < self._result.pages)

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
            if eff is not Effect.NONE:
                effects[label] = CodeEffect(eff, value)
        return replace(
            self._box,
            width=self.box_w.value(),
            height=self.box_h.value(),
            line_height=self.line_h.value(),
            letter_spacing=self.spacing.value(),
            lines_per_page=self.lines.value(),
            origin_x=self.origin_x.value(),
            origin_y=self.origin_y.value(),
            effects=effects,
        )

    def _emit_box(self) -> None:
        if self._syncing:
            return
        self.box_changed.emit(self.current_box())


__all__ = ["PreviewWindow"]
