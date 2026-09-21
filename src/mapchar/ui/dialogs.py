"""Dialogs: the file container, imports, reports, pointers."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from mapchar.core.numbers import format_hex_offset
from mapchar.project.formats.summary import ImportSummary
from mapchar.ui.number_fields import OffsetEdit
from mapchar.ui.widgets import ResultsTable, ok_cancel, show_elided_tooltips


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
        buttons = ok_cancel(self)
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


class ImportDialog(QDialog):
    """What an import will do, shown before it does it.

    An import reaches across blocks, lands as one undo step and is driven as
    often by a drop — whose kind is a guess from a suffix — as by a menu, so it
    says which blocks it touches, how many strings of each and what it cannot
    place, and waits. **Force** re-plans rather than filtering the plan on
    screen: what it takes back are records the first pass never matched, so
    only the importer can say what they would do.
    """

    def __init__(
        self,
        name: str,
        summary_for: Callable[[bool], ImportSummary],
        parent: QWidget | None = None,
    ):
        super().__init__(parent)
        self.setWindowTitle(f"Import {name}")
        self._summary_for = summary_for
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        layout.addWidget(self.heading)
        self.blocks = ResultsTable(("Block", "Strings", "Action"))
        # A report, not a list to pick from: nothing here is chosen, so the row
        # numbers and the selection would both be furniture.
        self.blocks.verticalHeader().setVisible(False)
        self.blocks.setSelectionMode(ResultsTable.SelectionMode.NoSelection)
        self.blocks.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout.addWidget(self.blocks)
        self.skipped_label = QLabel("Not imported:")
        layout.addWidget(self.skipped_label)
        self.skipped = QPlainTextEdit()
        self.skipped.setReadOnly(True)
        self.skipped.setMaximumHeight(110)
        layout.addWidget(self.skipped)
        self.force = QCheckBox("Import the skipped records anyway")
        self.force.setToolTip(
            "Import records whose original no longer matches the project's"
        )
        self.force.toggled.connect(self._refill)
        layout.addWidget(self.force)
        layout.addWidget(QLabel("The whole import lands as one undo step."))
        self.buttons = ok_cancel(self)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Import")
        layout.addWidget(self.buttons)
        self.resize(520, 420)
        self._refill()

    def _refill(self) -> None:
        summary = self._summary_for(self.force.isChecked())
        self.heading.setText(
            f"{summary.kind}: {summary.strings} string(s) "
            f"in {len(summary.blocks)} block(s)."
        )
        self.blocks.fill((b.name, str(b.strings), b.action) for b in summary.blocks)
        self.skipped.setPlainText("\n".join(summary.skipped))
        # The box and its heading go together when there is nothing to put in
        # them, rather than leaving an empty frame to be read as a failure.
        for widget in (self.skipped_label, self.skipped):
            widget.setVisible(bool(summary.skipped))
        # Offered only where it would take something back, so its absence says
        # the skipped lines are not a matter of the project having moved on —
        # and it stays on screen once ticked, since what it took back is
        # exactly what is no longer listed above it.
        self.force.setVisible(
            self.force.isChecked() or (summary.forceable and bool(summary.skipped))
        )
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(
            not summary.nothing_to_do
        )

    def forced(self) -> bool:
        return self.force.isChecked()


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
        self.offset_from = OffsetEdit(0)
        self.offset_to = OffsetEdit(0)
        self.offset_step = OffsetEdit(1)
        self.offset_from.setToolTip("First offset tried, in hex; - to subtract")
        self.offset_to.setToolTip("Last offset tried, in hex; - to subtract")
        self.offset_step.setToolTip("Gap between the offsets tried, in hex")
        form.addRow("Offset from", self.offset_from)
        form.addRow("Offset to", self.offset_to)
        form.addRow("Offset step", self.offset_step)
        buttons = ok_cancel(self)
        form.addRow(buttons)

    def selected_only(self) -> bool:
        return bool(self.scope.currentData())

    def _numbers(self) -> tuple[int, int, int] | None:
        """The three offset fields, or ``None`` if one of them does not read."""
        numbers = (
            self.offset_from.value(0),
            self.offset_to.value(0),
            self.offset_step.value(1),
        )
        return None if None in numbers else numbers

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
                "The offset range is not made of numbers. Write hex values, "
                "with a leading - to subtract.",
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
            [
                "Mapping",
                "Size",
                "Endian",
                "Offset",
                "Bank",
                "Strings",
                "Stride",
                "Addresses",
            ]
        )
        self.table.fill(
            [
                c.mapping_id,
                str(c.size),
                c.endian,
                format_hex_offset(c.offset),
                # Bank 0 is what every unbanked mapping reads too: left blank.
                f"{bank:02X}" if bank else "",
                str(c.explained),
                str(c.stride),
                f"{run[0]:X}–{run[-1]:X}" if run else "",
            ]
            # Each asked once: a candidate over a file of fill has a million
            # addresses behind its run and its bank.
            for c, run, bank in ((c, c.table_run(), c.bank()) for c in candidates)
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
