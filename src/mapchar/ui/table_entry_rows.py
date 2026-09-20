"""The rows a code's operands and a switch's parameters are edited in, and the
list that stacks them.

Each row is one operand or one parameter, reading itself back as the value the
table holds; :class:`RowList` keeps rows of one kind under each other with a
button that adds one more.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.bits import bits_to_hex, hex_to_bits
from mapchar.core.table import (
    BITS,
    COUNT_SPECS,
    RAW,
    OperandSpec,
    Stop,
    SwitchParam,
)
from mapchar.ui.number_fields import HEX_NUMBER, number_spin
from mapchar.ui.widgets import fill_pick, fit_chars, hint_field, select_data

if TYPE_CHECKING:
    from collections.abc import Callable

_BIN = QRegularExpression(r"[01]*")

OPERAND_SPECS = (
    ("u8", "u8 — one byte, shown $XX"),
    ("u16", "u16 — two bytes, little-endian"),
    ("u24", "u24 — three bytes, little-endian"),
    ("u32", "u32 — four bytes, little-endian"),
    ("u16be", "u16be — two bytes, big-endian"),
    ("u24be", "u24be — three bytes, big-endian"),
    ("u32be", "u32be — four bytes, big-endian"),
    ("s8", "s8 — one signed byte, shown in decimal"),
    ("s16", "s16 — two signed bytes, little-endian"),
    ("s16be", "s16be — two signed bytes, big-endian"),
    ("bytes", "N raw bytes, shown as hex"),
    ("bits", "N raw bits, shown as %bits"),
)
"""What an operand picker offers: the named specs, then the counted ones."""

STOP_ANY, STOP_COUNT, STOP_DATA, STOP_BYTES, STOP_BITS = (
    "any",
    "count",
    "data",
    "bytes",
    "bits",
)
STOPS = (
    (
        STOP_ANY,
        "until the string ends",
        "Reads until the string ends, an end token, or a return entry of the table",
    ),
    (STOP_COUNT, "a count of", "Exactly this many weighted matches, then back"),
    (
        STOP_DATA,
        "a count read as",
        "The count is read from the data as this operand when the frame opens",
    ),
    (
        STOP_BYTES,
        "until the bytes",
        "Until these bytes appear at a token boundary; consumed, printing nothing",
    ),
    (
        STOP_BITS,
        "until the bits",
        "Until these bits appear at a token boundary; consumed, printing nothing",
    ),
)
"""How a switch parameter's frame ends: its datum, its name and its meaning."""


class OperandRow(QWidget):
    """One operand of a code entry: a spec picker, with a size for a counted
    one."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.pick = QComboBox()
        for spec, tip in OPERAND_SPECS:
            self.pick.addItem(spec, spec)
            self.pick.setItemData(
                self.pick.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        self.pick.setToolTip("What the operand reads and how it is shown")
        self.size = number_spin(1, 64, 2, value=1)
        self.size.setToolTip("How many bytes or bits")
        self.drop = _drop_button("Remove this operand")
        row.addWidget(self.pick)
        row.addWidget(self.size)
        row.addStretch(1)
        row.addWidget(self.drop)
        self.pick.currentIndexChanged.connect(self._on_pick)
        self.size.valueChanged.connect(lambda _: self.changed.emit())
        self.drop.clicked.connect(lambda: self.removed.emit(self))
        self._on_pick()

    def _on_pick(self) -> None:
        self.size.setVisible(self.pick.currentData() in ("bytes", "bits"))
        self.changed.emit()

    def spec(self) -> OperandSpec:
        name = self.pick.currentData()
        if name == "bytes":
            return OperandSpec("bytes", self.size.value() * 8)
        if name == "bits":
            return OperandSpec("bits", self.size.value())
        return OperandSpec.parse(name)

    def set_spec(self, spec: OperandSpec) -> None:
        if spec.kind == "bytes":
            select_data(self.pick, "bytes")
            self.size.setValue(spec.bits // 8)
        elif spec.kind == "bits":
            select_data(self.pick, "bits")
            self.size.setValue(spec.bits)
        else:
            select_data(self.pick, spec.spec())


class ParamRow(QWidget):
    """One parameter of a switch entry: where it reads, how it stops, and
    whether its matches count here too."""

    changed = Signal()
    removed = Signal(object)

    def __init__(self, tables: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.table = QComboBox()
        self.table.setToolTip("The table the bytes are read in")
        self.set_tables(tables)
        self.stop = QComboBox()
        for datum, name, tip in STOPS:
            self.stop.addItem(name, datum)
            self.stop.setItemData(
                self.stop.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        self.stop.setToolTip("When reading goes back")
        self.count = number_spin(1, 9999, 4, value=1)
        self.count.setToolTip("Weighted matches to read before going back")
        self.operand = QComboBox()
        self.operand.setToolTip("The operand whose value is the count")
        for spec in COUNT_SPECS:
            self.operand.addItem(spec, spec)
        self.bytes = hint_field(QLineEdit(), "FF", "The bytes, in hex")
        self.bytes.setValidator(QRegularExpressionValidator(HEX_NUMBER, self.bytes))
        fit_chars(self.bytes, 8)
        self.bits = hint_field(QLineEdit(), "101", "The bits")
        self.bits.setValidator(QRegularExpressionValidator(_BIN, self.bits))
        fit_chars(self.bits, 10)
        self.values = {
            STOP_COUNT: self.count,
            STOP_DATA: self.operand,
            STOP_BYTES: self.bytes,
            STOP_BITS: self.bits,
        }
        self.shared = QCheckBox("counts here too (+)")
        self.shared.setToolTip("Its matches also count in the table that switched here")
        self.through = QCheckBox("falls through (|)")
        self.through.setToolTip(
            "Bytes its table has no entry for are read in the table that "
            "switched here, so the table need hold only what differs"
        )
        self.drop = _drop_button("Remove this parameter")
        row.addWidget(self.table)
        row.addWidget(self.stop)
        for widget in self.values.values():
            row.addWidget(widget)
        row.addWidget(self.shared)
        row.addWidget(self.through)
        row.addStretch(1)
        row.addWidget(self.drop)
        self.table.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.stop.currentIndexChanged.connect(self._on_stop)
        self.count.valueChanged.connect(lambda _: self.changed.emit())
        self.operand.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.bytes.textChanged.connect(lambda _: self.changed.emit())
        self.bits.textChanged.connect(lambda _: self.changed.emit())
        self.shared.toggled.connect(lambda _: self.changed.emit())
        self.through.toggled.connect(lambda _: self.changed.emit())
        self.drop.clicked.connect(lambda: self.removed.emit(self))
        self._on_stop()

    def set_tables(self, tables: list[str]) -> None:
        items = [(f"@{tid}", tid) for tid in tables]
        items += [("raw — unmatched bytes", RAW), ("bits — unmatched bits", BITS)]
        fill_pick(self.table, items)

    def _on_stop(self) -> None:
        how = self.stop.currentData()
        for datum, widget in self.values.items():
            widget.setVisible(datum == how)
        self.changed.emit()

    def param(self) -> SwitchParam:
        """The parameter the row spells; raises ValueError while one part is
        still blank."""
        how = self.stop.currentData()
        if how == STOP_COUNT:
            stop = Stop(count=self.count.value())
        elif how == STOP_DATA:
            stop = Stop(operand=OperandSpec.parse(self.operand.currentData()))
        elif how == STOP_BYTES:
            digits = self.bytes.text().strip()
            if not digits or len(digits) % 2:
                raise ValueError("the bytes a parameter stops at need whole bytes")
            stop = Stop(fallback=hex_to_bits(digits))
        elif how == STOP_BITS:
            if not self.bits.text().strip():
                raise ValueError("the bits a parameter stops at are blank")
            stop = Stop(fallback=self.bits.text().strip())
        else:
            stop = Stop()
        return SwitchParam(
            self.table.currentData(),
            stop,
            self.shared.isChecked(),
            self.through.isChecked(),
        )

    def set_param(self, param: SwitchParam) -> None:
        if not select_data(self.table, param.table_id):
            # A table not loaded is still named, so the entry survives a save.
            self.table.addItem(f"@{param.table_id}", param.table_id)
            self.table.setCurrentIndex(self.table.count() - 1)
        stop = param.stop
        if stop.count is not None:
            select_data(self.stop, STOP_COUNT)
            self.count.setValue(stop.count)
        elif stop.operand is not None:
            select_data(self.stop, STOP_DATA)
            select_data(self.operand, stop.operand.spec())
        elif stop.fallback is not None and len(stop.fallback) % 8 == 0:
            select_data(self.stop, STOP_BYTES)
            self.bytes.setText(bits_to_hex(stop.fallback))
        elif stop.fallback is not None:
            select_data(self.stop, STOP_BITS)
            self.bits.setText(stop.fallback)
        else:
            select_data(self.stop, STOP_ANY)
        self.shared.setChecked(param.shared)
        self.through.setChecked(param.through)


def _drop_button(tip: str) -> QToolButton:
    button = QToolButton()
    button.setText("−")
    button.setToolTip(tip)
    button.setAutoRaise(True)
    return button


class RowList(QWidget):
    """Rows of one kind under each other, with a button to add one more."""

    changed = Signal()

    def __init__(self, make_row: Callable[[], QWidget], add_label: str, parent=None):
        super().__init__(parent)
        self._make_row = make_row
        self._rows: list[QWidget] = []
        self.box = QVBoxLayout(self)
        self.box.setContentsMargins(0, 0, 0, 0)
        self.box.setSpacing(2)
        self.add = QPushButton(add_label)
        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.addWidget(self.add)
        bottom.addStretch(1)
        self.box.addLayout(bottom)
        self.add.clicked.connect(lambda: (self.add_row(), self.changed.emit()))

    def rows(self) -> list[QWidget]:
        return list(self._rows)

    def add_row(self) -> QWidget:
        row = self._make_row()
        row.changed.connect(self.changed)
        row.removed.connect(self.remove_row)
        self._rows.append(row)
        self.box.insertWidget(len(self._rows) - 1, row)
        return row

    def remove_row(self, row: QWidget) -> None:
        if row in self._rows:
            self._rows.remove(row)
            self.box.removeWidget(row)
            row.deleteLater()
            self.changed.emit()

    def clear(self) -> None:
        for row in self._rows:
            self.box.removeWidget(row)
            row.deleteLater()
        self._rows = []


__all__ = [
    "OPERAND_SPECS",
    "STOPS",
    "STOP_ANY",
    "STOP_BITS",
    "STOP_BYTES",
    "STOP_COUNT",
    "STOP_DATA",
    "OperandRow",
    "ParamRow",
    "RowList",
]
