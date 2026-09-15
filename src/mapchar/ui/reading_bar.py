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

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QLineEdit,
    QWidget,
)

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    StringType,
    WriteMode,
    default_write_mode,
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
from mapchar.ui.skips_picker import SkipsPicker
from mapchar.ui.widgets import CompactComboBox, WrapBar, fit_chars, hint_field

RANGE, TABLE, LIST = "range", "table", "list"
"""The source kinds, as the Source picker's data."""

_SOURCE_NAMES = {
    RANGE: "Range",
    TABLE: "Pointer table",
    LIST: "Pointer list",
}

END, FIXED_LENGTH, PASCAL, NEXT, LINES = "end", "fixed", "pascal", "next", "lines"
"""The string types, as the String type picker's data."""

SECTIONS = {
    "Source": ("source_kind", "start", "stop", "ptr_addresses"),
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
        "count",
        "stop_at_end",
        "pascal",
        "spp",
        "lines",
        "realign",
        "skips",
        "line_length",
        "show_end",
    ),
    "Writing": ("bound", "write_mode", "fill", "spare_room"),
}
"""The bar's sections, in order, and the controls each gathers."""

_NOT_STRING_VIEW = ("Source", "Pointers")
"""The sections about where a block's strings are, which a view of the strings'
own bytes does not show. Not Strings, which shapes the bytes themselves —
skip ranges among them, so they are editable wherever the strings are read."""


def _endian_combo(tip: str = "") -> QComboBox:
    """A byte-order picker, little first as a console's numbers are."""
    combo = QComboBox()
    combo.addItem("Little", "little")
    combo.addItem("Big", "big")
    if tip:
        combo.setToolTip(tip)
    return combo


DEFAULT_READING = BlockConfig(RangeSource(0, 0), EndToken(), "")
"""What the bar shows with nothing open: a file read as a range of strings, so
the window starts on one source's settings rather than on every source's at
once."""


def source_kind(config: BlockConfig) -> str:
    """A configuration's source kind, as the Source picker names it."""
    source = config.source
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
        self._mappings: dict[str, bool] = {}
        self._groups: dict[str, QWidget] = {}
        self.sections: dict[str, QWidget] = {}

        self.source_kind = CompactComboBox(120)
        # Each count is as wide as the values it usually holds, not its maximum.
        self.start = AddressEdit(self.spelling)
        self.stop = AddressEdit(self.spelling)
        self.count = number_spin(1, 1_000_000, 4)
        self.ptr_size = number_spin(1, 4, 1)
        self.ptr_stride = number_spin(1, 4096, 2)
        self.ptr_endian = _endian_combo()
        self.ptr_mapping = CompactComboBox(150)
        self.ptr_mapping.setEditable(True)
        self.ptr_offset = OffsetEdit()
        self.ptr_bank = HexSpinBox(0, 0xFFF, 2)
        self.ptr_addresses = hint_field(
            QLineEdit(),
            "addresses, comma separated",
            "Where each pointer sits, comma separated",
        )
        fit_chars(self.ptr_addresses, 24)
        self.spelling.changed.connect(self._respell_addresses)

        self.string_type = QComboBox()
        for label, data in (
            ("End token", END),
            ("Fixed length", FIXED_LENGTH),
            ("Length prefix", PASCAL),
            ("Next pointer", NEXT),
            ("Lines", LINES),
        ):
            self.string_type.addItem(label, data)
        self.lines = number_spin(1, 1000, 2)
        self.fixed_length = number_spin(1, 1_000_000, 3)
        self.stop_at_end = QCheckBox("Stop at end token")
        self.stop_at_end.setToolTip("End earlier at an end token")
        self.pascal_width = number_spin(1, 4, 1)
        self.pascal_endian = _endian_combo("The prefix's byte order")
        self.pascal_tokens = QCheckBox("Counts tokens")
        self.pascal_tokens.setToolTip("The prefix counts tokens by weight, not bytes")
        self.spp = number_spin(1, 64, 2)
        self.realign_m = number_spin(0, 65536, 2, off=True)
        self.realign_o = number_spin(0, 65536, 2)
        self.line_length = number_spin(0, 1_000_000, 3, off=True)
        self.show_end = QCheckBox("Show [end]")
        self.show_end.setToolTip("Show an [end] code after every fixed string")
        self.skips = SkipsPicker()

        self.bound = AddressEdit(self.spelling)
        hint_field(
            self.bound,
            "stop",
            "Writes stop before this address; blank uses the default shown",
        )
        self.write_mode = QComboBox()
        for label, mode, tip in (
            ("Automatic", None, "Packed with pointers, slotted without"),
            (
                "Packed",
                WriteMode.PACKED,
                "Strings laid end to end, every pointer rewritten",
            ),
            ("Slotted", WriteMode.SLOTTED, "Every string stays in its own place"),
        ):
            self.write_mode.addItem(label, mode)
            self.write_mode.setItemData(
                self.write_mode.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        self.fill = HexEdit(2)
        self.spare_room = QComboBox()
        self.spare_room.addItem("Fill", "fill")
        self.spare_room.addItem("Keep", "keep")
        for at, tip in (
            (0, "Pad the freed tail with the fill byte"),
            (1, "Leave the freed tail as it was"),
        ):
            self.spare_room.setItemData(at, tip, Qt.ItemDataRole.ToolTipRole)

        groups = {}
        for name, label, widgets, tip in (
            ("source_kind", "", (self.source_kind,), "Where the strings are"),
            ("start", "Start", (self.start,), "The first byte"),
            ("stop", "Stop", (self.stop,), "The first byte past the region"),
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
            (
                "ptr_addresses",
                "Addresses",
                (self.ptr_addresses,),
                "Where each pointer sits, comma separated",
            ),
            ("string_type", "Ends at", (self.string_type,), "How a string ends"),
            ("fixed_length", "Length", (self.fixed_length,), "Bytes in every string"),
            ("count", "Count", (self.count,), "How many strings; sets Stop"),
            ("stop_at_end", "", (self.stop_at_end,), None),
            (
                "pascal",
                "Prefix",
                (self.pascal_width, self.pascal_endian, self.pascal_tokens),
                "Bytes in the length prefix",
            ),
            (
                "spp",
                "Ends per string",
                (self.spp,),
                "End tokens one string runs through before it ends",
            ),
            ("lines", "Lines", (self.lines,), "Line codes one string holds"),
            (
                "realign",
                "Realign",
                (self.realign_m, QLabel("+"), self.realign_o),
                "After each end token, round the next start up to a multiple "
                "+ offset, in bytes",
            ),
            (
                "line_length",
                "Lines",
                (self.line_length,),
                "Split fixed strings into lines this many bytes long",
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
                "After a shorter re-compression: fill the slot's tail, or keep it",
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
            ("pascal_endian", self.pascal_endian),
            ("write_mode", self.write_mode),
            ("spare_room", self.spare_room),
        ):
            combo.currentIndexChanged.connect(lambda _=0, n=name: self._edited(n))
        self.ptr_mapping.activated.connect(lambda _=0: self._edited("ptr_mapping"))
        self.ptr_mapping.lineEdit().editingFinished.connect(
            lambda: self._edited("ptr_mapping")
        )
        self.count.valueChanged.connect(self._on_count)
        for name, spin in (
            ("ptr_size", self.ptr_size),
            ("ptr_stride", self.ptr_stride),
            ("ptr_bank", self.ptr_bank),
            ("fixed_length", self.fixed_length),
            ("pascal_width", self.pascal_width),
            ("spp", self.spp),
            ("lines", self.lines),
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

    def set_mappings(self, mappings) -> None:
        """The mapping plugins on offer, listed by name; a mapping id typed in
        that names none of them is kept as an id."""
        was = self.ptr_mapping.blockSignals(True)
        current = self.mapping_id()
        self._mappings = {
            m.info.id: bool(getattr(m, "needs_bank", True)) for m in mappings
        }
        self.ptr_mapping.clear()
        for m in mappings:
            self.ptr_mapping.addItem(m.info.name, m.info.id)
        if not mappings:
            self.ptr_mapping.addItem("linear", "linear")
        self._show_mapping(current or self.ptr_mapping.itemData(0))
        self.ptr_mapping.blockSignals(was)

    def suggest_mapping(self, mapping_id: str) -> None:
        """Start the pointer mapping on ``mapping_id``, applying nothing."""
        was = self.ptr_mapping.blockSignals(True)
        self._show_mapping(mapping_id)
        self.ptr_mapping.blockSignals(was)
        self._sync()

    def mapping_id(self) -> str:
        """The mapping the picker names: a listed one's id, or the id typed."""
        pick = self.ptr_mapping
        text = pick.currentText().strip()
        at = pick.findText(text)
        if at >= 0:
            return pick.itemData(at)
        return text or "linear"

    def _show_mapping(self, mapping_id: str) -> None:
        at = self.ptr_mapping.findData(mapping_id)
        if at >= 0:
            self.ptr_mapping.setCurrentIndex(at)
        else:
            self.ptr_mapping.setCurrentText(mapping_id)
        # An editable picker scrolls to the end of a long name; show its start.
        self.ptr_mapping.lineEdit().setCursorPosition(0)

    def _needs_bank(self) -> bool:
        """Whether the mapping reads a bank: a listed one that says so, or an
        id the list does not know, which is left its bank rather than guessed."""
        return self._mappings.get(self.mapping_id(), True)

    def show_bound_default(self, bound: int | None) -> None:
        """Say in the Bound field's placeholder where a blank bound stops."""
        hint_field(
            self.bound,
            "stop" if bound is None else self.spelling.format(bound),
            self.bound.toolTip(),
        )

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
                self._show_mapping(s.mapping_id)
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
                self.pascal_endian.setCurrentIndex(1 if st.endian == "big" else 0)
                self.pascal_tokens.setChecked(st.counts_tokens)
            elif isinstance(st, NextPointer):
                kind = NEXT
            elif isinstance(st, Lines):
                kind = LINES
                self.lines.setValue(st.count)
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
            self._show_count()
        finally:
            self._loading = False
        self._sync()

    def _show_count(self) -> None:
        """Say how many fixed-length strings the range holds."""
        start, stop = self.start.value(), self.stop.value()
        length = self.fixed_length.value()
        if start is None or stop is None or length < 1:
            return
        self.count.setValue(max((stop - start) // length, 1))

    def _on_count(self) -> None:
        """A count typed in moves Stop to hold that many strings."""
        if self._loading:
            return
        start = self.start.value()
        if start is not None:
            self.stop.set_value(start + self.count.value() * self.fixed_length.value())
        self._edited("count")

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
        kinds = (TABLE, LIST) if self._pointers else (RANGE,)
        for kind in kinds:
            self.source_kind.addItem(_SOURCE_NAMES[kind], kind)
        self.source_kind.setCurrentIndex(max(self.source_kind.findData(current), 0))
        # A file has no addresses of its own to list pointers at.
        if self._pointers and not self._block:
            self.source_kind.model().item(1).setEnabled(False)

    def _edited(self, name: str) -> None:
        if self._loading:
            return
        if name in ("start", "stop", "fixed_length"):
            self._loading = True
            try:
                self._show_count()
            finally:
                self._loading = False
        self._sync()
        self.edited.emit(name)

    def _sync(self) -> None:
        kind = self.source_kind.currentData()
        pointers, block = self._pointers, self._block
        st = self.string_type.currentData()
        was = self.string_type.blockSignals(True)
        self.string_type.model().item(3).setEnabled(pointers)
        self.string_type.blockSignals(was)
        shown = {
            # Text has one kind of source, so there is nothing to pick.
            "source_kind": pointers,
            "start": block and kind != LIST,
            "stop": block and kind in (RANGE, TABLE),
            "ptr_size": pointers,
            "ptr_stride": kind == TABLE,
            "ptr_endian": pointers,
            "ptr_mapping": pointers,
            "ptr_offset": pointers,
            "ptr_bank": pointers and self._needs_bank(),
            "ptr_addresses": block and kind == LIST,
            "string_type": True,
            "fixed_length": st == FIXED_LENGTH,
            "count": block and kind == RANGE and st == FIXED_LENGTH,
            "stop_at_end": st == FIXED_LENGTH,
            "pascal": st == PASCAL,
            "spp": st == END,
            "lines": st == LINES,
            "realign": True,
            "line_length": st == FIXED_LENGTH,
            "show_end": st == FIXED_LENGTH,
            "skips": block,
            "bound": block,
            "write_mode": block,
            "fill": block,
            "spare_room": block,
        }
        for name, visible in shown.items():
            self._groups[name].setVisible(visible)
        self.pascal_endian.setVisible(self.pascal_width.value() > 1)
        auto = default_write_mode(self._pointers, bool(self.skips.value()))
        self.write_mode.setItemText(0, f"Automatic ({auto.value})")
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
            "mapping_id": self.mapping_id(),
            "offset": getattr(old, "offset", 0) if offset is None else offset,
            "bank": self.ptr_bank.value(),
        }
        if kind == TABLE:
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
            "string_type": self._string_type(),
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

    def _string_type(self) -> StringType:
        st = self.string_type.currentData()
        if st == FIXED_LENGTH:
            return FixedLength(self.fixed_length.value(), self.stop_at_end.isChecked())
        if st == PASCAL:
            return Pascal(
                self.pascal_width.value(),
                self.pascal_tokens.isChecked(),
                self.pascal_endian.currentData(),
            )
        if st == NEXT and self._pointers:
            return NextPointer()
        if st == LINES:
            return Lines(self.lines.value())
        return EndToken()

    def spare_room_rule(self) -> str:
        return self.spare_room.currentData()


def source_kind_for(kind: str | None, pointers: bool) -> str:
    """``kind`` when it reads the way asked, else that way's first kind."""
    if pointers:
        return kind if kind in (TABLE, LIST) else TABLE
    return RANGE


def _or(value: int | None, default: int) -> int:
    return default if value is None else value
