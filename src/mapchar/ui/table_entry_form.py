"""The Table Editor's entry form: one entry, every part of it a control.

Only text is typed — the key, the kind, the weight, a code's operands and a
switch's parameters are pickers and sized fields, so what an entry can be is
on show rather than remembered. The form reads back as an
:class:`~mapchar.core.table.Entry` and writes out the line the native grammar
spells it with; that line is a field too, and a line typed or pasted into it
fills the form, so the grammar stays open to whoever knows it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace

from PySide6.QtCore import QRegularExpression, Qt, Signal
from PySide6.QtGui import QRegularExpressionValidator
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.bits import bits_to_hex, hex_to_bits
from mapchar.core.table import (
    BITS,
    COUNT_SPECS,
    LABEL_PATTERN,
    RAW,
    RETURN,
    Entry,
    OperandSpec,
    Stop,
    SwitchParam,
    TokenKind,
)
from mapchar.project.formats.table_native import format_entry, parse_entry
from mapchar.ui.number_fields import number_spin
from mapchar.ui.widgets import (
    ElidedLabel,
    ModeToggle,
    WrapBar,
    fill_pick,
    fit_chars,
    hint_field,
    mono_font,
    select_data,
)

KINDS = (
    (TokenKind.TEXT, "Text", "The bits decode to the text"),
    (TokenKind.END, "End", "As text, and the string ends after it"),
    (
        TokenKind.CODE,
        "Code",
        "A control code with operands read from the bytes after it",
    ),
    (
        TokenKind.SWITCH,
        "Switch",
        "Prints its text, then reads the bytes after it in other tables",
    ),
    (
        TokenKind.RETURN,
        "Return",
        "Leaves the table that switched here; at the top level, ends the string",
    ),
)
"""Every kind an entry can be: its datum, its name and what it does."""

KIND_NAMES = {kind: name for kind, name, _ in KINDS}

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

_HEX = QRegularExpression(r"[0-9A-Fa-f]*")
_BIN = QRegularExpression(r"[01]*")


def describe(entry: Entry) -> str:
    """What an entry does beyond its text, in words: the grid's Details."""
    if entry.kind is TokenKind.CODE:
        return "reads " + ", ".join(o.spec() for o in entry.operands)
    if entry.kind is TokenKind.SWITCH:
        return ", then ".join(describe_param(p) for p in entry.params)
    if entry.kind is TokenKind.END:
        return "ends the string"
    if entry.kind is TokenKind.RETURN:
        return "leaves the table"
    return ""


def describe_param(param: SwitchParam) -> str:
    if param.table_id == RETURN:
        return "return"
    stop = param.stop
    if stop.count is not None:
        how = f"×{stop.count}"
    elif stop.operand is not None:
        how = f"×{stop.operand.spec()} from the data"
    elif stop.fallback is not None:
        how = "until " + stop.spec()
    else:
        how = "until the string ends"
    return f"@{param.table_id} {how}" + (" +" if param.shared else "")


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
        self.operand = QComboBox()
        for spec in COUNT_SPECS:
            self.operand.addItem(spec, spec)
        self.bytes = hint_field(QLineEdit(), "FF", "The bytes, in hex")
        self.bytes.setValidator(QRegularExpressionValidator(_HEX, self.bytes))
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
        self.shared = QCheckBox("+")
        self.shared.setToolTip(
            "Its matches also count towards the table that switched here\n"
            "(a Pascal string whose count covers the switched-to text)"
        )
        self.drop = _drop_button("Remove this parameter")
        row.addWidget(self.table)
        row.addWidget(self.stop)
        for widget in self.values.values():
            row.addWidget(widget)
        row.addWidget(self.shared)
        row.addStretch(1)
        row.addWidget(self.drop)
        self.table.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.stop.currentIndexChanged.connect(self._on_stop)
        self.count.valueChanged.connect(lambda _: self.changed.emit())
        self.operand.currentIndexChanged.connect(lambda _: self.changed.emit())
        self.bytes.textChanged.connect(lambda _: self.changed.emit())
        self.bits.textChanged.connect(lambda _: self.changed.emit())
        self.shared.toggled.connect(lambda _: self.changed.emit())
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
        return SwitchParam(self.table.currentData(), stop, self.shared.isChecked())

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
        self.add.setFlat(True)
        self.add.setToolTip(f"{add_label} after the last")
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


class TableEntryForm(QWidget):
    """One entry as controls, and as the line that spells it."""

    changed = Signal()
    """Something in the form changed; the line and any problem are current."""
    submitted = Signal()
    """Enter in a field: put the entry in the table."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._tables: list[str] = []
        self._syncing = False
        self._comment_lines = ""
        box = QVBoxLayout(self)
        box.setContentsMargins(0, 0, 0, 0)
        box.setSpacing(3)

        # -- key, kind, weight ------------------------------------------------
        head = WrapBar()
        self.key = QLineEdit()
        fit_chars(self.key, 10)
        self.key_mode = ModeToggle((("Hex", "hex"), ("Bits", "bits")))
        self.key_mode.button("hex").setToolTip("The key as hex digits, four bits each")
        self.key_mode.button("bits").setToolTip(
            "The key as bits, for a width that is not whole digits"
        )
        self.key_width = QLabel("")
        head.add_group(
            "Key",
            self.key,
            self.key_mode,
            self.key_width,
            tip="The bits the entry matches; leading zeros count",
        )
        self.kind = QComboBox()
        for kind, name, tip in KINDS:
            self.kind.addItem(name, kind)
            self.kind.setItemData(
                self.kind.count() - 1, tip, Qt.ItemDataRole.ToolTipRole
            )
        head.add_group("Kind", self.kind, tip="What the entry does when its bits match")
        self.weight = number_spin(-99, 9999, 4, value=1)
        head.add_group(
            "Weight",
            self.weight,
            tip="How much a match counts towards a switch's count; usually 1",
        )
        box.addWidget(head)

        # -- text or label ----------------------------------------------------
        text_row = QHBoxLayout()
        self.text_label = QLabel("Text")
        self.text = hint_field(QLineEdit(), "", "")
        text_row.addWidget(self.text_label)
        text_row.addWidget(self.text, 1)
        box.addLayout(text_row)

        # -- the parts one kind has -------------------------------------------
        self.operands = RowList(OperandRow, "Add Operand")
        self.operands_box = _captioned(
            "Operands", self.operands, "What the code reads after its bits, in order"
        )
        box.addWidget(self.operands_box)
        self.params = RowList(lambda: ParamRow(self._tables), "Add Parameter")
        self.then_return = QCheckBox("then return")
        self.then_return.setToolTip(
            "After the parameters, leave the table this entry is in; "
            "at the top level, end the string"
        )
        params_inner = QWidget()
        pv = QVBoxLayout(params_inner)
        pv.setContentsMargins(0, 0, 0, 0)
        pv.setSpacing(2)
        pv.addWidget(self.params)
        pv.addWidget(self.then_return)
        self.params_box = _captioned(
            "Parameters",
            params_inner,
            "Where the bytes after the switch are read, one frame each, in order",
        )
        box.addWidget(self.params_box)

        # -- comment and the line ---------------------------------------------
        comment_row = QHBoxLayout()
        self.comment = QPlainTextEdit()
        self.comment.setPlaceholderText("Comment")
        self.comment.setToolTip("Kept above the entry in the table file")
        self.comment.setFixedHeight(self.comment.fontMetrics().height() * 2 + 10)
        self.comment.setTabChangesFocus(True)
        comment_row.addWidget(QLabel("Comment"))
        comment_row.addWidget(self.comment, 1)
        box.addLayout(comment_row)
        line_row = QHBoxLayout()
        self.line = hint_field(
            QLineEdit(),
            "41=A   /FF=[end]   $F0=[color],u8   !F1=[item] @items:1",
            "The entry as its line in the table file. "
            "Typing or pasting a line here fills the form",
        )
        self.line.setFont(mono_font())
        line_row.addWidget(QLabel("Line"))
        line_row.addWidget(self.line, 1)
        box.addLayout(line_row)
        self.problem = ElidedLabel("")
        box.addWidget(self.problem)

        self.key.setValidator(QRegularExpressionValidator(_HEX, self.key))
        self.key.textChanged.connect(self._on_form_changed)
        self.key_mode.chosen.connect(self._on_key_mode)
        self.kind.currentIndexChanged.connect(self._on_kind)
        self.weight.valueChanged.connect(self._on_form_changed)
        self.text.textChanged.connect(self._on_form_changed)
        self.operands.changed.connect(self._on_form_changed)
        self.params.changed.connect(self._on_form_changed)
        self.then_return.toggled.connect(self._on_form_changed)
        self.comment.textChanged.connect(self._on_form_changed)
        self.line.textChanged.connect(self._on_line_edited)
        for field in (self.key, self.text, self.line):
            field.returnPressed.connect(self.submitted)
        self._on_kind()

    # -- tables ---------------------------------------------------------------

    def set_tables(self, tables: list[str]) -> None:
        """The loaded tables a parameter can name."""
        self._tables = list(tables)
        for row in self.params.rows():
            row.set_tables(self._tables)

    # -- the key --------------------------------------------------------------

    def key_bits(self) -> str:
        text = self.key.text().strip()
        if self.key_mode.value() == "bits":
            return text
        return hex_to_bits(text)

    def set_key(self, bits: str) -> None:
        mode = "hex" if len(bits) % 4 == 0 else "bits"
        self.key_mode.set_value(mode)
        self._apply_key_mode(mode)
        self.key.setText(bits_to_hex(bits) if mode == "hex" else bits)

    def _apply_key_mode(self, mode: str) -> None:
        pattern = _HEX if mode == "hex" else _BIN
        self.key.setValidator(QRegularExpressionValidator(pattern, self.key))

    def _on_key_mode(self, mode: str) -> None:
        typed = self.key.text().strip()
        try:
            spelled = bits_to_hex(typed) if mode == "hex" else hex_to_bits(typed)
        except ValueError:
            # Not whole digits: the key stays as bits, which spell any width.
            self.key_mode.set_value("bits")
            self.problem.setText(f"{len(typed)} bits are not whole hex digits.")
            return
        self._apply_key_mode(mode)
        self.key.setText(spelled)

    # -- the kind -------------------------------------------------------------

    def _on_kind(self) -> None:
        kind = self.kind.currentData()
        self.text_label.setText("Label" if kind is TokenKind.CODE else "Text")
        self.text_label.setVisible(kind is not TokenKind.RETURN)
        self.text.setVisible(kind is not TokenKind.RETURN)
        hints = {
            TokenKind.TEXT: (
                "A, \\n, or [label]",
                "What the bits decode to; \\n is a line break, [label] a code",
            ),
            TokenKind.END: ("[end]", "What the end token shows; usually [end]"),
            TokenKind.CODE: (
                "color",
                "The code's name, shown as [name] with its operands",
            ),
            TokenKind.SWITCH: (
                "[item], text, or nothing",
                "Printed before the switch runs; nothing makes a silent switch "
                "the encoder inserts itself",
            ),
            TokenKind.RETURN: ("", ""),
        }
        placeholder, tip = hints[kind]
        self.text.setPlaceholderText(placeholder)
        self.text.setToolTip(tip)
        self.operands_box.setVisible(kind is TokenKind.CODE)
        self.params_box.setVisible(kind is TokenKind.SWITCH)
        if kind is TokenKind.CODE and not self.operands.rows():
            self.operands.add_row()
        if kind is TokenKind.SWITCH and not self.params.rows():
            self.params.add_row()
        self._on_form_changed()

    # -- reading and writing the entry ----------------------------------------

    def entry(self) -> Entry:
        """The entry the form spells; raises ValueError with what is missing."""
        bits = self.key_bits()
        if not bits:
            raise ValueError("The key is blank.")
        kind = self.kind.currentData()
        text = self.text.text()
        weight = self.weight.value()
        comment = self.comment.toPlainText().strip()
        if kind is TokenKind.CODE:
            if not LABEL_PATTERN.fullmatch(text):
                raise ValueError(
                    "A code needs a label: no spaces or brackets, "
                    "not starting with $ or %."
                )
            operands = tuple(row.spec() for row in self.operands.rows())
            if not operands:
                raise ValueError("A code needs at least one operand.")
            entry = Entry(bits, kind, text, weight, operands=operands, comment=comment)
        elif kind is TokenKind.SWITCH:
            params = tuple(row.param() for row in self.params.rows())
            if self.then_return.isChecked():
                params += (SwitchParam(RETURN),)
            if not params:
                raise ValueError("A switch needs at least one parameter.")
            entry = Entry(bits, kind, text, weight, params=params, comment=comment)
        elif kind is TokenKind.RETURN:
            entry = Entry(bits, kind, "", weight, comment=comment)
        else:
            entry = Entry(bits, kind, text, weight, comment=comment)
        # The grammar is the judge of the text: an unclosed bracket, a stray
        # one, a label the line cannot carry.
        parse_entry(format_entry(entry))
        return entry

    def set_entry(self, entry: Entry | None) -> None:
        """Show ``entry``, or a blank text entry for ``None``."""
        self._syncing = True
        try:
            if entry is None:
                self.key.clear()
                self.key_mode.set_value("hex")
                self._apply_key_mode("hex")
                select_data(self.kind, TokenKind.TEXT)
                self.weight.setValue(1)
                self.text.clear()
                self.comment.clear()
                self.operands.clear()
                self.params.clear()
                self.then_return.setChecked(False)
            else:
                self.set_key(entry.bits)
                select_data(self.kind, entry.kind)
                self.weight.setValue(entry.weight)
                self.text.setText(entry.text)
                self.comment.setPlainText(entry.comment)
                self.operands.clear()
                for spec in entry.operands:
                    self.operands.add_row().set_spec(spec)
                self.params.clear()
                params = list(entry.params)
                trailing = bool(params) and params[-1].table_id == RETURN
                if trailing:
                    params.pop()
                for param in params:
                    self.params.add_row().set_param(param)
                self.then_return.setChecked(trailing)
        finally:
            self._syncing = False
        self._on_kind()

    def _on_form_changed(self, *_) -> None:
        if self._syncing:
            return
        self._syncing = True
        try:
            self.key_width.setText(self._width_text())
            try:
                entry = self.entry()
            except ValueError as exc:
                # A blank form has nothing to say yet; Add says so when asked.
                self.problem.setText(str(exc) if self.key_bits() else "")
                if not self.key_bits():
                    self.line.clear()
            else:
                self.problem.setText("")
                line = format_entry(entry)
                # Left alone when it already says so: a set moves the cursor.
                if self.line.text() != line:
                    self.line.setText(line)
        finally:
            self._syncing = False
        self.changed.emit()

    def _width_text(self) -> str:
        n = len(self.key_bits())
        return f"{n} bit{'s' if n != 1 else ''}" if n else ""

    def _on_line_edited(self, text: str) -> None:
        """A line typed into the Line field fills the form, keeping the
        comment, which the line does not spell."""
        if self._syncing:
            return
        try:
            entry = parse_entry(text.strip())
        except ValueError as exc:
            self.problem.setText(str(exc))
            self.changed.emit()
            return
        comment = self.comment.toPlainText()
        self._syncing = True
        try:
            self.set_entry(replace(entry, comment=comment))
        finally:
            self._syncing = False
        self.key_width.setText(self._width_text())
        self.problem.setText("")
        self.changed.emit()

    def focus_key(self) -> None:
        self.key.setFocus()
        self.key.selectAll()


def _captioned(title: str, inner: QWidget, tip: str) -> QWidget:
    box = QWidget()
    row = QHBoxLayout(box)
    row.setContentsMargins(0, 0, 0, 0)
    caption = QLabel(title)
    caption.setToolTip(tip)
    caption.setAlignment(Qt.AlignmentFlag.AlignTop)
    row.addWidget(caption)
    row.addWidget(inner, 1)
    return box


__all__ = [
    "KINDS",
    "KIND_NAMES",
    "OPERAND_SPECS",
    "STOPS",
    "OperandRow",
    "ParamRow",
    "RowList",
    "TableEntryForm",
    "describe",
    "describe_param",
]
