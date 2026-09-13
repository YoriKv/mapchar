"""The Preview window: a string drawn through a font into its text box."""

from __future__ import annotations

from PySide6.QtCore import QEvent, QRect, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPalette, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.engines.layout import Layout, layout
from mapchar.ui import theme
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import glyph_icon


class GlyphSheet:
    """A PNG cut into cells; draws glyphs into a QImage."""

    def __init__(self, font: Font):
        self.font = font
        self.image = QImage(font.path) if font.path else QImage()
        self.ok = not self.image.isNull()
        self.transparent: QColor | None = None
        if self.ok:
            if (
                font.transparent is not None
                and self.image.format() == QImage.Format.Format_Indexed8
            ):
                table = self.image.colorTable()
                if font.transparent < len(table):
                    self.transparent = QColor(table[font.transparent])
            elif self.ok:
                self.transparent = QColor(self.image.pixelColor(0, 0))

    def cell(self, glyph: int) -> QRect:
        col, row = glyph % self.font.columns, glyph // self.font.columns
        return QRect(
            col * self.font.cell_width,
            row * self.font.cell_height,
            self.font.cell_width,
            self.font.cell_height,
        )

    def measured_widths(self, gap: int = 1) -> tuple[int, ...]:
        """Advance per glyph: the last inked column plus a gap."""
        if not self.ok:
            return ()
        widths = []
        rows = self.image.height() // self.font.cell_height
        for glyph in range(rows * self.font.columns):
            rect = self.cell(glyph)
            last = -1
            for x in range(rect.width()):
                for y in range(rect.height()):
                    c = self.image.pixelColor(rect.x() + x, rect.y() + y)
                    if self.transparent is None or c != self.transparent:
                        last = x
                        break
            widths.append(last + 1 + gap if last >= 0 else self.font.cell_width // 2)
        return tuple(widths)

    def render(self, result: Layout, box: TextBox, page: int, scale: int) -> QImage:
        out = QImage(
            max(box.width, 1) * scale,
            max(box.height, 1) * scale,
            QImage.Format.Format_ARGB32,
        )
        out.fill(QColor(24, 24, 28))
        painter = QPainter(out)
        for p in result.placements:
            if p.page != page or p.glyph is None:
                if p.page == page and p.glyph is None:
                    painter.setPen(QPen(theme.ERROR_INK))
                    painter.drawRect(
                        QRect(
                            p.x * scale,
                            p.y * scale,
                            self.font.cell_width * scale - 1,
                            self.font.cell_height * scale - 1,
                        )
                    )
                continue
            src = self.cell(p.glyph)
            if self.ok:
                tile = self.image.copy(src).convertToFormat(QImage.Format.Format_ARGB32)
                if self.transparent is not None:
                    for y in range(tile.height()):
                        for x in range(tile.width()):
                            if QColor(tile.pixelColor(x, y)) == self.transparent:
                                tile.setPixelColor(x, y, QColor(0, 0, 0, 0))
                dest = QRect(
                    p.x * scale, p.y * scale, src.width() * scale, src.height() * scale
                )
                painter.drawImage(dest, tile)
            if p.overflow:
                painter.fillRect(
                    QRect(
                        p.x * scale,
                        p.y * scale,
                        src.width() * scale,
                        src.height() * scale,
                    ),
                    theme.TINT_END,
                )
        painter.end()
        return out


class PreviewWindow(QWidget):
    font_changed = Signal(object)
    """A new Font value for the bound font entry."""
    box_changed = Signal(object)
    """A new TextBox value for the current block."""
    wrap_requested = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent, Qt.WindowType.Window)
        self.setWindowTitle("Preview")
        self._font: Font | None = None
        self._box = TextBox()
        self._sheet: GlyphSheet | None = None
        self._result: Layout | None = None
        self._page = 0
        self._labels: list[str] = []
        self._syncing = False
        layout_ = QVBoxLayout(self)
        self.tabs = QTabWidget()
        layout_.addWidget(self.tabs, 1)

        # Preview tab.
        preview = QWidget()
        pv = QVBoxLayout(preview)
        self.canvas = QLabel()
        self.canvas.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        pv.addWidget(self.canvas, 1)
        row = QHBoxLayout()
        self.status = QLabel("")
        self.prev = QPushButton("Page")
        self.next = QPushButton("Page")
        self.prev.setToolTip("Previous page")
        self.next.setToolTip("Next page")
        self.next.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self._bake_icons()
        self.zoom = QSpinBox()
        self.zoom.setRange(1, 8)
        self.zoom.setValue(3)
        self.wrap = QPushButton("Wrap translation")
        self.copy = QPushButton("Copy image")
        row.addWidget(self.status, 1)
        row.addWidget(self.prev)
        row.addWidget(self.next)
        row.addWidget(QLabel("Zoom"))
        row.addWidget(self.zoom)
        row.addWidget(self.wrap)
        row.addWidget(self.copy)
        pv.addLayout(row)
        self.tabs.addTab(preview, "Preview")

        # Font tab.
        font_tab = QWidget()
        ff = QFormLayout(font_tab)
        self.font_path = QLineEdit()
        self.font_browse = QPushButton("Browse…")
        pr = QHBoxLayout()
        pr.addWidget(self.font_path, 1)
        pr.addWidget(self.font_browse)
        ff.addRow("Sheet (PNG)", pr)
        self.cell_w = QSpinBox()
        self.cell_w.setRange(1, 64)
        self.cell_h = QSpinBox()
        self.cell_h.setRange(1, 64)
        self.columns = QSpinBox()
        self.columns.setRange(1, 256)
        self.base = QSpinBox()
        self.base.setRange(0, 65535)
        self.chars = QLineEdit()
        self.chars.setPlaceholderText("characters in glyph order from the base glyph")
        self.space = QSpinBox()
        self.space.setRange(0, 64)
        self.missing = QSpinBox()
        self.missing.setRange(-1, 65535)
        self.measure = QPushButton("Measure widths from sheet")
        self.fixed = QPushButton("Fixed width")
        ff.addRow("Cell width", self.cell_w)
        ff.addRow("Cell height", self.cell_h)
        ff.addRow("Columns", self.columns)
        ff.addRow("Base glyph", self.base)
        ff.addRow("Characters", self.chars)
        ff.addRow("Space width", self.space)
        ff.addRow("Missing glyph (-1: box)", self.missing)
        wr = QHBoxLayout()
        wr.addWidget(self.measure)
        wr.addWidget(self.fixed)
        ff.addRow("Widths", wr)
        self.glyph_map = QTableWidget(0, 2)
        self.glyph_map.setHorizontalHeaderLabels(["Text or [code]", "Glyph"])
        self.glyph_add = QPushButton("Add mapping")
        ff.addRow("Overrides", self.glyph_map)
        ff.addRow("", self.glyph_add)
        self.tabs.addTab(font_tab, "Font")

        # Box tab.
        box_tab = QWidget()
        bf = QFormLayout(box_tab)
        self.box_w = QSpinBox()
        self.box_w.setRange(1, 1024)
        self.box_h = QSpinBox()
        self.box_h.setRange(1, 1024)
        self.line_h = QSpinBox()
        self.line_h.setRange(1, 128)
        self.spacing = QSpinBox()
        self.spacing.setRange(-8, 32)
        self.lines = QSpinBox()
        self.lines.setRange(0, 64)
        self.origin_x = QSpinBox()
        self.origin_x.setRange(0, 1024)
        self.origin_y = QSpinBox()
        self.origin_y.setRange(0, 1024)
        for label, w in (
            ("Width", self.box_w),
            ("Height", self.box_h),
            ("Line height", self.line_h),
            ("Letter spacing", self.spacing),
            ("Lines per page (0: fit)", self.lines),
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
        cv.addWidget(self.codes)
        self.tabs.addTab(codes_tab, "Codes")

        self.prev.clicked.connect(lambda: self._set_page(self._page - 1))
        self.next.clicked.connect(lambda: self._set_page(self._page + 1))
        self.zoom.valueChanged.connect(lambda _: self._paint())
        self.wrap.clicked.connect(self.wrap_requested)
        self.copy.clicked.connect(self._copy)
        self.font_browse.clicked.connect(self._browse)
        for w in (
            self.cell_w,
            self.cell_h,
            self.columns,
            self.base,
            self.space,
            self.missing,
        ):
            w.valueChanged.connect(lambda _: self._emit_font())
        self.chars.editingFinished.connect(self._emit_font)
        self.font_path.editingFinished.connect(self._emit_font)
        self.measure.clicked.connect(self._measure)
        self.fixed.clicked.connect(self._fixed)
        self.glyph_add.clicked.connect(self._add_mapping)
        self.glyph_map.itemChanged.connect(lambda _: self._emit_font())
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
        """The page arrows in the theme's button-text color; pixmaps, so they
        are re-baked on a palette change."""
        color = self.palette().color(QPalette.ColorRole.ButtonText)
        ratio = self.devicePixelRatioF()
        self.prev.setIcon(glyph_icon(Glyph.ARROW_LEFT, color, ratio=ratio))
        self.next.setIcon(glyph_icon(Glyph.ARROW_RIGHT, color, ratio=ratio))

    def changeEvent(self, event) -> None:
        super().changeEvent(event)
        if event.type() is QEvent.Type.PaletteChange:
            self._bake_icons()

    def set_font(self, font: Font | None) -> None:
        self._font = font
        self._sheet = GlyphSheet(font) if font is not None else None
        self._syncing = True
        try:
            f = font or Font(None)
            self.font_path.setText(f.path or "")
            self.cell_w.setValue(f.cell_width)
            self.cell_h.setValue(f.cell_height)
            self.columns.setValue(f.columns)
            self.base.setValue(f.base)
            self.chars.setText(f.chars)
            self.space.setValue(f.space if f.space is not None else 0)
            self.missing.setValue(f.missing if f.missing is not None else -1)
            self.glyph_map.setRowCount(len(f.glyphs))
            for r, (text, glyph) in enumerate(f.glyphs.items()):
                self.glyph_map.setItem(r, 0, QTableWidgetItem(text))
                self.glyph_map.setItem(r, 1, QTableWidgetItem(str(glyph)))
        finally:
            self._syncing = False
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

    def show_string(self, source, title: str) -> None:
        self.setWindowTitle(f"Preview — {title}")
        self._source = source
        self._page = 0
        self._paint()

    def _paint(self) -> None:
        source = getattr(self, "_source", None)
        if self._font is None or self._sheet is None or source is None:
            self.canvas.clear()
            self.status.setText("Bind a font to the block (Font tab).")
            return
        self._result = layout(source, self._font, self._box)
        image = self._sheet.render(
            self._result, self._box, self._page, self.zoom.value()
        )
        from PySide6.QtGui import QPixmap

        self.canvas.setPixmap(QPixmap.fromImage(image))
        parts = [f"page {self._page + 1}/{self._result.pages}"]
        if self._result.overflow_width:
            parts.append("too wide")
        if self._result.overflow_lines:
            parts.append("too many lines")
        if not self._sheet.ok:
            parts.append("sheet not found")
        self.status.setText("  ·  ".join(parts))
        self.prev.setEnabled(self._page > 0)
        self.next.setEnabled(self._page + 1 < self._result.pages)

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

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Glyph sheet", "", "Images (*.png *.bmp)"
        )
        if path:
            self.font_path.setText(path)
            self._emit_font()

    def current_font(self) -> Font:
        f = self._font or Font(None)
        glyphs = {}
        for r in range(self.glyph_map.rowCount()):
            a, b = self.glyph_map.item(r, 0), self.glyph_map.item(r, 1)
            if (
                a is not None
                and b is not None
                and a.text()
                and b.text().strip().isdigit()
            ):
                glyphs[a.text()] = int(b.text())
        from dataclasses import replace

        return replace(
            f,
            path=self.font_path.text() or None,
            cell_width=self.cell_w.value(),
            cell_height=self.cell_h.value(),
            columns=self.columns.value(),
            base=self.base.value(),
            chars=self.chars.text(),
            glyphs=glyphs,
            space=self.space.value() or None,
            missing=None if self.missing.value() < 0 else self.missing.value(),
        )

    def _emit_font(self) -> None:
        if self._syncing:
            return
        self.font_changed.emit(self.current_font())

    def _measure(self) -> None:
        font = self.current_font()
        sheet = GlyphSheet(font)
        self.font_changed.emit(font.with_widths(sheet.measured_widths()))

    def _fixed(self) -> None:
        self.font_changed.emit(self.current_font().with_widths(()))

    def _add_mapping(self) -> None:
        r = self.glyph_map.rowCount()
        self.glyph_map.insertRow(r)
        self.glyph_map.setItem(r, 0, QTableWidgetItem("[code]"))
        self.glyph_map.setItem(r, 1, QTableWidgetItem("0"))

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
        from dataclasses import replace

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


__all__ = ["GlyphSheet", "PreviewWindow"]
