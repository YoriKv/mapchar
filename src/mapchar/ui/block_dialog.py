"""New Block…: a block's name, table and reading settled before it is made,
with a count of the strings that reading would cut."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLineEdit,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.block import BlockConfig, source_start
from mapchar.ui.number_fields import AddressSpelling
from mapchar.ui.reading_bar import ReadingBar
from mapchar.ui.widgets import (
    CompactComboBox,
    ElidedLabel,
    ModeToggle,
    fill_pick,
    hint_field,
    ok_cancel,
    select_data,
)

COUNT_LIMIT = 100
"""How many strings the dialog counts up to. Past that it says "more than",
since a count is for telling a reading that cuts the region as meant from one
that does not, and a reading nobody has settled is changed at every keystroke
over a region that may run to the end of the ROM."""

Counter = Callable[[BlockConfig], int | str]
"""Counts the strings a reading cuts, reading no more than
:data:`COUNT_LIMIT` + 1 of them — or says, as text, why it cannot."""


def default_name(config: BlockConfig) -> str:
    """What a block is called when nobody names it: after where it starts."""
    start = source_start(config.source)
    return "Block" if start is None else f"Block {start:X}"


def count_text(count: int | str) -> str:
    """What the count line says: how many strings, or why there is no count."""
    if isinstance(count, str):
        return count
    if count > COUNT_LIMIT:
        return f"More than {COUNT_LIMIT} strings"
    if count == 0:
        return "No strings"
    return f"{count} string" if count == 1 else f"{count} strings"


class NewBlockDialog(QDialog):
    """File ▸ New Block…: what the block is called, the table it reads
    through, and how its bytes are cut — the same Reading bar the window shows
    a block's settings in, over the region the caller proposes — with a count
    of the strings the reading makes, kept current as the settings change.

    The dialog only gathers the settings: ``counter`` is the caller's, since
    the bytes and the tables are.
    """

    OPEN_WIDTH = 960
    """Pixels across the dialog opens at; the bar wraps to it."""

    RECOUNT_DELAY = 150
    """Milliseconds a change waits before the strings are counted again, so a
    number typed digit by digit is read once."""

    def __init__(
        self,
        config: BlockConfig,
        table_items: list[tuple[str, str]],
        mappings,
        counter: Counter,
        *,
        spelling: AddressSpelling | None = None,
        suggested_mapping: str | None = None,
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle("New Block")
        self._base = config
        self._counter = counter
        self._sized = False
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = hint_field(
            QLineEdit(),
            default_name(config),
            "The block's name; left blank, it is named after its start",
        )
        self.table_pick = CompactComboBox(220)
        self.table_pick.setToolTip("Table or encoding the text is read through")
        fill_pick(self.table_pick, table_items)
        if not select_data(self.table_pick, config.table_id):
            # A table the reading names but nobody loaded is still what it
            # names: said so, rather than shown as some other table.
            label = f"@{config.table_id}  (not loaded)" if config.table_id else ""
            self.table_pick.addItem(label or "(no table)", config.table_id)
            self.table_pick.setCurrentIndex(self.table_pick.count() - 1)
        self.mode_toggle = ModeToggle((("Strings", False), ("Pointers", True)))
        self.mode_toggle.button(False).setToolTip("Read the bytes as text")
        self.mode_toggle.button(True).setToolTip(
            "Read the bytes as pointers to the strings"
        )
        self.mode_toggle.set_value(config.has_pointers)
        # The toggle sits at the row's left, as wide as its two buttons, rather
        # than spread over the width the name field takes.
        mode_row = QHBoxLayout()
        mode_row.addWidget(self.mode_toggle)
        mode_row.addStretch(1)
        form.addRow("Name", self.name)
        form.addRow("Table", self.table_pick)
        form.addRow("Read as", mode_row)
        layout.addLayout(form)

        self.reading_bar = ReadingBar(spelling)
        self.reading_bar.set_mappings(mappings)
        self.reading_bar.load(config, block=True)
        if suggested_mapping and not config.has_pointers:
            self.reading_bar.suggest_mapping(suggested_mapping)
        layout.addWidget(self.reading_bar)

        self.count = ElidedLabel()
        self.count.setToolTip("How many strings the reading cuts the region into")
        layout.addWidget(self.count)
        layout.addWidget(ok_cancel(self))

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(self.RECOUNT_DELAY)
        self._timer.timeout.connect(self.recount)
        self.reading_bar.edited.connect(lambda _name: self._schedule())
        self.table_pick.currentIndexChanged.connect(lambda _i: self._schedule())
        self.mode_toggle.chosen.connect(self._on_mode)
        # The bar asks for the width its sections take in one row, which is
        # more than any screen shows the window at; the dialog opens at a
        # width a window commonly has, and the bar wraps to it as it does
        # there. How tall that makes the bar is known once it is laid out at
        # that width, so the height is settled on the first show.
        self.resize(self.OPEN_WIDTH, self.minimumSizeHint().height())
        self.name.setFocus()
        self.recount()

    def showEvent(self, event) -> None:  # noqa: N802 - Qt override
        super().showEvent(event)
        if not self._sized:
            self._sized = True
            layout = self.layout()
            # Laid out again first: the minimum height the layout set before
            # the show came from the bar at a narrower width, wrapped taller,
            # and would keep the dialog from settling at its wrapped height.
            layout.activate()
            self.resize(self.OPEN_WIDTH, layout.minimumHeightForWidth(self.OPEN_WIDTH))

    def _on_mode(self, pointers: bool) -> None:
        self.reading_bar.set_pointers(pointers)
        self._schedule()

    def _schedule(self) -> None:
        self._timer.start()

    def config(self) -> BlockConfig:
        """The reading the dialog shows."""
        return self.reading_bar.config(self._base, self.table_pick.currentData() or "")

    def block_name(self) -> str:
        """The name typed, or the default when none was."""
        return self.name.text().strip() or self.name.placeholderText()

    def recount(self) -> None:
        """Count the strings the reading cuts now, and name the block after
        where it now starts."""
        self._timer.stop()
        cfg = self.config()
        self.name.setPlaceholderText(default_name(cfg))
        self.count.setText(count_text(self._counter(cfg)))

    def accept(self) -> None:
        # Whatever was typed last is what the block is made with, however
        # little time has passed since.
        self._timer.stop()
        super().accept()
