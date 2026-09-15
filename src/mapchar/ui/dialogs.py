"""Dialogs: block configuration, the file container, dump, reports, pointers."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
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
    WriteMode,
)
from mapchar.core.numbers import format_num, parse_num
from mapchar.project.formats.script import DumpMode
from mapchar.ui.widgets import ResultsTable, hint_field, show_elided_tooltips


def _form_group(title: str) -> tuple[QGroupBox, QFormLayout]:
    """A titled box holding a form, for a dialog long enough to need sections."""
    box = QGroupBox(title)
    return box, QFormLayout(box)


class HexEdit(QLineEdit):
    def __init__(self, value: int = 0, parent: QWidget | None = None):
        super().__init__(f"{value:X}", parent)
        self.setPlaceholderText("hex")

    def value(self) -> int:
        return parse_hex(self.text())

    def set_value(self, value: int) -> None:
        self.setText(f"{value:X}")


class BlockDialog(QDialog):
    """New Block… and a block's Edit…: the whole of a block's configuration.

    The compression row is what makes a block a *decompressed* region rather
    than a window on the file's own bytes, so it carries the spare-room rule
    with it: a re-compression that comes out shorter than the slot it replaces
    has to say what fills the rest.
    """

    def __init__(
        self,
        table_ids: list[str],
        config: BlockConfig | None = None,
        name: str = "",
        parent: QWidget | None = None,
        mapping_ids: list[str] = (),
        *,
        compression_items: list[tuple[str, object]] = (),
        compression_id: str | None = None,
        spare_room: str = "fill",
        suggested_mapping: str | None = None,
        title: str = "Block",
    ):
        super().__init__(parent)
        self.setWindowTitle(title)
        # Three groups in two columns — where the strings are, how they are cut,
        # how they go back — rather than one form a screen tall: the dialog has
        # to fit a laptop with its OK button on screen.
        name_form = QFormLayout()
        self.name = QLineEdit(name)
        name_form.addRow("Name", self.name)
        source_box, form = _form_group("Source")
        self.source_kind = QComboBox()
        self.source_kind.addItems(
            ["Range", "Fixed strings", "Pointer table", "Pointer list"]
        )
        form.addRow("Source", self.source_kind)
        self.start = HexEdit()
        self.stop = HexEdit()
        self.stop.setToolTip("The first byte past the region, in hex")
        self.count = QSpinBox()
        self.count.setRange(1, 1_000_000)
        self.length = QSpinBox()
        self.length.setRange(1, 1_000_000)
        form.addRow("Start", self.start)
        form.addRow("Stop (exclusive)", self.stop)
        form.addRow("Count", self.count)
        form.addRow("String length", self.length)
        self.ptr_size = QSpinBox()
        self.ptr_size.setRange(1, 4)
        self.ptr_size.setValue(2)
        self.ptr_stride = QSpinBox()
        self.ptr_stride.setRange(1, 4096)
        self.ptr_stride.setValue(2)
        self.ptr_endian = QComboBox()
        self.ptr_endian.addItems(["little", "big"])
        self.ptr_mapping = QComboBox()
        self.ptr_mapping.setEditable(True)
        self.ptr_mapping.addItems(list(mapping_ids) or ["linear"])
        # The container publishes what the header says the ROM is mapped as
        # (``KEY_SUGGESTED_MAPPING``); a new block starts on that rather than on
        # linear, which is wrong for every banked ROM.
        if suggested_mapping and config is None:
            self.ptr_mapping.setCurrentText(suggested_mapping)
        self.ptr_offset = QLineEdit("0")
        self.ptr_offset.setToolTip(
            "Added to every pointer value: decimal or $hex, with a leading - to "
            "subtract"
        )
        self.ptr_bank = QSpinBox()
        self.ptr_bank.setRange(0, 4095)
        self.ptr_addresses = hint_field(QLineEdit(), "hex addresses, comma separated")
        form.addRow("Pointer size", self.ptr_size)
        form.addRow("Pointer stride", self.ptr_stride)
        form.addRow("Pointer endian", self.ptr_endian)
        form.addRow("Mapping", self.ptr_mapping)
        form.addRow("Target offset", self.ptr_offset)
        form.addRow("Bank", self.ptr_bank)
        form.addRow("Pointer addresses", self.ptr_addresses)
        strings_box, form = _form_group("Strings")
        self.string_type = QComboBox()
        self.string_type.addItems(
            ["End token", "Fixed length", "Pascal (length prefix)", "Next pointer"]
        )
        form.addRow("String type", self.string_type)
        self.fixed_length = QSpinBox()
        self.fixed_length.setRange(1, 1_000_000)
        form.addRow("Fixed length", self.fixed_length)
        self.stop_at_end = QCheckBox("Stop at end token")
        form.addRow("", self.stop_at_end)
        self.pascal_width = QSpinBox()
        self.pascal_width.setRange(1, 4)
        form.addRow("Pascal width", self.pascal_width)
        self.pascal_tokens = QCheckBox("Length counts token weights")
        form.addRow("", self.pascal_tokens)
        self.table = QComboBox()
        self.table.addItems(table_ids)
        form.addRow("Table", self.table)
        self.spp = QSpinBox()
        self.spp.setRange(1, 64)
        form.addRow("End tokens per string", self.spp)
        # A zero is a switch left off, and says so rather than showing a number
        # the reader has to know the meaning of.
        self.realign_m = QSpinBox()
        self.realign_m.setRange(0, 65536)
        self.realign_m.setSpecialValueText("off")
        self.realign_o = QSpinBox()
        self.realign_o.setRange(0, 65536)
        form.addRow("Realign multiple", self.realign_m)
        form.addRow("Realign offset", self.realign_o)
        self.line_length = QSpinBox()
        self.line_length.setRange(0, 1_000_000)
        self.line_length.setSpecialValueText("off")
        form.addRow("Fixed line length", self.line_length)
        self.show_end = QCheckBox("Show [end] after fixed strings")
        form.addRow("", self.show_end)
        self.skips = hint_field(
            QLineEdit(),
            "from>to, from>to  (hex)",
            "Byte ranges inside the region that are not text, in hex",
        )
        form.addRow("Skip ranges", self.skips)
        writing_box, form = _form_group("Writing")
        self.bound = HexEdit()
        self.bound.setText("")
        hint_field(
            self.bound,
            "the region's stop",
            "The last address a write may reach, in hex; blank stops at the "
            "region's end",
        )
        form.addRow("Write bound", self.bound)
        self.write_mode = QComboBox()
        self.write_mode.addItem("Automatic", None)
        self.write_mode.addItem("Packed", WriteMode.PACKED)
        self.write_mode.addItem("Slotted", WriteMode.SLOTTED)
        form.addRow("Write mode", self.write_mode)
        self.fill = HexEdit(0xFF)
        form.addRow("Fill byte", self.fill)
        self.compression = QComboBox()
        self.compression.addItem("None (the file's own bytes)", None)
        for label, data in compression_items:
            self.compression.addItem(label, data)
        form.addRow("Compression", self.compression)
        self.spare_room = QComboBox()
        self.spare_room.addItem("Fill with the fill byte", "fill")
        self.spare_room.addItem("Keep the bytes that were there", "keep")
        form.addRow("Spare room", self.spare_room)
        at = self.compression.findData(compression_id)
        self.compression.setCurrentIndex(max(at, 0))
        at = self.spare_room.findData(spare_room)
        self.spare_room.setCurrentIndex(max(at, 0))
        self.compression.currentIndexChanged.connect(self._sync)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        left = QVBoxLayout()
        left.addWidget(source_box)
        left.addStretch(1)
        right = QVBoxLayout()
        right.addWidget(strings_box)
        right.addWidget(writing_box)
        right.addStretch(1)
        columns = QHBoxLayout()
        columns.addLayout(left, 1)
        columns.addLayout(right, 1)
        layout = QVBoxLayout(self)
        layout.addLayout(name_form)
        layout.addLayout(columns, 1)
        layout.addWidget(buttons)
        self.source_kind.currentIndexChanged.connect(self._sync)
        self.string_type.currentIndexChanged.connect(self._sync)
        if config is not None:
            self._load(config)
        self._sync()

    def _load(self, c: BlockConfig) -> None:
        s = c.source
        if isinstance(s, RangeSource):
            self.source_kind.setCurrentIndex(0)
            self.start.set_value(s.start)
            self.stop.set_value(s.stop)
        elif isinstance(s, FixedSource):
            self.source_kind.setCurrentIndex(1)
            self.start.set_value(s.start)
            self.count.setValue(s.count)
            self.length.setValue(s.length)
        elif isinstance(s, PointerTableSource | PointerListSource):
            self.source_kind.setCurrentIndex(
                2 if isinstance(s, PointerTableSource) else 3
            )
            if isinstance(s, PointerTableSource):
                self.start.set_value(s.start)
                self.stop.set_value(s.stop)
                self.ptr_stride.setValue(s.stride)
            else:
                self.ptr_addresses.setText(", ".join(f"{a:X}" for a in s.addresses))
            self.ptr_size.setValue(s.size)
            self.ptr_endian.setCurrentIndex(1 if s.endian == "big" else 0)
            self.ptr_mapping.setCurrentText(s.mapping_id)
            self.ptr_offset.setText(str(s.offset))
            self.ptr_bank.setValue(s.bank)
        st = c.string_type
        if isinstance(st, FixedLength):
            self.string_type.setCurrentIndex(1)
            self.fixed_length.setValue(st.length)
            self.stop_at_end.setChecked(st.stop_at_end)
        elif isinstance(st, Pascal):
            self.string_type.setCurrentIndex(2)
            self.pascal_width.setValue(st.width)
            self.pascal_tokens.setChecked(st.counts_tokens)
        elif isinstance(st, NextPointer):
            self.string_type.setCurrentIndex(3)
        i = self.table.findText(c.table_id)
        if i >= 0:
            self.table.setCurrentIndex(i)
        self.spp.setValue(c.strings_per_pointer)
        self.realign_m.setValue(c.realign[0])
        self.realign_o.setValue(c.realign[1])
        self.line_length.setValue(c.line_length)
        self.show_end.setChecked(c.show_end)
        self.skips.setText(", ".join(f"{a:X}>{b:X}" for a, b in c.skips))
        if c.bound is not None:
            self.bound.set_value(c.bound)
        self.write_mode.setCurrentIndex(max(self.write_mode.findData(c.write_mode), 0))
        self.fill.set_value(c.fill)

    def _sync(self) -> None:
        kind = self.source_kind.currentIndex()
        fixed_source = kind == 1
        pointers = kind in (2, 3)
        self.start.setEnabled(kind != 3)
        self.stop.setEnabled(kind in (0, 2))
        self.count.setEnabled(fixed_source)
        self.length.setEnabled(fixed_source)
        for w in (
            self.ptr_size,
            self.ptr_endian,
            self.ptr_mapping,
            self.ptr_offset,
            self.ptr_bank,
        ):
            w.setEnabled(pointers)
        self.ptr_stride.setEnabled(kind == 2)
        self.ptr_addresses.setEnabled(kind == 3)
        st = self.string_type.currentIndex()
        self.string_type.setEnabled(not fixed_source)
        self.string_type.model().item(3).setEnabled(pointers)
        if st == 3 and not pointers:
            self.string_type.setCurrentIndex(0)
            st = 0
        self.fixed_length.setEnabled(st == 1 and not fixed_source)
        self.stop_at_end.setEnabled(st == 1 or fixed_source)
        self.pascal_width.setEnabled(st == 2 and not fixed_source)
        self.pascal_tokens.setEnabled(st == 2 and not fixed_source)
        self.spp.setEnabled(st == 0 and not fixed_source)
        # Spare room is what a re-compression that came out short leaves behind,
        # so it says nothing at all about a block read straight from the file.
        self.spare_room.setEnabled(self.compression.currentData() is not None)
        self.line_length.setEnabled(st == 1 or fixed_source)
        self.show_end.setEnabled(st == 1 or fixed_source)

    def _target_offset(self) -> int | None:
        """The offset added to every pointer value, or ``None`` if unreadable.

        One spelling for every number in the app
        (:func:`~mapchar.core.numbers.parse_num`): decimal, or ``$hex``, either
        sign. A field nobody filled in adds nothing.
        """
        text = self.ptr_offset.text().strip()
        if not text:
            return 0
        try:
            return parse_num(text)
        except ValueError:
            return None

    def accept(self) -> None:
        """Refuse to close on a number nobody can read.

        Checked here rather than in :meth:`config`, which every caller reaches
        only *after* the dialog has closed: an exception out of it would leave a
        Qt slot carrying the traceback and the block half made.
        """
        if self.ptr_offset.isEnabled() and self._target_offset() is None:
            QMessageBox.warning(
                self,
                self.windowTitle(),
                f"{self.ptr_offset.text().strip()!r} is not a number. Write a "
                "decimal or a $hex value, with a leading - to subtract.",
            )
            self.ptr_offset.setFocus()
            self.ptr_offset.selectAll()
            return
        super().accept()

    def _pointer_fields(self) -> dict:
        offset = self._target_offset()
        return {
            "size": self.ptr_size.value(),
            "endian": self.ptr_endian.currentText(),
            "mapping_id": self.ptr_mapping.currentText().strip() or "linear",
            "offset": 0 if offset is None else offset,
            "bank": self.ptr_bank.value(),
        }

    def config(self) -> BlockConfig:
        kind = self.source_kind.currentIndex()
        if kind == 1:
            source = FixedSource(
                self.start.value(), self.count.value(), self.length.value()
            )
            string_type = FixedLength(self.length.value(), self.stop_at_end.isChecked())
        else:
            if kind == 0:
                source = RangeSource(self.start.value(), self.stop.value())
            elif kind == 2:
                source = PointerTableSource(
                    self.start.value(),
                    self.stop.value(),
                    stride=self.ptr_stride.value(),
                    **self._pointer_fields(),
                )
            else:
                addresses = tuple(
                    parse_hex(a)
                    for a in self.ptr_addresses.text().split(",")
                    if a.strip()
                )
                source = PointerListSource(addresses, **self._pointer_fields())
            st = self.string_type.currentIndex()
            if st == 1:
                string_type = FixedLength(
                    self.fixed_length.value(), self.stop_at_end.isChecked()
                )
            elif st == 2:
                string_type = Pascal(
                    self.pascal_width.value(), self.pascal_tokens.isChecked()
                )
            elif st == 3:
                string_type = NextPointer()
            else:
                string_type = EndToken()
        skips = []
        for pair in self.skips.text().split(","):
            if ">" in pair:
                a, b = pair.split(">", 1)
                skips.append((parse_hex(a), parse_hex(b)))
        bound_text = self.bound.text().strip()
        return BlockConfig(
            source=source,
            string_type=string_type,
            table_id=self.table.currentText(),
            strings_per_pointer=self.spp.value(),
            realign=(self.realign_m.value(), self.realign_o.value()),
            skips=tuple(skips),
            line_length=self.line_length.value(),
            bound=parse_hex(bound_text) if bound_text else None,
            write_mode=self.write_mode.currentData(),
            fill=self.fill.value() & 0xFF,
            show_end=self.show_end.isChecked(),
        )

    def compression_id(self) -> str | None:
        """The scheme this block decompresses through, or ``None`` for the
        file's own bytes."""
        return self.compression.currentData()

    def spare_room_rule(self) -> str:
        return self.spare_room.currentData()


class ContainerDialog(QDialog):
    """Edit File Container…: what the region is made of and how it is unwrapped.

    The files list is how split ROM chips are joined, and the order in it is the
    order offsets are counted in, so it is reorderable rather than a fixed echo
    of how the entry was opened. Applying re-reads the entry.
    """

    def __init__(
        self,
        container_items: list[tuple[str, object]],
        paths: tuple[str, ...] | list[str],
        container_id: str = "raw",
        detected: str | None = None,
        readonly_ids: frozenset[str] = frozenset(),
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Edit File Container")
        self._readonly = readonly_ids
        layout = QVBoxLayout(self)
        self.files = QListWidget()
        # A path is cut in its middle, so both the drive and the file name stay
        # in sight, and hovering it reads the rest.
        self.files.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        show_elided_tooltips(self.files)
        for path in paths:
            self.files.addItem(path)
        self.files.setCurrentRow(0)
        layout.addWidget(QLabel("Files, joined end to end in this order:"))
        layout.addWidget(self.files, 1)
        row = QHBoxLayout()
        for label, slot in (
            ("Move Up", lambda: self._move(-1)),
            ("Move Down", lambda: self._move(1)),
            ("Append…", self._append),
            ("Remove", self._remove),
        ):
            button = QPushButton(label)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)
        layout.addLayout(row)
        form = QFormLayout()
        self.container = QComboBox()
        for label, data in container_items:
            mark = "  (detected)" if data == detected else ""
            self.container.addItem(f"{label}{mark}", data)
        self.container.setCurrentIndex(max(self.container.findData(container_id), 0))
        form.addRow("Container", self.container)
        layout.addLayout(form)
        self.note = QLabel("")
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.container.currentIndexChanged.connect(self._sync)
        self.resize(520, 320)
        self._sync()

    def _sync(self) -> None:
        if self.container.currentData() in self._readonly:
            self.note.setText(
                "This container has no way to put the bytes back, so the entry "
                "will open view-only."
            )
        else:
            self.note.setText("")

    def _move(self, delta: int) -> None:
        at = self.files.currentRow()
        to = at + delta
        if at < 0 or not 0 <= to < self.files.count():
            return
        item = self.files.takeItem(at)
        self.files.insertItem(to, item)
        self.files.setCurrentRow(to)

    def _append(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Append File")
        if path:
            self.files.addItem(path)
            self.files.setCurrentRow(self.files.count() - 1)

    def _remove(self) -> None:
        # Never the last one: a region with no files is not a region.
        if self.files.count() > 1 and self.files.currentRow() >= 0:
            self.files.takeItem(self.files.currentRow())

    def paths(self) -> tuple[str, ...]:
        return tuple(self.files.item(i).text() for i in range(self.files.count()))

    def container_id(self) -> str:
        return str(self.container.currentData())


class DumpDialog(QDialog):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Dump")
        form = QFormLayout(self)
        self.mode = QComboBox()
        self.mode.addItem("Originals", DumpMode.ORIGINALS)
        self.mode.addItem("Translations", DumpMode.TRANSLATIONS)
        self.mode.addItem("Both (original as comments)", DumpMode.BOTH)
        form.addRow("Content", self.mode)
        self.all_blocks = QCheckBox("Every block of the file")
        form.addRow("", self.all_blocks)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def dump_mode(self) -> DumpMode:
        return self.mode.currentData()


class TextDialog(QDialog):
    """A read-only text box, for reports and notices."""

    def __init__(self, title: str, text: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title)
        layout = QVBoxLayout(self)
        box = QPlainTextEdit(text)
        box.setReadOnly(True)
        layout.addWidget(box)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.clicked.connect(self.accept)
        layout.addWidget(buttons)
        self.resize(560, 420)


class PointerSearchDialog(QDialog):
    """What the pointer search should cover, asked before it runs.

    Both answers change the candidate space the search has to walk, so neither
    can be settled afterwards. **Scope** is about cost — one string is a search
    short enough to repeat while trying offsets, a hundred is not — and the
    **offset range** is the one part of a pointer's arithmetic a ROM chooses
    freely, so a table based somewhere other than the string it names is found
    only by trying the offsets it might be based on.
    """

    def __init__(
        self, strings: int, selected: int | None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.setWindowTitle("Find Pointers")
        form = QFormLayout(self)
        self.scope = QComboBox()
        self.scope.addItem(f"Every string in the block ({strings})", False)
        if selected is not None:
            self.scope.addItem(f"The selected string only (#{selected})", True)
        form.addRow("Look for", self.scope)
        self.offset_from = QLineEdit("0")
        self.offset_to = QLineEdit("0")
        self.offset_step = QLineEdit("1")
        for field, what in (
            (self.offset_from, "The first offset a pointer may be based on"),
            (self.offset_to, "The last offset a pointer may be based on"),
            (self.offset_step, "How far apart the offsets tried are"),
        ):
            field.setToolTip(f"{what}: decimal or $hex, with a leading - to subtract")
        form.addRow("Offset from", self.offset_from)
        form.addRow("Offset to", self.offset_to)
        form.addRow("Offset step", self.offset_step)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def selected_only(self) -> bool:
        return bool(self.scope.currentData())

    def _numbers(self) -> tuple[int, int, int] | None:
        """The three offset fields, or ``None`` if one of them does not read."""
        try:
            return (
                parse_num(self.offset_from.text().strip() or "0"),
                parse_num(self.offset_to.text().strip() or "0"),
                parse_num(self.offset_step.text().strip() or "1"),
            )
        except ValueError:
            return None

    def offsets(self) -> tuple[int, ...]:
        """The offsets to try, both ends included.

        A step under one, or an end before the start, is the single offset the
        range begins at — the fields were not really a range. How long a wide
        range then takes is the search's Stop button's problem, not this
        dialog's.
        """
        first, last, step = self._numbers() or (0, 0, 1)
        if step < 1 or last < first:
            return (first,)
        return tuple(range(first, last + 1, step))

    def accept(self) -> None:
        # Refused here rather than in :meth:`offsets`, which every caller
        # reaches only after the dialog has closed.
        if self._numbers() is None:
            QMessageBox.warning(
                self,
                "Find Pointers",
                "The offset range is not made of numbers. Write decimal or "
                "$hex values, with a leading - to subtract.",
            )
            return
        super().accept()


class DiscoveryDialog(QDialog):
    """Pointer discovery results, and which of two things to do with one.

    A discovery answers two different questions, so it has two ways out. **Use
    as pointer table** believes the whole result: the block's source becomes the
    pointer table the candidate describes and the strings are re-read through it.
    **Attach** believes only the addresses: the source stays as it was and the
    strings gain the pointers that reach them, which is what a block whose
    pointers are scattered rather than tabulated needs.
    """

    def __init__(self, candidates, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle("Find Pointers")
        self.candidates = candidates
        self.attach = False
        """Whether the result was taken by Attach rather than as the source."""
        layout = QVBoxLayout(self)
        self.table = ResultsTable(
            ["Mapping", "Size", "Endian", "Offset", "Strings", "Stride", "Addresses"]
        )
        self.table.fill(
            [
                c.mapping_id,
                str(c.size),
                c.endian,
                format_num(c.offset),
                str(c.explained),
                str(c.stride),
                f"{c.addresses[0]:X}–{c.addresses[-1]:X}" if c.addresses else "",
            ]
            for c in candidates
        )
        if candidates:
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        buttons.addButton(
            "Use as Pointer Table", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.attach_button = buttons.addButton(
            "Attach", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        # The box emits ``clicked`` before ``accepted``, so which button was
        # taken is known by the time the dialog closes on it.
        buttons.clicked.connect(self._on_clicked)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(620, 320)

    def _on_clicked(self, button) -> None:
        self.attach = button is self.attach_button

    def chosen(self):
        return self.table.pick(self.candidates)
