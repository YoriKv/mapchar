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
    Pascal,
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
    ):
        super().__init__(parent)
        self.setWindowTitle("Block")
        form = QFormLayout(self)
        self.name = QLineEdit(name)
        form.addRow("Name", self.name)
        self.source_kind = QComboBox()
        self.source_kind.addItems(["Range", "Fixed strings"])
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
        self.string_type = QComboBox()
        self.string_type.addItems(["End token", "Fixed length", "Pascal"])
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
        st = c.string_type
        if isinstance(st, FixedLength):
            self.string_type.setCurrentIndex(1)
            self.fixed_length.setValue(st.length)
            self.stop_at_end.setChecked(st.stop_at_end)
        elif isinstance(st, Pascal):
            self.string_type.setCurrentIndex(2)
            self.pascal_width.setValue(st.width)
            self.pascal_tokens.setChecked(st.counts_tokens)
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
        fixed_source = self.source_kind.currentIndex() == 1
        self.stop.setEnabled(not fixed_source)
        self.count.setEnabled(fixed_source)
        self.length.setEnabled(fixed_source)
        st = self.string_type.currentIndex()
        self.string_type.setEnabled(not fixed_source)
        self.fixed_length.setEnabled(st == 1 and not fixed_source)
        self.stop_at_end.setEnabled(st == 1 or fixed_source)
        self.pascal_width.setEnabled(st == 2 and not fixed_source)
        self.pascal_tokens.setEnabled(st == 2 and not fixed_source)
        self.spp.setEnabled(st == 0 and not fixed_source)
        self.line_length.setEnabled(st == 1 or fixed_source)
        self.show_end.setEnabled(st == 1 or fixed_source)

    def config(self) -> BlockConfig:
        if self.source_kind.currentIndex() == 1:
            source = FixedSource(
                self.start.value(), self.count.value(), self.length.value()
            )
            string_type = FixedLength(self.length.value(), self.stop_at_end.isChecked())
        else:
            source = RangeSource(self.start.value(), self.stop.value())
            st = self.string_type.currentIndex()
            if st == 1:
                string_type = FixedLength(
                    self.fixed_length.value(), self.stop_at_end.isChecked()
                )
            elif st == 2:
                string_type = Pascal(
                    self.pascal_width.value(), self.pascal_tokens.isChecked()
                )
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
