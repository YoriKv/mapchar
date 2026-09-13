"""Dialogs: block configuration, dump, shortcuts."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLineEdit,
    QPlainTextEdit,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

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
)
from mapchar.project.formats.script import DumpMode


def parse_hex(text: str, default: int = 0) -> int:
    text = text.strip().replace("$", "").replace("0x", "").replace("_", "")
    if not text:
        return default
    return int(text, 16)


class HexEdit(QLineEdit):
    def __init__(self, value: int = 0, parent: QWidget | None = None):
        super().__init__(f"{value:X}", parent)
        self.setPlaceholderText("hex")

    def value(self) -> int:
        return parse_hex(self.text())

    def set_value(self, value: int) -> None:
        self.setText(f"{value:X}")


class BlockDialog(QDialog):
    def __init__(
        self,
        table_ids: list[str],
        config: BlockConfig | None = None,
        name: str = "",
        parent: QWidget | None = None,
        mapping_ids: list[str] = (),
    ):
        super().__init__(parent)
        self.setWindowTitle("Block")
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        form.addRow("Name", self.name)
        self.source_kind = QComboBox()
        self.source_kind.addItems(
            ["Range", "Fixed strings", "Pointer table", "Pointer list"]
        )
        form.addRow("Source", self.source_kind)
        self.start = HexEdit()
        self.stop = HexEdit()
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
        self.ptr_offset = QLineEdit("0")
        self.ptr_bank = QSpinBox()
        self.ptr_bank.setRange(0, 4095)
        self.ptr_addresses = QLineEdit()
        self.ptr_addresses.setPlaceholderText("hex addresses, comma separated")
        form.addRow("Pointer size", self.ptr_size)
        form.addRow("Pointer stride", self.ptr_stride)
        form.addRow("Pointer endian", self.ptr_endian)
        form.addRow("Mapping", self.ptr_mapping)
        form.addRow("Target offset (±dec or $hex)", self.ptr_offset)
        form.addRow("Bank", self.ptr_bank)
        form.addRow("Pointer addresses", self.ptr_addresses)
        self.string_type = QComboBox()
        self.string_type.addItems(
            ["End token", "Fixed length", "Pascal", "Next pointer"]
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
        form.addRow("Start table", self.table)
        self.spp = QSpinBox()
        self.spp.setRange(1, 64)
        form.addRow("End tokens per string", self.spp)
        self.realign_m = QSpinBox()
        self.realign_m.setRange(0, 65536)
        self.realign_o = QSpinBox()
        self.realign_o.setRange(0, 65536)
        form.addRow("Realign multiple", self.realign_m)
        form.addRow("Realign offset", self.realign_o)
        self.line_length = QSpinBox()
        self.line_length.setRange(0, 1_000_000)
        form.addRow("Fixed line length (0: off)", self.line_length)
        self.show_end = QCheckBox("Show [end] after fixed strings")
        form.addRow("", self.show_end)
        self.skips = QLineEdit()
        self.skips.setPlaceholderText("from>to, from>to  (hex)")
        form.addRow("Skip ranges", self.skips)
        self.bound = HexEdit()
        self.bound.setText("")
        form.addRow("Write bound (blank: stop)", self.bound)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
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
        self.line_length.setEnabled(st == 1 or fixed_source)
        self.show_end.setEnabled(st == 1 or fixed_source)

    def _pointer_fields(self) -> dict:
        offset_text = self.ptr_offset.text().strip() or "0"
        if offset_text.lstrip("-").startswith("$"):
            offset = int(offset_text.replace("$", ""), 16)
        else:
            offset = int(offset_text)
        return {
            "size": self.ptr_size.value(),
            "endian": self.ptr_endian.currentText(),
            "mapping_id": self.ptr_mapping.currentText().strip() or "linear",
            "offset": offset,
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
            show_end=self.show_end.isChecked(),
        )


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
    """A read-only text box, for shortcuts and notices."""

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


class DiscoveryDialog(QDialog):
    """Pointer discovery results; the chosen candidate becomes the source."""

    def __init__(self, candidates, parent: QWidget | None = None):
        super().__init__(parent)
        from PySide6.QtWidgets import QTableWidget, QTableWidgetItem

        self.setWindowTitle("Find Pointers")
        self.candidates = candidates
        layout = QVBoxLayout(self)
        self.table = QTableWidget(len(candidates), 6)
        self.table.setHorizontalHeaderLabels(
            ["Mapping", "Size", "Endian", "Strings", "Stride", "First address"]
        )
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        for row, c in enumerate(candidates):
            cells = [
                c.mapping_id,
                str(c.size),
                c.endian,
                str(c.explained),
                str(c.stride),
                f"{c.addresses[0]:X}" if c.addresses else "",
            ]
            for col, text in enumerate(cells):
                self.table.setItem(row, col, QTableWidgetItem(text))
        self.table.resizeColumnsToContents()
        if candidates:
            self.table.selectRow(0)
        layout.addWidget(self.table, 1)
        buttons = QDialogButtonBox()
        buttons.addButton(
            "Use as pointer table", QDialogButtonBox.ButtonRole.AcceptRole
        )
        buttons.addButton(QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.resize(560, 320)

    def chosen(self):
        rows = self.table.selectionModel().selectedRows()
        return self.candidates[rows[0].row()] if rows else None
