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

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QLabel,
    QWidget,
)

from mapchar.core.block import (
    MAX_RECORD_HEADER,
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NestedPointerSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    StringType,
)
from mapchar.ui.addresses_picker import AddressesPicker
from mapchar.ui.bars import ROW_BREAK, WrapBar
from mapchar.ui.kind_names import SOURCE_NAMES, STRING_TYPE_NAMES
from mapchar.ui.number_fields import (
    AddressEdit,
    AddressSpelling,
    HexEdit,
    HexSpinBox,
    OffsetEdit,
    number_spin,
)
from mapchar.ui.skips_picker import SkipsPicker
from mapchar.ui.widgets import (
    CompactComboBox,
    hint_field,
)
from mapchar.ui.writing_picker import WritingPicker

RANGE, TABLE, LIST, NESTED = "range", "table", "list", "nested"
"""The source kinds, as the Source picker's data."""

_SOURCE_CLASSES = {
    RANGE: RangeSource,
    TABLE: PointerTableSource,
    LIST: PointerListSource,
    NESTED: NestedPointerSource,
}
"""Which source each picker row makes, so its label comes from the one table
every surface names a source by (:mod:`mapchar.ui.kind_names`)."""

END, FIXED_LENGTH, PASCAL, NEXT, LINES = "end", "fixed", "pascal", "next", "lines"
"""The string types, as the String type picker's data."""

_STRING_TYPE_CLASSES = {
    END: EndToken,
    FIXED_LENGTH: FixedLength,
    PASCAL: Pascal,
    NEXT: NextPointer,
    LINES: Lines,
}
"""The same for the Ends-at picker's rows."""

SECTIONS = {
    "Source": ("source_kind", "start", "stop", "writing", "ptr_addresses"),
    "Pointers": (
        "ptr_size",
        "ptr_stride",
        "ptr_endian",
        "ptr_mapping",
        "ptr_offset",
        "ptr_null",
        "ptr_bank",
        "inner",
        "inner_null",
    ),
    "Strings": (
        "string_type",
        "realign",
        "header",
        "skips",
        "fixed_length",
        "count",
        "stop_at_end",
        "line_length",
        "show_end",
        "pascal",
        "spp",
        "lines",
    ),
}
"""The bar's sections, in order, and the controls each gathers.

Every section is always there, and within one the controls every reading has
come first, in one order: what does not apply is greyed where it stands, so
opening another entry moves nothing. Only a section's tail — the fields one
kind of source or string has and another has not — comes and goes, and nothing
stands after it to be pushed along."""

_TAILS = frozenset(
    {
        "ptr_addresses",
        "inner",
        "inner_null",
        "fixed_length",
        "count",
        "stop_at_end",
        "line_length",
        "show_end",
        "pascal",
        "spp",
        "lines",
    }
)
"""The controls shown only where they apply; every other one is greyed."""

_WHERE = frozenset(
    {
        "source_kind",
        "start",
        "stop",
        "ptr_addresses",
        "ptr_size",
        "ptr_stride",
        "ptr_endian",
        "ptr_mapping",
        "ptr_offset",
        "ptr_null",
        "ptr_bank",
        "inner",
        "inner_null",
    }
)
"""The controls about where a block's strings are, which a view of the strings'
own bytes greys. Not the Strings section's, which shape the bytes themselves —
skip ranges among them, so they are editable wherever the strings are read —
nor Writing."""


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
    if isinstance(source, NestedPointerSource):
        return NESTED
    return RANGE


def _null_edit(tip: str) -> HexEdit:
    """A pointer value that reaches nothing, in hex; blank for none."""
    edit = HexEdit(4, pad=False)
    hint_field(edit, "none", tip)
    return edit


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
        self.ptr_null = _null_edit("A pointer holding this value reaches no string")
        self.inner_size = number_spin(1, 4, 1)
        self.inner_size.setValue(2)
        self.inner_endian = _endian_combo("The inner pointers' byte order")
        self.inner_null = _null_edit(
            "An inner pointer holding this value reaches no string"
        )
        self.ptr_addresses = AddressesPicker(self.spelling)

        self.string_type = QComboBox()
        for data, cls in _STRING_TYPE_CLASSES.items():
            self.string_type.addItem(STRING_TYPE_NAMES[cls], data)
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
        self.header = number_spin(0, MAX_RECORD_HEADER, 2, off=True)
        self.skips = SkipsPicker()

        self.writing = WritingPicker(self.spelling)

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
            ("ptr_addresses", "Addresses", (self.ptr_addresses,), None),
            (
                "ptr_null",
                "Null",
                (self.ptr_null,),
                "A pointer value that means no string, in hex",
            ),
            (
                "inner",
                "Inner",
                (self.inner_size, self.inner_endian),
                "Bytes in an inner pointer, and their order: a record's first "
                "pointer is its inner table, its second the base those count from",
            ),
            (
                "inner_null",
                "Inner null",
                (self.inner_null,),
                "An inner pointer value that means no string, in hex",
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
            (
                "header",
                "Header",
                (self.header,),
                "Bytes in front of every string that are not text: a record's "
                "position, id or flags",
            ),
            ("skips", "Skips", (self.skips,), None),
            ("writing", "Writing", (self.writing,), None),
        ):
            groups[name] = (label, widgets, tip)
        for title, names in SECTIONS.items():
            section = self.add_section(title)
            self.sections[title] = section.parentWidget()
            for name in names:
                label, widgets, tip = groups[name]
                self._groups[name] = section.add_group(label, *widgets, tip=tip)
                # What one string type has and another has not sits on a row
                # of its own, so the row above it never changes length and
                # the section never changes height.
                if title == "Strings" and name in _TAILS:
                    self._groups[name].setProperty(ROW_BREAK, True)

        for name, combo in (
            ("source_kind", self.source_kind),
            ("ptr_endian", self.ptr_endian),
            ("inner_endian", self.inner_endian),
            ("string_type", self.string_type),
            ("pascal_endian", self.pascal_endian),
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
            ("inner_size", self.inner_size),
            ("fixed_length", self.fixed_length),
            ("pascal_width", self.pascal_width),
            ("spp", self.spp),
            ("lines", self.lines),
            ("realign_m", self.realign_m),
            ("realign_o", self.realign_o),
            ("line_length", self.line_length),
            ("header", self.header),
        ):
            spin.valueChanged.connect(lambda _=0, n=name: self._edited(n))
        self.skips.changed.connect(lambda: self._edited("skips"))
        self.ptr_addresses.changed.connect(lambda: self._edited("ptr_addresses"))
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
            ("ptr_null", self.ptr_null),
            ("inner_null", self.inner_null),
        ):
            field.editingFinished.connect(lambda n=name: self._edited(n))
        self.writing.changed.connect(self._edited)

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

    def show_bound_default(self, default: int | str | None) -> None:
        """Say in the Bound field's placeholder where a blank bound stops: at an
        address, or where the words given say."""
        self.writing.set_bound_default(default)

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
                if isinstance(s, PointerTableSource | NestedPointerSource):
                    self.start.set_value(s.start)
                    self.stop.set_value(s.stop)
                    self.ptr_stride.setValue(s.stride)
                else:
                    self.ptr_addresses.set_value(s.addresses)
                self.ptr_size.setValue(s.size)
                self.ptr_endian.setCurrentIndex(1 if s.endian == "big" else 0)
                self._show_mapping(s.mapping_id)
                self.ptr_offset.set_value(s.offset)
                self.ptr_bank.setValue(s.bank)
                self.ptr_null.set_value(s.null)
                if isinstance(s, NestedPointerSource):
                    self.inner_size.setValue(s.inner_size)
                    self.inner_endian.setCurrentIndex(
                        1 if s.inner_endian == "big" else 0
                    )
                    self.inner_null.set_value(s.inner_null)
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
            self.header.setValue(config.header)
            self.skips.set_value(config.skips)
            self.writing.load(
                config, block=block, spare_room=spare_room, compressed=compressed
            )
            self._show_count()
        finally:
            self._loading = False
        self._sync()

    def _header(self) -> int:
        """The bytes in front of every string that are not text; only a range
        has them."""
        return self.header.value() if self.source_kind.currentData() == RANGE else 0

    def _show_count(self) -> None:
        """Say how many fixed-length strings the range holds.

        As many as the reading extracts: a string takes its header and its
        length, and one is read wherever its header still begins inside the
        range, so a last string Stop cuts short is counted too.
        """
        start, stop = self.start.value(), self.stop.value()
        length, header = self.fixed_length.value(), self._header()
        if start is None or stop is None or length < 1:
            return
        self.count.setValue(max(-(-(stop - start - header) // (length + header)), 1))

    def _on_count(self) -> None:
        """A count typed in moves Stop to hold that many strings."""
        if self._loading:
            return
        start = self.start.value()
        if start is not None:
            record = self.fixed_length.value() + self._header()
            self.stop.set_value(start + self.count.value() * record)
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
        kinds = (TABLE, LIST, NESTED) if self._pointers else (RANGE,)
        for kind in kinds:
            self.source_kind.addItem(SOURCE_NAMES[_SOURCE_CLASSES[kind]], kind)
        self.source_kind.setCurrentIndex(max(self.source_kind.findData(current), 0))
        # A file has no addresses of its own to list pointers at, nor strings
        # of its own to group.
        if self._pointers and not self._block:
            for at in (1, 2):
                self.source_kind.model().item(at).setEnabled(False)

    def _edited(self, name: str) -> None:
        if self._loading:
            return
        if name in ("start", "stop", "fixed_length", "header"):
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
        applies = {
            # Text has one kind of source, so there is nothing to pick.
            "source_kind": pointers,
            "start": block and kind != LIST,
            "stop": block and kind != LIST,
            "writing": block,
            "ptr_addresses": block and kind == LIST,
            "ptr_size": pointers,
            "ptr_stride": kind in (TABLE, NESTED),
            "ptr_endian": pointers,
            "ptr_mapping": pointers,
            "ptr_offset": pointers,
            "ptr_null": pointers,
            "ptr_bank": pointers and self._needs_bank(),
            "inner": kind == NESTED,
            "inner_null": kind == NESTED,
            "string_type": True,
            "realign": True,
            "header": block and kind == RANGE,
            "skips": block,
            "fixed_length": st == FIXED_LENGTH,
            "count": block and kind == RANGE and st == FIXED_LENGTH,
            "stop_at_end": st == FIXED_LENGTH,
            "line_length": st == FIXED_LENGTH,
            "show_end": st == FIXED_LENGTH,
            "pascal": st == PASCAL,
            "spp": st == END,
            "lines": st == LINES,
        }
        for name, applying in applies.items():
            group = self._groups[name]
            if name in _TAILS:
                # A string that ends at the next pointer has no field of its
                # own; the row keeps one, greyed, rather than closing up.
                group.setVisible(applying or (name == "spp" and st == NEXT))
            group.setEnabled(applying and not (self._string_view and name in _WHERE))
        self.pascal_endian.setVisible(self.pascal_width.value() > 1)
        forced = bool(self.skips.value()) or (
            kind == RANGE and bool(self.header.value())
        )
        self.writing.show_forced(self._pointers, forced)

    def show_string_view(self, string_view: bool) -> None:
        """Grey what says where the strings are — for a view of strings' own
        bytes — or, with ``False``, leave every control the reading uses live."""
        if string_view != self._string_view:
            self._string_view = string_view
            self._sync()

    # -- reading back ---------------------------------------------------------

    def config(self, base: BlockConfig, table_id: str) -> BlockConfig:
        """The reading the controls show, over ``base`` for what they do not.

        A file's source has no addresses in the bar, so they stay ``base``'s.
        A number nobody can read keeps ``base``'s value rather than a guess —
        a pointer list drops the one row instead, since its addresses are read
        one to a row.
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
            "null": self.ptr_null.value(),
        }
        if kind == TABLE:
            source = PointerTableSource(
                start, stop, stride=self.ptr_stride.value(), **pointer
            )
        elif kind == NESTED:
            source = NestedPointerSource(
                start,
                stop,
                stride=self.ptr_stride.value(),
                inner_size=self.inner_size.value(),
                inner_endian=self.inner_endian.currentData(),
                inner_null=self.inner_null.value(),
                **pointer,
            )
        elif kind == LIST:
            source = PointerListSource(self.ptr_addresses.value(), **pointer)
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
            bound, write_mode, fill = self.writing.values()
            changes |= {
                "header": self.header.value(),
                "skips": self.skips.value(),
                "bound": bound,
                "write_mode": write_mode,
                "fill": base.fill if fill is None else fill,
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
        return self.writing.spare_room_rule()


def source_kind_for(kind: str | None, pointers: bool) -> str:
    """``kind`` when it reads the way asked, else that way's first kind."""
    if pointers:
        return kind if kind in (TABLE, LIST, NESTED) else TABLE
    return RANGE


def _or(value: int | None, default: int) -> int:
    return default if value is None else value
