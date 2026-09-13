"""The Preview window: a string drawn through a font into its text box."""

from __future__ import annotations

from PySide6.QtCore import QRect, Qt, Signal
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
    QScrollArea,
    QSpinBox,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.font import CodeEffect, Effect, Font, TextBox
from mapchar.core.text import char_units
from mapchar.core.tokens import Token
from mapchar.engines.layout import Layout, layout, unspellable
from mapchar.engines.relsearch import HIRAGANA, KATAKANA, RUNS
from mapchar.ui import theme
from mapchar.ui.glyphs import Glyph
from mapchar.ui.icon_font import ThemedIcons, themed_icon
from mapchar.ui.window_layout import remember_layout


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

    def render(
        self,
        result: Layout,
        box: TextBox,
        page: int,
        scale: int,
        grid: bool = False,
    ) -> QImage:
        out = QImage(
            max(box.width, 1) * scale,
            max(box.height, 1) * scale,
            QImage.Format.Format_ARGB32,
        )
        out.fill(theme.PREVIEW_PAPER)
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
        if grid and scale >= 3:
            painter.setPen(QPen(theme.PREVIEW_GRID))
            for x in range(0, box.width + 1):
                painter.drawLine(x * scale, 0, x * scale, box.height * scale)
            for y in range(0, box.height + 1):
                painter.drawLine(0, y * scale, box.width * scale, y * scale)
        painter.end()
        return out


class GlyphSheetView(QWidget):
    """The sheet as a grid of cells, each captioned with what it spells.

    Clicking picks one tile, or a whole row when ``rows`` is on; the pick is
    where Fill lays its characters and what Shift moves.
    """

    picked = Signal(int, int)
    """The first and last glyph index of the pick."""

    CAPTION = 14
    """Pixels under each cell for its character."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._font: Font | None = None
        self._sheet: GlyphSheet | None = None
        self._spelling: dict[int, str] = {}
        self._rows = 0
        self.scale = 3
        self.rows = False
        self.first = 0
        self.last = 0

    def set_font(self, font: Font | None, sheet: GlyphSheet | None) -> None:
        self._font, self._sheet = font, sheet
        self._spelling = {}
        if font is not None:
            for i, ch in enumerate(font.units):
                self._spelling[font.base + i] = ch
            for text, glyph in font.glyphs.items():
                self._spelling[glyph] = text
        self._rows = 0
        if font is not None and sheet is not None and sheet.ok:
            self._rows = max(1, sheet.image.height() // max(font.cell_height, 1))
        elif font is not None:
            highest = max(self._spelling, default=0)
            self._rows = highest // max(font.columns, 1) + 1
        self.updateGeometry()
        self.resize(self.sizeHint())
        self.update()

    # --- geometry ---------------------------------------------------------

    def _cell_size(self) -> tuple[int, int]:
        f = self._font or Font(None)
        return f.cell_width * self.scale, f.cell_height * self.scale + self.CAPTION

    def sizeHint(self):
        from PySide6.QtCore import QSize

        f = self._font or Font(None)
        w, h = self._cell_size()
        return QSize(w * f.columns + 1, h * max(self._rows, 1) + 1)

    def minimumSizeHint(self):
        return self.sizeHint()

    def glyph_at(self, x: int, y: int) -> int | None:
        f = self._font
        if f is None:
            return None
        w, h = self._cell_size()
        col, row = x // w, y // h
        if col < 0 or col >= f.columns or row < 0 or row >= self._rows:
            return None
        return row * f.columns + col

    def set_pick(self, first: int, last: int | None = None) -> None:
        self.first = first
        self.last = first if last is None else last
        self.update()
        self.picked.emit(self.first, self.last)

    def mousePressEvent(self, event) -> None:
        glyph = self.glyph_at(int(event.position().x()), int(event.position().y()))
        if glyph is None:
            return
        f = self._font
        if self.rows and f is not None:
            start = glyph - glyph % f.columns
            self.set_pick(start, start + f.columns - 1)
        else:
            self.set_pick(glyph)

    # --- painting ---------------------------------------------------------

    def paintEvent(self, event) -> None:
        f = self._font
        painter = QPainter(self)
        painter.fillRect(self.rect(), self.palette().base())
        if f is None:
            painter.end()
            return
        w, h = self._cell_size()
        tile_h = f.cell_height * self.scale
        highlight = self.palette().highlight().color()
        grid = QColor(highlight)
        grid.setAlpha(60)
        for glyph in range(self._rows * f.columns):
            col, row = glyph % f.columns, glyph // f.columns
            x, y = col * w, row * h
            if self._sheet is not None and self._sheet.ok:
                src = self._sheet.cell(glyph)
                painter.drawImage(QRect(x, y, w, tile_h), self._sheet.image, src)
            painter.setPen(QPen(grid))
            painter.drawRect(QRect(x, y, w, tile_h))
            text = self._spelling.get(glyph, "")
            if text:
                painter.setPen(QPen(self.palette().text().color()))
                painter.drawText(
                    QRect(x, y + tile_h, w, self.CAPTION),
                    Qt.AlignmentFlag.AlignCenter,
                    text if len(text) <= 3 else text[:2] + "…",
                )
            if self.first <= glyph <= self.last:
                painter.setPen(QPen(highlight, 2))
                painter.drawRect(QRect(x + 1, y + 1, w - 2, h - 2))
        painter.end()


class PreviewWindow(ThemedIcons, QWidget):
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
        self.grid = QPushButton("Grid")
        self.grid.setCheckable(True)
        self.grid.setToolTip("Rule the box in pixels")
        self.wrap = QPushButton("Wrap translation")
        self.copy = QPushButton("Copy image")
        row.addWidget(self.status, 1)
        row.addWidget(self.prev)
        row.addWidget(self.next)
        row.addWidget(QLabel("Zoom"))
        row.addWidget(self.zoom)
        row.addWidget(self.grid)
        row.addWidget(self.wrap)
        row.addWidget(self.copy)
        pv.addLayout(row)
        self.readout = QLabel("")
        self.readout.setToolTip("What the draft encodes to, against the room it has")
        pv.addWidget(self.readout)
        self.tabs.addTab(preview, "Preview")

        # Font tab: the fields above, the sheet below.
        font_tab = QWidget()
        font_layout = QVBoxLayout(font_tab)
        font_layout.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        font_layout.addWidget(split, 1)
        fields = QWidget()
        ff = QFormLayout(fields)
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
        self.transparent = QSpinBox()
        self.transparent.setRange(-1, 255)
        self.transparent.setToolTip(
            "Which palette index is transparent; -1 takes the top-left pixel's colour"
        )
        self.measure = QPushButton("Measure widths from sheet")
        self.fixed = QPushButton("Fixed width")
        self.gap = QSpinBox()
        self.gap.setRange(0, 16)
        self.gap.setValue(1)
        self.gap.setToolTip("Pixels added after the last inked column when measuring")
        ff.addRow("Cell width", self.cell_w)
        ff.addRow("Cell height", self.cell_h)
        ff.addRow("Columns", self.columns)
        ff.addRow("Base glyph", self.base)
        ff.addRow("Characters", self.chars)
        ff.addRow("Space width", self.space)
        ff.addRow("Missing glyph (-1: box)", self.missing)
        ff.addRow("Transparent index (-1: top-left)", self.transparent)
        wr = QHBoxLayout()
        wr.addWidget(self.measure)
        wr.addWidget(QLabel("Gap"))
        wr.addWidget(self.gap)
        wr.addWidget(self.fixed)
        ff.addRow("Widths", wr)
        self.glyph_map = QTableWidget(0, 2)
        self.glyph_map.setHorizontalHeaderLabels(["Text or [code]", "Glyph"])
        self.glyph_add = QPushButton("Add mapping")
        ff.addRow("Overrides", self.glyph_map)
        ff.addRow("", self.glyph_add)
        split.addWidget(fields)

        # The sheet, and the alphabet tools that work on its selection.
        sheet_side = QWidget()
        sv = QVBoxLayout(sheet_side)
        sv.setContentsMargins(0, 0, 0, 0)
        tools = QHBoxLayout()
        self.sheet_zoom = QSpinBox()
        self.sheet_zoom.setRange(1, 8)
        self.sheet_zoom.setValue(3)
        self.sheet_mode = QComboBox()
        self.sheet_mode.addItem("Select tile", False)
        self.sheet_mode.addItem("Select row", True)
        self.fill_table = QPushButton("Fill from table")
        self.fill_table.setToolTip(
            "Lay the start table's text over the glyphs from the selected one, "
            "in key order"
        )
        self.fill_with = QPushButton("Fill with…")
        self.shift_up = QPushButton("Shift up")
        self.shift_down = QPushButton("Shift down")
        for button in (self.shift_up, self.shift_down):
            button.setToolTip("Move the whole alphabet one row of glyphs")
        self.copy_alphabet = QPushButton("Copy")
        self.paste_alphabet = QPushButton("Paste")
        for button in (self.copy_alphabet, self.paste_alphabet):
            button.setToolTip("The alphabet as 20=A lines, one glyph per line")
        tools.addWidget(QLabel("Sheet"))
        tools.addWidget(self.sheet_mode)
        tools.addWidget(QLabel("Zoom"))
        tools.addWidget(self.sheet_zoom)
        tools.addWidget(self.fill_table)
        tools.addWidget(self.fill_with)
        tools.addWidget(self.shift_up)
        tools.addWidget(self.shift_down)
        tools.addWidget(self.copy_alphabet)
        tools.addWidget(self.paste_alphabet)
        tools.addStretch(1)
        sv.addLayout(tools)
        self.sheet_view = GlyphSheetView()
        scroll = QScrollArea()
        scroll.setWidget(self.sheet_view)
        scroll.setWidgetResizable(False)
        sv.addWidget(scroll, 1)
        self.sheet_pick = QLabel("")
        sv.addWidget(self.sheet_pick)
        split.addWidget(sheet_side)
        split.setStretchFactor(1, 1)
        self.fill_table.setEnabled(False)
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
        self.grid.toggled.connect(lambda _: self._paint())
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
        self.transparent.valueChanged.connect(lambda _: self._emit_font())
        self.measure.clicked.connect(self._measure)
        self.fixed.clicked.connect(self._fixed)
        self.sheet_zoom.valueChanged.connect(self._sheet_zoom)
        self.sheet_mode.currentIndexChanged.connect(self._sheet_mode)
        self.sheet_view.picked.connect(self._on_pick)
        self.fill_table.clicked.connect(self._fill_from_table)
        self.fill_with.clicked.connect(self._fill_with)
        self.shift_up.clicked.connect(lambda: self._shift(-1))
        self.shift_down.clicked.connect(lambda: self._shift(1))
        self.copy_alphabet.clicked.connect(self._copy_alphabet)
        self.paste_alphabet.clicked.connect(self._paste_alphabet)
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
        """The page arrows in the theme's button-text color."""
        role = QPalette.ColorRole.ButtonText
        self.prev.setIcon(themed_icon(self, Glyph.ARROW_LEFT, role))
        self.next.setIcon(themed_icon(self, Glyph.ARROW_RIGHT, role))

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
            self.transparent.setValue(
                f.transparent if f.transparent is not None else -1
            )
            self.glyph_map.setRowCount(len(f.glyphs))
            for r, (text, glyph) in enumerate(f.glyphs.items()):
                self.glyph_map.setItem(r, 0, QTableWidgetItem(text))
                self.glyph_map.setItem(r, 1, QTableWidgetItem(str(glyph)))
        finally:
            self._syncing = False
        self.sheet_view.set_font(font, self._sheet)
        self._paint()

    def update_font(self, font: Font | None) -> None:
        """A new value for the font the fields already show: redraw only."""
        self._font = font
        self._sheet = GlyphSheet(font) if font is not None else None
        self.sheet_view.set_font(font, self._sheet)
        self._paint()

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
        from PySide6.QtGui import QPixmap

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
            transparent=(
                None if self.transparent.value() < 0 else self.transparent.value()
            ),
        )

    def _emit_font(self) -> None:
        if self._syncing:
            return
        self.font_changed.emit(self.current_font())

    def _measure(self) -> None:
        font = self.current_font()
        sheet = GlyphSheet(font)
        widths = sheet.measured_widths(self.gap.value())
        self.font_changed.emit(font.with_widths(widths))

    def _fixed(self) -> None:
        self.font_changed.emit(self.current_font().with_widths(()))

    # --- the sheet and its alphabet -----------------------------------------

    def set_table_chars(self, chars: str) -> None:
        """The start table's single-character text, in key order, for Fill."""
        self._table_chars = chars
        self.fill_table.setEnabled(bool(chars))

    def _sheet_zoom(self, value: int) -> None:
        self.sheet_view.scale = value
        self.sheet_view.set_font(self._font, self._sheet)

    def _sheet_mode(self, _: int) -> None:
        self.sheet_view.rows = bool(self.sheet_mode.currentData())

    def _on_pick(self, first: int, last: int) -> None:
        span = f"{first}" if first == last else f"{first}–{last}"
        self.sheet_pick.setText(f"Glyph {span} selected; Fill lays from {first}.")

    def _lay_chars(self, chars: str) -> None:
        """Lay ``chars`` over consecutive glyphs from the pick, as the alphabet.

        The pick becomes the base glyph: that is what the ``chars`` string is,
        and an override already on one of those glyphs is left alone.
        """
        if not chars:
            return
        font = self.current_font()
        from dataclasses import replace

        self.font_changed.emit(replace(font, base=self.sheet_view.first, chars=chars))

    def _fill_from_table(self) -> None:
        self._lay_chars(self._table_chars)

    def _fill_with(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        templates = {
            "A-Z": "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
            "a-z": "abcdefghijklmnopqrstuvwxyz",
            "0-9": "0123456789",
            "A-Z a-z 0-9": (
                "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
            ),
            "ASCII printable": "".join(chr(c) for c in range(0x20, 0x7F)),
            "あ-ん": RUNS[HIRAGANA],
            "ア-ン": RUNS[KATAKANA],
        }
        options = [*templates, "custom…"]
        choice, ok = QInputDialog.getItem(
            self, "Fill", "Characters from the selected glyph:", options, 0, False
        )
        if not ok:
            return
        if choice == "custom…":
            chars, ok = QInputDialog.getText(self, "Fill", "Characters in glyph order:")
            if not ok:
                return
        else:
            chars = templates[choice]
        self._lay_chars(chars)

    def _shift(self, rows: int) -> None:
        """Move the whole alphabet ``rows`` rows of glyphs up or down the sheet."""
        font = self.current_font()
        base = max(0, font.base + rows * font.columns)
        from dataclasses import replace

        self.font_changed.emit(replace(font, base=base))

    def _copy_alphabet(self) -> None:
        """The alphabet as ``20=A`` lines, one glyph per line."""
        font = self.current_font()
        pairs = {font.base + i: ch for i, ch in enumerate(font.units)}
        pairs.update({glyph: text for text, glyph in font.glyphs.items()})
        lines = [f"{glyph:02X}={text}" for glyph, text in sorted(pairs.items())]
        QApplication.clipboard().setText("\n".join(lines))

    def _paste_alphabet(self) -> None:
        """``20=A`` lines back into the font: a run becomes ``chars``, the rest
        overrides."""
        pairs: list[tuple[int, str]] = []
        for line in QApplication.clipboard().text().splitlines():
            line = line.strip()
            if not line or "=" not in line:
                continue
            key, text = line.split("=", 1)
            try:
                glyph = int(key.strip().lstrip("$"), 16)
            except ValueError:
                continue
            if text:
                pairs.append((glyph, text))
        if not pairs:
            self.sheet_pick.setText("The clipboard holds no 20=A lines.")
            return
        pairs.sort()
        singles = [p for p in pairs if len(char_units(p[1])) == 1]
        run: list[tuple[int, str]] = []
        for pair in singles:
            if not run or pair[0] == run[-1][0] + 1:
                run.append(pair)
            else:
                break
        base = run[0][0] if run else 0
        chars = "".join(text for _, text in run)
        # Everything outside the run is an override, picked by glyph rather
        # than by position: a multi-character line may sort before the run,
        # and slicing the list would drop it and duplicate a run character.
        in_run = {glyph for glyph, _ in run}
        rest = {text: glyph for glyph, text in pairs if glyph not in in_run}
        from dataclasses import replace

        self.font_changed.emit(
            replace(self.current_font(), base=base, chars=chars, glyphs=rest)
        )

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


__all__ = ["GlyphSheet", "GlyphSheetView", "PreviewWindow"]
