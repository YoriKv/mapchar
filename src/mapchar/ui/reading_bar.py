"""The Reading bar: how the bytes are cut into strings, edited live.

Every setting a block's reading has — where its strings come from, how one
ends, how it goes back — as a row of controls under the Codecs bar, the way
celPix keeps a slice's codec in its toolbar. The bar holds no entry: it is
loaded from a configuration and reads one back, and says which control the
user changed, so the window applies the change — to a block as an undo step, to
a file as its session — and every view re-reads.

What shows follows the reading: its source kind and string type, whether it is
read as pointers (the Table list's **Pointer**), and whether it is a block's,
since only a block has addresses of its own, a way back to disk and room left
by a re-compression.
"""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtCore import QRegularExpression, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QSpinBox,
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
from mapchar.core.numbers import format_num, parse_num
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

_HEX = QRegularExpression(r"\s*\$?[0-9A-Fa-f_]*\s*")
_NUMBER = QRegularExpression(r"\s*(-?\$|\$-)?[0-9A-Fa-f]*\s*")


class HexEdit(QLineEdit):
    """A field for one hex number; blank is ``None``."""

    def __init__(self, chars: int = 8, parent: QWidget | None = None):
        super().__init__(parent)
        self.setValidator(QRegularExpressionValidator(_HEX, self))
        fit_chars(self, chars)
        self.setMaximumWidth(self.minimumWidth())

    def value(self) -> int | None:
        try:
            return parse_hex(self.text(), None)
        except ValueError:
            return None

    def set_value(self, value: int | None) -> None:
        self.setText("" if value is None else f"{value:X}")


def _spin(low: int, high: int, off: bool = False) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(low, high)
    if off:
        spin.setSpecialValueText("off")
    return spin


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

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._loading = False
        self._pointers = False
        self._block = False
        self._groups: dict[str, QWidget] = {}

        self.source_kind = CompactComboBox(120)
        self.start = HexEdit()
        self.stop = HexEdit()
        self.count = _spin(1, 1_000_000)
        self.length = _spin(1, 1_000_000)
        self.ptr_size = _spin(1, 4)
        self.ptr_stride = _spin(1, 4096)
        self.ptr_endian = QComboBox()
        self.ptr_endian.addItem("Little", "little")
        self.ptr_endian.addItem("Big", "big")
        self.ptr_mapping = CompactComboBox(100)
        self.ptr_mapping.setEditable(True)
        self.ptr_offset = QLineEdit("0")
        self.ptr_offset.setValidator(QRegularExpressionValidator(_NUMBER, self))
        fit_chars(self.ptr_offset, 8)
        self.ptr_offset.setMaximumWidth(self.ptr_offset.minimumWidth())
        self.ptr_bank = _spin(0, 4095)
        self.ptr_addresses = hint_field(
            QLineEdit(), "hex addresses, comma separated", "The pointers' addresses"
        )
        fit_chars(self.ptr_addresses, 24)

        self.string_type = QComboBox()
        for label, data in (
            ("End token", END),
            ("Fixed length", FIXED_LENGTH),
            ("Pascal (length prefix)", PASCAL),
            ("Next pointer", NEXT),
        ):
            self.string_type.addItem(label, data)
        self.fixed_length = _spin(1, 1_000_000)
        self.stop_at_end = QCheckBox("Stop at end token")
        self.pascal_width = _spin(1, 4)
        self.pascal_tokens = QCheckBox("Counts token weights")
        self.spp = _spin(1, 64)
        self.realign_m = _spin(0, 65536, off=True)
        self.realign_o = _spin(0, 65536)
        self.line_length = _spin(0, 1_000_000, off=True)
        self.show_end = QCheckBox("Show [end]")
        self.skips = hint_field(
            QLineEdit(),
            "from>to, from>to  (hex)",
            "Byte ranges inside the region that are not text, in hex",
        )
        fit_chars(self.skips, 16)

        self.bound = HexEdit()
        hint_field(
            self.bound,
            "stop",
            "The last address a write may reach, in hex; blank stops at the "
            "region's end",
        )
        self.write_mode = QComboBox()
        self.write_mode.addItem("Automatic", None)
        self.write_mode.addItem("Packed", WriteMode.PACKED)
        self.write_mode.addItem("Slotted", WriteMode.SLOTTED)
        self.fill = HexEdit(2)
        self.spare_room = QComboBox()
        self.spare_room.addItem("Fill", "fill")
        self.spare_room.addItem("Keep", "keep")

        for name, label, widgets, tip in (
            ("source_kind", "Source", (self.source_kind,), "Where the strings are"),
            ("start", "Start", (self.start,), "The first byte, in hex"),
            ("stop", "Stop", (self.stop,), "The first byte past the region, in hex"),
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
                "Added to every pointer value: decimal or $hex, with a leading - "
                "to subtract",
            ),
            (
                "ptr_bank",
                "Bank",
                (self.ptr_bank,),
                "The bank a banked mapping reads in",
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
            self._groups[name] = self.add_group(label, *widgets, tip=tip)

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
            ("skips", self.skips),
            ("bound", self.bound),
            ("fill", self.fill),
        ):
            field.editingFinished.connect(lambda n=name: self._edited(n))

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
                    self.ptr_addresses.setText(", ".join(f"{a:X}" for a in s.addresses))
                self.ptr_size.setValue(s.size)
                self.ptr_endian.setCurrentIndex(1 if s.endian == "big" else 0)
                self.ptr_mapping.setCurrentText(s.mapping_id)
                self.ptr_offset.setText(format_num(s.offset) if s.offset else "0")
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
            self.skips.setText(", ".join(f"{a:X}>{b:X}" for a, b in config.skips))
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

    # -- reading back ---------------------------------------------------------

    def target_offset(self) -> int | None:
        text = self.ptr_offset.text().strip()
        if not text:
            return 0
        try:
            return parse_num(text)
        except ValueError:
            return None

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
        offset = self.target_offset()
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
            try:
                addresses = tuple(
                    parse_hex(a)
                    for a in self.ptr_addresses.text().split(",")
                    if a.strip()
                )
            except ValueError:
                addresses = getattr(old, "addresses", ())
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
                "skips": self._skips(base.skips),
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

    def _skips(self, old: tuple[tuple[int, int], ...]) -> tuple[tuple[int, int], ...]:
        skips = []
        try:
            for pair in self.skips.text().split(","):
                if ">" in pair:
                    a, b = pair.split(">", 1)
                    skips.append((parse_hex(a), parse_hex(b)))
        except ValueError:
            return old
        return tuple(skips)

    def spare_room_rule(self) -> str:
        return self.spare_room.currentData()


def source_kind_for(kind: str | None, pointers: bool) -> str:
    """``kind`` when it reads the way asked, else that way's first kind."""
    if pointers:
        return kind if kind in (TABLE, LIST) else TABLE
    return kind if kind in (RANGE, FIXED) else RANGE


def _or(value: int | None, default: int) -> int:
    return default if value is None else value
