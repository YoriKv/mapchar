"""Fields for numbers, sized to what they are expected to hold and spelled one
way everywhere.

- **Counts** are spin boxes as wide as the values a user is expected to put in
  them (:func:`number_spin`, :func:`fit_spin`), not as their range's maximum.
- **Addresses** — file offsets — are :class:`AddressEdit`s, one width, spelled
  the way the navigation bar's address format spells a position
  (:class:`AddressSpelling`), and re-spelled when the format changes.
- **Offsets** — signed amounts added to a value — are :class:`OffsetEdit`s, the
  addresses' width, in hex with a leading ``-`` to subtract.
- **Other hex numbers** — a byte, a bank — are :class:`HexEdit`s and
  :class:`HexSpinBox`es, padded to their digits.

``docs/ui.md`` holds the rules.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QRegularExpression, QSignalBlocker, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QDoubleSpinBox,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from mapchar.core.address import AddressLayout, format_address, parse_address
from mapchar.core.numbers import format_hex_offset, parse_hex, parse_hex_offset
from mapchar.ui.widgets import fit_chars

ADDRESS_CHARS = 10
"""Room for the longest spelling an address format writes (``$08:000000``,
``$C0:FFFF``, ``7FFFFF``), which every address and offset field has."""

HEX_NUMBER = QRegularExpression(r"\s*(\$|0[xX])?[0-9A-Fa-f_]*\s*")
"""What every field a hex number is typed into accepts while it is typed:
``$`` or ``0x`` before the digits, ``_`` between them, spaces around them."""
_ADDRESS = QRegularExpression(r"\s*(\$|0[xX])?[0-9A-Fa-f_]*(:[0-9A-Fa-f_]*)?\s*")
_OFFSET = QRegularExpression(r"\s*(-?\$?|\$-)?(0[xX])?[0-9A-Fa-f_]*\s*")


def fit_spin(spin: QAbstractSpinBox, digits: int) -> QAbstractSpinBox:
    """Fix a spin box's width to ``digits`` digits in its own base, or to its
    sign, special value text or smallest value where one of those is wider.

    Qt sizes a spin box by its range's ends, so a count that may reach a million
    but holds tens is as wide as the million. Returns ``spin``.
    """
    base = spin.displayIntegerBase() if isinstance(spin, QSpinBox) else 10
    low, high, value = spin.minimum(), spin.maximum(), spin.value()
    with QSignalBlocker(spin):
        spin.setRange(low, max(low, base**digits - 1))
        width = spin.sizeHint().width()
        spin.setRange(low, high)
        spin.setValue(value)
    spin.setFixedWidth(width)
    return spin


def number_spin(
    low: int,
    high: int,
    digits: int,
    *,
    value: int | None = None,
    off: bool = False,
    special: str | None = None,
) -> QSpinBox:
    """A spin box over ``low``–``high`` as wide as ``digits`` digits.

    A box whose lowest value means something other than a count says so there
    rather than in its label (``docs/ui.md``): ``off`` reads ``off``, and
    ``special`` reads whatever it is given — ``none``, ``fit``, ``box``.
    """
    spin = QSpinBox()
    spin.setRange(low, high)
    if value is not None:
        spin.setValue(value)
    if special is not None:
        spin.setSpecialValueText(special)
    elif off:
        spin.setSpecialValueText("off")
    return fit_spin(spin, digits)


def decimal_spin(low: float, high: float, digits: int, decimals: int) -> QDoubleSpinBox:
    """A spin box over ``low``–``high`` with ``decimals`` places, as wide as
    ``digits`` whole digits."""
    spin = QDoubleSpinBox()
    spin.setDecimals(decimals)
    spin.setRange(low, high)
    return fit_spin(spin, digits)


class HexSpinBox(QSpinBox):
    """A spin box that shows its value in upper-case hex, padded to its digits."""

    def __init__(self, low: int, high: int, digits: int, parent: QWidget | None = None):
        super().__init__(parent)
        self._digits = digits
        self.setDisplayIntegerBase(16)
        self.setRange(low, high)
        fit_spin(self, digits)

    def textFromValue(self, value: int) -> str:  # noqa: N802 - Qt override
        return f"{value:0{self._digits}X}"


class HexEdit(QLineEdit):
    """A field for one unsigned hex number, padded to ``digits``; blank is
    ``None``.

    With ``pad`` off, ``digits`` only sets the field's width and what is typed
    keeps its own — for a number whose digit count means something, as a fill's
    first key means the width of every key it lays down.
    """

    def __init__(self, digits: int, parent: QWidget | None = None, *, pad: bool = True):
        super().__init__(parent)
        self._digits = digits if pad else 0
        self.setValidator(QRegularExpressionValidator(HEX_NUMBER, self))
        fit_chars(self, digits)
        self.setMaximumWidth(self.minimumWidth())
        self.editingFinished.connect(self._respell)

    def value(self) -> int | None:
        try:
            return parse_hex(self.text(), None)
        except ValueError:
            return None

    def set_value(self, value: int | None) -> None:
        self.setText("" if value is None else f"{value:0{self._digits}X}")

    def _respell(self) -> None:
        value = self.value()
        if value is not None:
            self.set_value(value)


class AddressSpelling(QObject):
    """How every address field spells a file offset: flat hex, or a bank layout.

    The window keeps one, following its address format; a field given none
    spells flat hex.
    """

    changed = Signal(object)
    """The layout changed; carries the one before, so a field can read what it
    shows under the spelling it was written in."""

    def __init__(self, parent: QObject | None = None):
        super().__init__(parent)
        self._layout: AddressLayout | None = None

    @property
    def layout(self) -> AddressLayout | None:
        return self._layout

    def set_layout(self, layout: AddressLayout | None) -> None:
        if layout != self._layout:
            old, self._layout = self._layout, layout
            self.changed.emit(old)

    def format(self, offset: int) -> str:
        return format_address(offset, self._layout)

    def parse(self, text: str) -> int | None:
        return parse_address(text, self._layout)


class AddressEdit(QLineEdit):
    """A field for one file offset, spelled as its :class:`AddressSpelling`
    spells one; blank or unreadable is ``None``.

    What is typed is re-spelled once it is finished, and what is shown is
    re-spelled when the spelling changes.
    """

    def __init__(
        self, spelling: AddressSpelling | None = None, parent: QWidget | None = None
    ):
        super().__init__(parent)
        self.spelling = spelling if spelling is not None else AddressSpelling(self)
        self.setValidator(QRegularExpressionValidator(_ADDRESS, self))
        fit_chars(self, ADDRESS_CHARS)
        self.setMaximumWidth(self.minimumWidth())
        self.editingFinished.connect(self._respell)
        self.spelling.changed.connect(self._on_spelling)

    def value(self) -> int | None:
        return self.spelling.parse(self.text())

    def set_value(self, offset: int | None) -> None:
        self.setText("" if offset is None else self.spelling.format(offset))

    def _respell(self) -> None:
        offset = self.value()
        if offset is not None:
            self.set_value(offset)

    def _on_spelling(self, old: AddressLayout | None) -> None:
        offset = parse_address(self.text(), old)
        if offset is not None:
            self.set_value(offset)


def respell_addresses(
    text: str, spelling: AddressSpelling, old: AddressLayout | None
) -> str:
    """A comma-separated address list read under ``old`` and spelled by
    ``spelling``; an item neither reads is kept as it is."""
    items = []
    for item in text.split(","):
        if not item.strip():
            continue
        offset = parse_address(item, old)
        items.append(item.strip() if offset is None else spelling.format(offset))
    return ", ".join(items)


class OffsetEdit(QLineEdit):
    """A field for a signed hex offset: ``1F0``, ``-10``, ``$`` optional."""

    def __init__(self, value: int = 0, parent: QWidget | None = None):
        super().__init__(parent)
        self.setValidator(QRegularExpressionValidator(_OFFSET, self))
        fit_chars(self, ADDRESS_CHARS)
        self.setMaximumWidth(self.minimumWidth())
        self.set_value(value)
        self.editingFinished.connect(self._respell)

    def value(self, blank: int | None = 0) -> int | None:
        """The offset; ``blank`` when there is no text, ``None`` when it does
        not read."""
        if not self.text().strip():
            return blank
        try:
            return parse_hex_offset(self.text())
        except ValueError:
            return None

    def set_value(self, value: int) -> None:
        self.setText(format_hex_offset(value))

    def _respell(self) -> None:
        if self.text().strip() and (value := self.value()) is not None:
            self.set_value(value)


__all__ = [
    "ADDRESS_CHARS",
    "HEX_NUMBER",
    "AddressEdit",
    "AddressSpelling",
    "HexEdit",
    "HexSpinBox",
    "OffsetEdit",
    "decimal_spin",
    "fit_spin",
    "number_spin",
    "respell_addresses",
]
