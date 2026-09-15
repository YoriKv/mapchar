"""The Preview window's Font tab: a font's fields above its glyph sheet.

The fields describe the sheet — where it is, how it is cut into cells, which
characters sit where — and the tools under the sheet lay an alphabet over the
glyphs picked in it. Every change leaves as a whole new :class:`Font` on
:attr:`FontTab.font_changed`; the tab itself keeps nothing but what it shows.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.font import Font
from mapchar.core.text import char_units
from mapchar.ui.alphabets import ALPHABETS, CUSTOM
from mapchar.ui.glyph_sheet import GlyphSheet, GlyphSheetView
from mapchar.ui.number_fields import number_spin
from mapchar.ui.widgets import ElidedLabel, hint_field, show_elided_tooltips


class FontTab(QWidget):
    """The fields and the sheet, with the alphabet tools between them."""

    font_changed = Signal(object)
    """A new ``Font`` value for the bound font entry."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._font: Font | None = None
        self.sheet: GlyphSheet | None = None
        """The sheet the fields describe, shared with whoever draws through it."""
        self._table_chars = ""
        self._syncing = False
        font_layout = QVBoxLayout(self)
        font_layout.setContentsMargins(0, 0, 0, 0)
        split = QSplitter(Qt.Orientation.Vertical)
        font_layout.addWidget(split, 1)
        fields = QWidget()
        ff = QFormLayout(fields)
        # The fields scroll, so the splitter can give the sheet the height.
        fields_scroll = QScrollArea()
        fields_scroll.setWidget(fields)
        fields_scroll.setWidgetResizable(True)
        fields_scroll.setFrameShape(QScrollArea.Shape.NoFrame)
        self.font_path = QLineEdit()
        self.font_browse = QPushButton("Browse…")
        pr = QHBoxLayout()
        pr.addWidget(self.font_path, 1)
        pr.addWidget(self.font_browse)
        ff.addRow("Sheet (PNG)", pr)
        self.cell_w = number_spin(1, 64, 2)
        self.cell_h = number_spin(1, 64, 2)
        self.columns = number_spin(1, 256, 2)
        self.base = number_spin(0, 65535, 3)
        self.chars = hint_field(
            QLineEdit(), "characters in glyph order from the base glyph"
        )
        self.space = number_spin(0, 64, 2, special="none")
        self.missing = number_spin(-1, 65535, 3, special="box")
        self.transparent = number_spin(-1, 255, 3, special="top-left")
        self.transparent.setToolTip(
            "Which palette index is transparent; top-left takes that pixel's colour"
        )
        self.measure = QPushButton("Measure Widths")
        self.measure.setToolTip(
            "Give each glyph the width of its inked columns on the sheet, plus Gap"
        )
        self.fixed = QPushButton("Fixed Width")
        self.fixed.setToolTip("Give every glyph the cell's full width")
        self.gap = number_spin(0, 16, 2, value=1)
        self.gap.setToolTip("Pixels added after the last inked column when measuring")
        ff.addRow("Cell width", self.cell_w)
        ff.addRow("Cell height", self.cell_h)
        ff.addRow("Columns", self.columns)
        ff.addRow("Base glyph", self.base)
        ff.addRow("Characters", self.chars)
        ff.addRow("Space width", self.space)
        ff.addRow("Missing glyph", self.missing)
        ff.addRow("Transparent index", self.transparent)
        wr = QHBoxLayout()
        wr.addWidget(self.measure)
        wr.addWidget(QLabel("Gap"))
        wr.addWidget(self.gap)
        wr.addWidget(self.fixed)
        wr.addStretch(1)
        ff.addRow("Widths", wr)
        self.glyph_map = QTableWidget(0, 2)
        self.glyph_map.setHorizontalHeaderLabels(["Text or [code]", "Glyph"])
        self.glyph_map.horizontalHeader().setStretchLastSection(True)
        show_elided_tooltips(self.glyph_map)
        self.glyph_add = QPushButton("Add Mapping")
        self.glyph_add.setToolTip("A row that draws a text or [code] as one glyph")
        add_row = QHBoxLayout()
        add_row.addWidget(self.glyph_add)
        add_row.addStretch(1)
        ff.addRow("Overrides", self.glyph_map)
        ff.addRow("", add_row)
        split.addWidget(fields_scroll)

        # The sheet, and the alphabet tools that work on its selection.
        sheet_side = QWidget()
        sv = QVBoxLayout(sheet_side)
        sv.setContentsMargins(0, 0, 0, 0)
        # Two rows — how the sheet is shown and picked, then what is done to the
        # alphabet — so neither sets the window's minimum width on its own.
        tools = QHBoxLayout()
        alphabet = QHBoxLayout()
        self.sheet_zoom = number_spin(1, 8, 1, value=3)
        self.sheet_mode = QComboBox()
        self.sheet_mode.addItem("Select tile", False)
        self.sheet_mode.addItem("Select row", True)
        self.sheet_mode.setToolTip("Whether a click picks one glyph or its whole row")
        self.fill_table = QPushButton("Fill from Table")
        self.fill_table.setToolTip(
            "Lay the start table's text over the glyphs from the selected one, "
            "in key order"
        )
        self.fill_with = QPushButton("Fill With…")
        self.fill_with.setToolTip(
            "Lay a template or typed run of characters over the glyphs from the "
            "selected one"
        )
        self.shift_up = QPushButton("Shift Up")
        self.shift_down = QPushButton("Shift Down")
        for button in (self.shift_up, self.shift_down):
            button.setToolTip("Move the whole alphabet one row of glyphs")
        self.copy_alphabet = QPushButton("Copy Alphabet")
        self.paste_alphabet = QPushButton("Paste Alphabet")
        for button in (self.copy_alphabet, self.paste_alphabet):
            button.setToolTip("The alphabet as 20=A lines, one glyph per line")
        tools.addWidget(QLabel("Sheet"))
        tools.addWidget(self.sheet_mode)
        tools.addWidget(QLabel("Zoom"))
        tools.addWidget(self.sheet_zoom)
        tools.addStretch(1)
        tools.addWidget(self.fill_table)
        tools.addWidget(self.fill_with)
        for button in (
            self.shift_up,
            self.shift_down,
            self.copy_alphabet,
            self.paste_alphabet,
        ):
            alphabet.addWidget(button)
        alphabet.addStretch(1)
        sv.addLayout(tools)
        sv.addLayout(alphabet)
        self.sheet_view = GlyphSheetView()
        scroll = QScrollArea()
        scroll.setWidget(self.sheet_view)
        scroll.setWidgetResizable(False)
        sv.addWidget(scroll, 1)
        self.sheet_pick = ElidedLabel("")
        sv.addWidget(self.sheet_pick)
        split.addWidget(sheet_side)
        split.setStretchFactor(1, 1)
        self.fill_table.setEnabled(False)

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

    # --- the font it shows --------------------------------------------------

    def set_font(self, font: Font | None) -> None:
        """Show ``font``: every field, and the sheet under them."""
        self._font = font
        self.sheet = GlyphSheet(font) if font is not None else None
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
        self.sheet_view.set_font(font, self.sheet)

    def update_font(self, font: Font | None) -> None:
        """A new value for the font the fields already show: redraw only."""
        self._font = font
        self.sheet = GlyphSheet(font) if font is not None else None
        self.sheet_view.set_font(font, self.sheet)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Glyph Sheet", "", "Images (*.png *.bmp)"
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
        self.sheet_view.set_font(self._font, self.sheet)

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
        self.font_changed.emit(replace(font, base=self.sheet_view.first, chars=chars))

    def _fill_from_table(self) -> None:
        self._lay_chars(self._table_chars)

    def _fill_with(self) -> None:
        options = [*ALPHABETS, CUSTOM]
        choice, ok = QInputDialog.getItem(
            self, "Fill", "Characters from the selected glyph:", options, 0, False
        )
        if not ok:
            return
        if choice == CUSTOM:
            chars, ok = QInputDialog.getText(self, "Fill", "Characters in glyph order:")
            if not ok:
                return
        else:
            chars = ALPHABETS[choice]
        self._lay_chars(chars)

    def _shift(self, rows: int) -> None:
        """Move the whole alphabet ``rows`` rows of glyphs up or down the sheet."""
        font = self.current_font()
        base = max(0, font.base + rows * font.columns)
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
        self.font_changed.emit(
            replace(self.current_font(), base=base, chars=chars, glyphs=rest)
        )

    def _add_mapping(self) -> None:
        r = self.glyph_map.rowCount()
        self.glyph_map.insertRow(r)
        self.glyph_map.setItem(r, 0, QTableWidgetItem("[code]"))
        self.glyph_map.setItem(r, 1, QTableWidgetItem("0"))


__all__ = ["FontTab"]
