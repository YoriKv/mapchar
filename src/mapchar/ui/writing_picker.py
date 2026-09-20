"""A block's write settings: how a write lays its strings out.

Set once per block and then left alone, so the Reading bar shows them as one
line — the mode, where the room ends, the fill — and edits them in a popup
under it, as it does the skip ranges. The picker owns the four fields and what
each says of itself, including what skip ranges and a record header do to the
choice of mode; the bar loads it from a configuration (:meth:`WritingPicker.load`),
reads it back (:meth:`WritingPicker.values`) and forwards its :attr:`changed`
as an edit of its own.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import QComboBox, QFormLayout, QLineEdit, QWidget

from mapchar.core.block import WriteMode, default_write_mode
from mapchar.core.fill import format_fill, parse_fill
from mapchar.ui.number_fields import HEX_NUMBER, AddressEdit
from mapchar.ui.popup_picker import PopupFrame, PopupPicker
from mapchar.ui.widgets import fit_chars, hint_field

if TYPE_CHECKING:
    from mapchar.core.block import BlockConfig
    from mapchar.ui.number_fields import AddressSpelling

_MODE_TIP = "How a write lays the strings out"
_MODE_FORCED = (
    "How a write lays the strings out; skip ranges and a record header break "
    "the text up, so the block is written slotted"
)
_PACKED_TIP = "Strings laid end to end, every pointer rewritten"
_PACKED_FORCED = "Unavailable: a write cannot lay end to end what it has to step around"
"""What the Write picker says of itself, with and without something forcing
slotted."""


class FillEdit(QLineEdit):
    """A fill pattern in hex: a byte for every two digits typed, so ``FFFF`` is
    two bytes and ``FF`` one; blank is ``None``."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setValidator(QRegularExpressionValidator(HEX_NUMBER, self))
        fit_chars(self, 4)

    def value(self) -> bytes | None:
        digits = self.text().strip().removeprefix("$").removeprefix("0x")
        digits = digits.removeprefix("0X").replace("_", "")
        try:
            return parse_fill("$" + digits) if digits else None
        except ValueError:
            return None

    def set_value(self, fill: bytes | None) -> None:
        self.setText("" if fill is None else format_fill(fill)[1:])


class WritingPopup(PopupFrame):
    """The write settings' fields, in a popup under their picker. They apply as
    they are edited."""

    def __init__(self, parent: QWidget, rows: tuple[tuple[str, QWidget], ...]):
        super().__init__(parent)
        form = QFormLayout(self)
        form.setContentsMargins(8, 8, 8, 8)
        for label, field in rows:
            form.addRow(label, field)


class WritingPicker(PopupPicker):
    """The write settings as one line, opening on the popup that edits them in
    place of a dropdown."""

    changed = Signal(str)
    """The user changed the field of this name; the bar passes it on as its own
    edit, so a run on one field still merges into a single undo step."""

    def __init__(self, spelling: AddressSpelling, parent: QWidget | None = None):
        super().__init__(250, "", parent)
        self.setToolTip("How a write lays the strings out, and up to where")
        self._spelling = spelling
        self._block = False
        """Whether there is a block to write; a file's reading has no line."""

        self.bound = AddressEdit(spelling)
        hint_field(
            self.bound,
            "stop",
            "Writes stop before this address; blank uses the default shown",
        )
        self.write_mode = QComboBox()
        for label, mode, tip in (
            ("Automatic", None, "Packed with pointers, slotted without"),
            ("Packed", WriteMode.PACKED, _PACKED_TIP),
            ("Slotted", WriteMode.SLOTTED, "Every string stays in its own place"),
        ):
            self.write_mode.addItem(label, mode)
            self.write_mode.setItemData(
                self.write_mode.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        self.write_mode.setToolTip(_MODE_TIP)
        self.fill = FillEdit()
        self.fill.setToolTip(
            "The bytes that pad unused room, in hex: FFFF pads with a word"
        )
        self.spare_room = QComboBox()
        self.spare_room.addItem("Fill", "fill")
        self.spare_room.addItem("Keep", "keep")
        for at, tip in (
            (0, "Pad the freed tail with the fill byte"),
            (1, "Leave the freed tail as it was"),
        ):
            self.spare_room.setItemData(at, tip, Qt.ItemDataRole.ToolTipRole)
        self.spare_room.setToolTip(
            "After a shorter re-compression: fill the slot's tail, or keep it"
        )
        self.popup = WritingPopup(
            self,
            (
                ("Bound", self.bound),
                ("Write", self.write_mode),
                ("Fill", self.fill),
                ("Spare room", self.spare_room),
            ),
        )
        for name, combo in (
            ("write_mode", self.write_mode),
            ("spare_room", self.spare_room),
        ):
            combo.currentIndexChanged.connect(lambda _=0, n=name: self.changed.emit(n))
        for name, field in (("bound", self.bound), ("fill", self.fill)):
            field.editingFinished.connect(lambda n=name: self.changed.emit(n))

    # -- loading ---------------------------------------------------------------

    def load(
        self,
        config: BlockConfig,
        *,
        block: bool,
        spare_room: str = "fill",
        compressed: bool = False,
    ) -> None:
        """Show ``config``'s write settings; without ``block`` there is nothing
        to write, and the line stays empty."""
        self._block = block
        self.bound.set_value(config.bound)
        self.write_mode.setCurrentIndex(
            max(self.write_mode.findData(config.write_mode), 0)
        )
        self.fill.set_value(config.fill)
        self.spare_room.setCurrentIndex(max(self.spare_room.findData(spare_room), 0))
        # Only a re-compression leaves room over, so only then is there a rule.
        self.spare_room.setEnabled(compressed)

    def set_bound_default(self, default: int | str | None) -> None:
        """Say in the Bound field's placeholder where a blank bound stops: at an
        address, or where the words given say."""
        if default is None:
            hint = "stop"
        elif isinstance(default, str):
            hint = default
        else:
            hint = self._spelling.format(default)
        hint_field(self.bound, hint, self.bound.toolTip())
        self._say()

    def show_forced(self, pointers: bool, forced: bool) -> None:
        """Say which mode Automatic means, and, where skip ranges or a record
        header force slotted, grey Packed and say what a block holding it is
        written as instead.

        The block keeps the mode it holds, so taking the skips or the header
        away writes it packed again.
        """
        auto = default_write_mode(pointers, forced)
        self.write_mode.setItemText(0, f"Automatic ({auto.value})")
        at = self.write_mode.findData(WriteMode.PACKED)
        self.write_mode.setItemText(at, "Packed (slotted)" if forced else "Packed")
        self.write_mode.model().item(at).setEnabled(not forced)
        self.write_mode.setItemData(
            at,
            _PACKED_FORCED if forced else _PACKED_TIP,
            Qt.ItemDataRole.ToolTipRole,
        )
        self.write_mode.setToolTip(_MODE_FORCED if forced else _MODE_TIP)
        self._say()

    def _say(self) -> None:
        """The write settings on one line: the mode, where the room ends, the
        fill."""
        if not self._block:
            self.set_summary("")
            return
        bound = self.bound.text() or self.bound.placeholderText()
        self.set_summary(
            f"{self.write_mode.currentText()} · to {bound} · {self.fill.text()}"
        )

    # -- reading back ----------------------------------------------------------

    def values(self) -> tuple[int | None, WriteMode | None, bytes | None]:
        """The bound, the mode and the fill; a fill nobody can read is ``None``,
        which leaves the block the one it holds."""
        return self.bound.value(), self.write_mode.currentData(), self.fill.value()

    def spare_room_rule(self) -> str:
        return self.spare_room.currentData()


__all__ = ["FillEdit", "WritingPicker", "WritingPopup"]
