"""The Reading bar: how the bytes are cut into strings, edited live.

Every setting a block's reading has — where its strings come from, how its
pointers read, how a string ends, how it goes back — as controls under the
Format bar, the way celPix keeps a slice's codec in its toolbar, gathered into
the sections :data:`SECTIONS` names. The bar holds no entry: it is loaded from a
configuration and reads one back, and says which control the user changed, so
the window applies the change — to a block as an undo step, to a file as its
session — and every view re-reads.

What shows follows the reading: its source kind and string type, whether it is
read as pointers (the Format bar's mode), and whether it is a block's, since
only a block has addresses of its own, a way back to disk and room left by a
re-compression. A section with nothing to show is hidden whole, and with
nothing open the bar shows :data:`DEFAULT_READING`.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QPoint, QRegularExpression, Qt, Signal
from PySide6.QtGui import QKeyEvent, QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.bits import parse_hex
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    FixedSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    StringType,
    WriteMode,
)
from mapchar.ui.number_fields import (
    AddressEdit,
    AddressSpelling,
    HexEdit,
    HexSpinBox,
    OffsetEdit,
    number_spin,
    respell_addresses,
)
from mapchar.ui.widgets import CompactComboBox, WrapBar, fit_chars, hint_field

RANGE, FIXED, TABLE, LIST = "range", "fixed", "table", "list"
"""The source kinds, as the Source picker's data."""

_SOURCE_NAMES = {
    RANGE: "Range",
    FIXED: "Fixed strings",
    TABLE: "Pointer table",
    LIST: "Pointer list",
}

END, FIXED_LENGTH, PASCAL, NEXT = "end", "fixed", "pascal", "next"
"""The string types, as the String type picker's data."""

SECTIONS = {
    "Source": (
        "source_kind",
        "start",
        "stop",
        "count",
        "length",
        "ptr_addresses",
        "skips",
    ),
    "Pointers": (
        "ptr_size",
        "ptr_stride",
        "ptr_endian",
        "ptr_mapping",
        "ptr_offset",
        "ptr_bank",
    ),
    "Strings": (
        "string_type",
        "fixed_length",
        "stop_at_end",
        "pascal",
        "spp",
        "realign",
        "line_length",
        "show_end",
    ),
    "Writing": ("bound", "write_mode", "fill", "spare_room"),
}
"""The bar's sections, in order, and the controls each gathers."""

_NOT_STRING_VIEW = ("Source", "Pointers")
"""The sections about where a block's strings are, which a view of the strings'
own bytes does not show."""

DEFAULT_READING = BlockConfig(RangeSource(0, 0), EndToken(), "")
"""What the bar shows with nothing open: a file read as a range of strings, so
the window starts on one source's settings rather than on every source's at
once."""

_HEX = QRegularExpression(r"\s*\$?[0-9A-Fa-f_]*\s*")


class _HexDelegate(QStyledItemDelegate):
    """Cells edited as hex numbers."""

    def createEditor(self, parent, option, index):  # noqa: N802 - Qt override
        editor = QLineEdit(parent)
        editor.setValidator(QRegularExpressionValidator(_HEX, editor))
        return editor


class SkipsPopup(QFrame):
    """The list of a block's skip ranges, edited in a popup under its picker.

    A row is a ``from`` and a ``to`` in hex; the list applies as it changes,
    and a row with a side still blank or unreadable is left out until it can
    be read whole. The popup closes on Esc or a click outside it.
    """

    changed = Signal()

    def __init__(self, parent: QWidget):
        super().__init__(parent, Qt.WindowType.Popup)
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self._loading = False
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(QLabel("Reading from continues at to, in hex"))
        self.table = QTableWidget(0, 2)
        self.table.setHorizontalHeaderLabels(["From", "To"])
        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.ResizeMode.Stretch
        )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setItemDelegate(_HexDelegate(self.table))
        self.table.setMinimumSize(fit_chars(QLineEdit(), 10).minimumWidth() * 2, 120)
        self.table.cellChanged.connect(self._on_cell_changed)
        layout.addWidget(self.table, 1)
        row = QHBoxLayout()
        self.add_button = QPushButton("Add")
        self.add_button.clicked.connect(lambda: self.add(None, None))
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self.remove_current)
        row.addWidget(self.add_button)
        row.addWidget(self.remove_button)
        row.addStretch(1)
        layout.addLayout(row)

    def set_value(self, skips: tuple[tuple[int, int], ...]) -> None:
        """Show ``skips``; a list already showing them, blank rows and all, is
        left as it is, so a reload after an edit does not lose the row being
        typed into."""
        if self.value() == tuple(skips):
            return
        self._loading = True
        try:
            self.table.setRowCount(0)
            for a, b in skips:
                self._append(f"{a:X}", f"{b:X}")
        finally:
            self._loading = False

    def value(self) -> tuple[tuple[int, int], ...]:
        """The rows that read as a pair of numbers."""
        skips = []
        for row in range(self.table.rowCount()):
            try:
                a = parse_hex(self._text(row, 0), None)
                b = parse_hex(self._text(row, 1), None)
            except ValueError:
                continue
            if a is not None and b is not None:
                skips.append((a, b))
        return tuple(skips)

    def add(self, start: int | None, stop: int | None) -> None:
        """Append a range — blank, to be typed, when either side is ``None``."""
        row = self._append(
            "" if start is None else f"{start:X}", "" if stop is None else f"{stop:X}"
        )
        self.table.setCurrentCell(row, 0)
        if start is None or stop is None:
            self.table.editItem(self.table.item(row, 0 if start is None else 1))
        else:
            self.changed.emit()

    def remove_current(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        had = self.value()
        self.table.removeRow(row)
        if self.value() != had:
            self.changed.emit()

    def _append(self, start: str, stop: str) -> int:
        was, self._loading = self._loading, True
        try:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(start))
            self.table.setItem(row, 1, QTableWidgetItem(stop))
        finally:
            self._loading = was
        return row

    def _text(self, row: int, column: int) -> str:
        item = self.table.item(row, column)
        return item.text() if item is not None else ""

    def _on_cell_changed(self, row: int, column: int) -> None:
        if not self._loading:
            self.changed.emit()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802 - Qt override
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)


class SkipsPicker(CompactComboBox):
    """A block's skip ranges: the list as one line, opening on the popup that
    edits it in place of a dropdown."""

    changed = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(140, parent)
        self.addItem("none")
        self.setToolTip("Byte ranges inside the region that are not text")
        self.popup = SkipsPopup(self)
        self.popup.changed.connect(self._on_changed)

    def set_value(self, skips: tuple[tuple[int, int], ...]) -> None:
        self.popup.set_value(skips)
        self._show_summary()

    def value(self) -> tuple[tuple[int, int], ...]:
        return self.popup.value()

    def add(self, start: int, stop: int) -> None:
        """Append ``start`` to ``stop`` and apply it."""
        self.popup.add(start, stop)

    def _on_changed(self) -> None:
        self._show_summary()
        self.changed.emit()

    def _show_summary(self) -> None:
        text = ", ".join(f"{a:X}>{b:X}" for a, b in self.value()) or "none"
        self.setItemText(0, text)

    def showPopup(self) -> None:  # noqa: N802 - Qt override
        below = self.mapToGlobal(QPoint(0, self.height()))
        screen = self.screen().availableGeometry()
        size = self.popup.sizeHint()
        x = min(below.x(), screen.right() - size.width())
        y = below.y()
        if y + size.height() > screen.bottom():
            y = self.mapToGlobal(QPoint(0, 0)).y() - size.height()
        self.popup.move(max(x, screen.left()), max(y, screen.top()))
        self.popup.show()
        self.popup.table.setFocus()

    def hidePopup(self) -> None:  # noqa: N802 - Qt override
        pass


def source_kind(config: BlockConfig) -> str:
    """A configuration's source kind, as the Source picker names it."""
    source = config.source
    if isinstance(source, FixedSource):
        return FIXED
    if isinstance(source, PointerTableSource):
        return TABLE
    if isinstance(source, PointerListSource):
        return LIST
    return RANGE


class ReadingBar(WrapBar):
    edited = Signal(str)
    """The user changed the control of this name; a run on one merges."""

    def __init__(
        self, spelling: AddressSpelling | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.spelling = spelling if spelling is not None else AddressSpelling(self)
        self._loading = False
        self._pointers = False
        self._block = False
        self._string_view = False
        self._groups: dict[str, QWidget] = {}
        self.sections: dict[str, QWidget] = {}

        self.source_kind = CompactComboBox(120)
        # Each count is as wide as the values it usually holds, not its maximum.
        self.start = AddressEdit(self.spelling)
        self.stop = AddressEdit(self.spelling)
        self.count = number_spin(1, 1_000_000, 4)
        self.length = number_spin(1, 1_000_000, 3)
        self.ptr_size = number_spin(1, 4, 1)
        self.ptr_stride = number_spin(1, 4096, 2)
        self.ptr_endian = QComboBox()
        self.ptr_endian.addItem("Little", "little")
        self.ptr_endian.addItem("Big", "big")
        self.ptr_mapping = CompactComboBox(100)
        self.ptr_mapping.setEditable(True)
        self.ptr_offset = OffsetEdit()
        self.ptr_bank = HexSpinBox(0, 0xFFF, 2)
        self.ptr_addresses = hint_field(
            QLineEdit(), "addresses, comma separated", "The pointers' addresses"
        )
        fit_chars(self.ptr_addresses, 24)
        self.spelling.changed.connect(self._respell_addresses)

        self.string_type = QComboBox()
        for label, data in (
            ("End token", END),
            ("Fixed length", FIXED_LENGTH),
            ("Pascal (length prefix)", PASCAL),
            ("Next pointer", NEXT),
        ):
            self.string_type.addItem(label, data)
        self.fixed_length = number_spin(1, 1_000_000, 3)
        self.stop_at_end = QCheckBox("Stop at end token")
        self.pascal_width = number_spin(1, 4, 1)
        self.pascal_tokens = QCheckBox("Counts token weights")
        self.spp = number_spin(1, 64, 2)
        self.realign_m = number_spin(0, 65536, 2, off=True)
        self.realign_o = number_spin(0, 65536, 2)
        self.line_length = number_spin(0, 1_000_000, 3, off=True)
        self.show_end = QCheckBox("Show [end]")
        self.skips = SkipsPicker()

        self.bound = AddressEdit(self.spelling)
        hint_field(
            self.bound,
            "stop",
            "The last address a write may reach; blank stops at the region's end",
        )
        self.write_mode = QComboBox()
        self.write_mode.addItem("Automatic", None)
        self.write_mode.addItem("Packed", WriteMode.PACKED)
        self.write_mode.addItem("Slotted", WriteMode.SLOTTED)
        self.fill = HexEdit(2)
        self.spare_room = QComboBox()
        self.spare_room.addItem("Fill", "fill")
        self.spare_room.addItem("Keep", "keep")

        groups = {}
        for name, label, widgets, tip in (
            ("source_kind", "", (self.source_kind,), "Where the strings are"),
            ("start", "Start", (self.start,), "The first byte"),
            ("stop", "Stop", (self.stop,), "The first byte past the region"),
            ("count", "Count", (self.count,), "How many strings"),
            ("length", "Length", (self.length,), "Bytes in every string"),
            ("ptr_size", "Size", (self.ptr_size,), "Bytes in a pointer"),
            (
                "ptr_stride",
                "Stride",
                (self.ptr_stride,),
                "Bytes from one pointer to the next",
            ),
            ("ptr_endian", "Endian", (self.ptr_endian,), "The pointers' byte order"),
            (
                "ptr_mapping",
                "Mapping",
                (self.ptr_mapping,),
                "How a pointer value becomes a file offset",
            ),
            (
                "ptr_offset",
                "Offset",
                (self.ptr_offset,),
                "Added to every pointer value, in hex, with a leading - to subtract",
            ),
            (
                "ptr_bank",
                "Bank",
                (self.ptr_bank,),
                "The bank a banked mapping reads in, in hex",
            ),
            ("ptr_addresses", "Addresses", (self.ptr_addresses,), None),
            ("string_type", "Ends at", (self.string_type,), "How a string ends"),
            ("fixed_length", "Length", (self.fixed_length,), "Bytes in every string"),
            ("stop_at_end", "", (self.stop_at_end,), None),
            (
                "pascal",
                "Prefix",
                (self.pascal_width, self.pascal_tokens),
                "Bytes in the length prefix",
            ),
            ("spp", "End tokens", (self.spp,), "End tokens in one string"),
            (
                "realign",
                "Realign",
                (self.realign_m, QLabel("+"), self.realign_o),
                "After each end token, round up to a multiple plus an offset",
            ),
            (
                "line_length",
                "Lines",
                (self.line_length,),
                "Split fixed strings into lines this long",
            ),
            ("show_end", "", (self.show_end,), None),
            ("skips", "Skips", (self.skips,), None),
            ("bound", "Bound", (self.bound,), None),
            (
                "write_mode",
                "Write",
                (self.write_mode,),
                "How a write lays the strings out",
            ),
            ("fill", "Fill", (self.fill,), "The byte that pads unused room, in hex"),
            (
                "spare_room",
                "Spare room",
                (self.spare_room,),
                "What a shorter re-compression leaves in its slot",
            ),
        ):
            groups[name] = (label, widgets, tip)
        for title, names in SECTIONS.items():
            section = self.add_section(title)
            self.sections[title] = section.parentWidget()
            for name in names:
                label, widgets, tip = groups[name]
                self._groups[name] = section.add_group(label, *widgets, tip=tip)

        for name, combo in (
            ("source_kind", self.source_kind),
            ("ptr_endian", self.ptr_endian),
            ("string_type", self.string_type),
            ("write_mode", self.write_mode),
            ("spare_room", self.spare_room),
        ):
            combo.currentIndexChanged.connect(lambda _=0, n=name: self._edited(n))
        self.ptr_mapping.activated.connect(lambda _=0: self._edited("ptr_mapping"))
        self.ptr_mapping.lineEdit().editingFinished.connect(
            lambda: self._edited("ptr_mapping")
        )
        for name, spin in (
            ("count", self.count),
            ("length", self.length),
            ("ptr_size", self.ptr_size),
            ("ptr_stride", self.ptr_stride),
            ("ptr_bank", self.ptr_bank),
            ("fixed_length", self.fixed_length),
            ("pascal_width", self.pascal_width),
            ("spp", self.spp),
            ("realign_m", self.realign_m),
            ("realign_o", self.realign_o),
            ("line_length", self.line_length),
        ):
            spin.valueChanged.connect(lambda _=0, n=name: self._edited(n))
        self.skips.changed.connect(lambda: self._edited("skips"))
        for name, box in (
            ("stop_at_end", self.stop_at_end),
            ("pascal_tokens", self.pascal_tokens),
            ("show_end", self.show_end),
        ):
            box.toggled.connect(lambda _=False, n=name: self._edited(n))
        for name, field in (
            ("start", self.start),
            ("stop", self.stop),
            ("ptr_offset", self.ptr_offset),
            ("ptr_addresses", self.ptr_addresses),
            ("bound", self.bound),
            ("fill", self.fill),
        ):
            field.editingFinished.connect(lambda n=name: self._edited(n))

        self.show_default()

    # -- loading --------------------------------------------------------------

    def set_mappings(self, mapping_ids: list[str]) -> None:
        was = self.ptr_mapping.blockSignals(True)
        text = self.ptr_mapping.currentText()
        self.ptr_mapping.clear()
        self.ptr_mapping.addItems(mapping_ids or ["linear"])
        self.ptr_mapping.setCurrentText(text or (mapping_ids or ["linear"])[0])
        self.ptr_mapping.blockSignals(was)

    def suggest_mapping(self, mapping_id: str) -> None:
        """Start the pointer mapping on ``mapping_id``, applying nothing."""
        was = self.ptr_mapping.blockSignals(True)
        self.ptr_mapping.setCurrentText(mapping_id)
        self.ptr_mapping.blockSignals(was)

    def load(
        self,
        config: BlockConfig,
        *,
        block: bool,
        spare_room: str = "fill",
        compressed: bool = False,
    ) -> None:
        """Show ``config``: a block's own, or a file's reading when not ``block``."""
        self._loading = True
        try:
            self._block = block
            self._pointers = config.has_pointers
            self._fill_kinds(source_kind(config))
            s = config.source
            if isinstance(s, RangeSource):
                self.start.set_value(s.start)
                self.stop.set_value(s.stop)
            elif isinstance(s, FixedSource):
                self.start.set_value(s.start)
                self.count.setValue(s.count)
                self.length.setValue(s.length)
            else:
                if isinstance(s, PointerTableSource):
                    self.start.set_value(s.start)
                    self.stop.set_value(s.stop)
                    self.ptr_stride.setValue(s.stride)
                else:
                    self.ptr_addresses.setText(
                        ", ".join(self.spelling.format(a) for a in s.addresses)
                    )
                self.ptr_size.setValue(s.size)
                self.ptr_endian.setCurrentIndex(1 if s.endian == "big" else 0)
                self.ptr_mapping.setCurrentText(s.mapping_id)
                self.ptr_offset.set_value(s.offset)
                self.ptr_bank.setValue(s.bank)
            st = config.string_type
            kind = END
            if isinstance(st, FixedLength):
                kind = FIXED_LENGTH
                self.fixed_length.setValue(st.length)
                self.stop_at_end.setChecked(st.stop_at_end)
            elif isinstance(st, Pascal):
                kind = PASCAL
                self.pascal_width.setValue(st.width)
                self.pascal_tokens.setChecked(st.counts_tokens)
            elif isinstance(st, NextPointer):
                kind = NEXT
            self.string_type.setCurrentIndex(self.string_type.findData(kind))
            self.spp.setValue(config.strings_per_pointer)
            self.realign_m.setValue(config.realign[0])
            self.realign_o.setValue(config.realign[1])
            self.line_length.setValue(config.line_length)
            self.show_end.setChecked(config.show_end)
            self.skips.set_value(config.skips)
            self.bound.set_value(config.bound)
            self.write_mode.setCurrentIndex(
                max(self.write_mode.findData(config.write_mode), 0)
            )
            self.fill.set_value(config.fill)
            self.spare_room.setCurrentIndex(
                max(self.spare_room.findData(spare_room), 0)
            )
            self.spare_room.setEnabled(compressed)
        finally:
            self._loading = False
        self._sync()

    def show_default(self) -> None:
        """Show :data:`DEFAULT_READING`, for when no entry has one of its own."""
        self.load(DEFAULT_READING, block=False)

    def set_pointers(self, pointers: bool) -> None:
        """Switch the source kinds between reading characters and pointers,
        keeping every other value, so :meth:`config` reads the other kind."""
        if pointers == self._pointers:
            return
        self._pointers = pointers
        kind = source_kind_for(self.source_kind.currentData(), pointers)
        self._loading = True
        try:
            self._fill_kinds(kind)
            if not pointers and self.string_type.currentData() == NEXT:
                self.string_type.setCurrentIndex(0)
        finally:
            self._loading = False
        self._sync()

    def _fill_kinds(self, current: str) -> None:
        self.source_kind.clear()
        kinds = (TABLE, LIST) if self._pointers else (RANGE, FIXED)
        for kind in kinds:
            self.source_kind.addItem(_SOURCE_NAMES[kind], kind)
        self.source_kind.setCurrentIndex(max(self.source_kind.findData(current), 0))
        # A file has no addresses of its own to list pointers at.
        if self._pointers and not self._block:
            self.source_kind.model().item(1).setEnabled(False)

    def _edited(self, name: str) -> None:
        if self._loading:
            return
        self._sync()
        self.edited.emit(name)

    def _sync(self) -> None:
        kind = self.source_kind.currentData()
        fixed = kind == FIXED
        pointers, block = self._pointers, self._block
        st = self.string_type.currentData()
        was = self.string_type.blockSignals(True)
        self.string_type.model().item(3).setEnabled(pointers)
        self.string_type.blockSignals(was)
        shown = {
            "source_kind": True,
            "start": block and kind != LIST,
            "stop": block and kind in (RANGE, TABLE),
            "count": block and fixed,
            "length": fixed,
            "ptr_size": pointers,
            "ptr_stride": kind == TABLE,
            "ptr_endian": pointers,
            "ptr_mapping": pointers,
            "ptr_offset": pointers,
            "ptr_bank": pointers,
            "ptr_addresses": block and kind == LIST,
            "string_type": not fixed,
            "fixed_length": st == FIXED_LENGTH and not fixed,
            "stop_at_end": st == FIXED_LENGTH or fixed,
            "pascal": st == PASCAL and not fixed,
            "spp": st == END and not fixed,
            "realign": True,
            "line_length": st == FIXED_LENGTH or fixed,
            "show_end": st == FIXED_LENGTH or fixed,
            "skips": block,
            "bound": block,
            "write_mode": block,
            "fill": block,
            "spare_room": block,
        }
        for name, visible in shown.items():
            self._groups[name].setVisible(visible)
        for title, names in SECTIONS.items():
            hidden = self._string_view and title in _NOT_STRING_VIEW
            self.sections[title].setVisible(not hidden and any(shown[n] for n in names))

    def show_string_view(self, string_view: bool) -> None:
        """Show only the sections that shape a string — for a view of strings'
        bytes — or, with ``False``, every section the reading uses."""
        if string_view != self._string_view:
            self._string_view = string_view
            self._sync()

    # -- reading back ---------------------------------------------------------

    def _respell_addresses(self, old) -> None:
        """Spell the pointer list under the address format it changed to."""
        text = self.ptr_addresses.text()
        self.ptr_addresses.setText(respell_addresses(text, self.spelling, old))

    def config(self, base: BlockConfig, table_id: str) -> BlockConfig:
        """The reading the controls show, over ``base`` for what they do not.

        A file's source has no addresses in the bar, so they stay ``base``'s.
        A number nobody can read keeps ``base``'s value rather than a guess.
        """
        kind = source_kind_for(self.source_kind.currentData(), self._pointers)
        old = base.source
        old_start = getattr(old, "start", 0)
        old_stop = getattr(old, "stop", 0)
        if self._block:
            start = _or(self.start.value(), old_start)
            stop = _or(self.stop.value(), old_stop)
        else:
            start, stop = old_start, old_stop
        offset = self.ptr_offset.value()
        pointer = {
            "size": self.ptr_size.value(),
            "endian": self.ptr_endian.currentData(),
            "mapping_id": self.ptr_mapping.currentText().strip() or "linear",
            "offset": getattr(old, "offset", 0) if offset is None else offset,
            "bank": self.ptr_bank.value(),
        }
        if kind == FIXED:
            count = self.count.value() if self._block else getattr(old, "count", 1)
            source = FixedSource(start, count, self.length.value())
        elif kind == TABLE:
            source = PointerTableSource(
                start, stop, stride=self.ptr_stride.value(), **pointer
            )
        elif kind == LIST:
            read = [
                self.spelling.parse(a)
                for a in self.ptr_addresses.text().split(",")
                if a.strip()
            ]
            if None in read:
                addresses = getattr(old, "addresses", ())
            else:
                addresses = tuple(read)
            source = PointerListSource(addresses, **pointer)
        else:
            source = RangeSource(start, stop)
        changes = {
            "source": source,
            "string_type": self._string_type(fixed=kind == FIXED),
            "table_id": table_id,
            "strings_per_pointer": self.spp.value(),
            "realign": (self.realign_m.value(), self.realign_o.value()),
            "line_length": self.line_length.value(),
            "show_end": self.show_end.isChecked(),
        }
        if self._block:
            fill = self.fill.value()
            changes |= {
                "skips": self.skips.value(),
                "bound": self.bound.value(),
                "write_mode": self.write_mode.currentData(),
                "fill": base.fill if fill is None else fill & 0xFF,
            }
        return replace(base, **changes)

    def _string_type(self, *, fixed: bool) -> StringType:
        st = self.string_type.currentData()
        if fixed:
            return FixedLength(self.length.value(), self.stop_at_end.isChecked())
        if st == FIXED_LENGTH:
            return FixedLength(self.fixed_length.value(), self.stop_at_end.isChecked())
        if st == PASCAL:
            return Pascal(self.pascal_width.value(), self.pascal_tokens.isChecked())
        if st == NEXT and self._pointers:
            return NextPointer()
        return EndToken()

    def spare_room_rule(self) -> str:
        return self.spare_room.currentData()


def source_kind_for(kind: str | None, pointers: bool) -> str:
    """``kind`` when it reads the way asked, else that way's first kind."""
    if pointers:
        return kind if kind in (TABLE, LIST) else TABLE
    return kind if kind in (RANGE, FIXED) else RANGE


def _or(value: int | None, default: int) -> int:
    return default if value is None else value
