"""File ▸ Project Statistics…: how much text the project holds, how much of it
is translated, and where — the totals by status, then one row per block."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHeaderView,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from mapchar import APP_NAME
from mapchar.core.block import Status
from mapchar.project.statistics import BlockStatistics, Tally, summed
from mapchar.ui.widgets import close_box

COUNTS = ("Strings", "Words", "Lines")

STATUS_ROWS: tuple[tuple[str, tuple[Status, ...]], ...] = (
    ("Edited", (Status.EDITED,)),
    ("Review", (Status.REVIEW,)),
    ("Done", (Status.DONE,)),
    ("Translated", (Status.EDITED, Status.REVIEW, Status.DONE)),
    ("Untouched", (Status.UNTOUCHED,)),
    ("Total", tuple(Status)),
)
"""The rows of the totals table; the two sums are set in bold."""

BLOCK_HEADERS = ("File", "Block", *COUNTS, "Translated")


def _values(tally: Tally) -> tuple[int, int, int]:
    return tally.strings, tally.words, tally.lines


def _percent(part: int, whole: int) -> str:
    return f"{100 * part // whole}%" if whole else "–"


class _Cell(QTableWidgetItem):
    """A read-only cell that sorts by a key of its own, not by its text."""

    def __init__(self, text: str, key: object = None, right: bool = False) -> None:
        super().__init__(text)
        self.key = text.casefold() if key is None else key
        self.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
        if right:
            self.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )

    def __lt__(self, other: QTableWidgetItem) -> bool:
        if isinstance(other, _Cell) and type(self.key) is type(other.key):
            return self.key < other.key
        return super().__lt__(other)


def _table(rows: int, headers: tuple[str, ...]) -> QTableWidget:
    table = QTableWidget(rows, len(headers))
    table.setHorizontalHeaderLabels(list(headers))
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setAlternatingRowColors(True)
    table.verticalHeader().setDefaultSectionSize(table.fontMetrics().height() + 8)
    return table


def totals_table(total: BlockStatistics) -> QTableWidget:
    """The project's strings, words and lines in each status, each with its
    share of the whole."""
    table = _table(len(STATUS_ROWS), COUNTS)
    table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setVerticalHeaderLabels([label for label, _ in STATUS_ROWS])
    whole = _values(total.total)
    bold = QFont()
    bold.setBold(True)
    for row, (label, statuses) in enumerate(STATUS_ROWS):
        for column, n in enumerate(_values(total.of(*statuses))):
            text = f"{n:,}"
            if label != "Total":
                text += f" ({_percent(n, whole[column])})"
            cell = _Cell(text, n, right=True)
            if label in ("Translated", "Total"):
                cell.setFont(bold)
            table.setItem(row, column, cell)
    table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    table.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Fixed)
    height = table.horizontalHeader().sizeHint().height() + sum(
        table.rowHeight(r) for r in range(table.rowCount())
    )
    table.setFixedHeight(height + 2 * table.frameWidth())
    return table


def blocks_table(blocks: list[BlockStatistics]) -> QTableWidget:
    """One row per block: its translated strings, words and lines over all of
    them, and its share translated by words. A count column sorts by that
    share, so the blocks with the most left to do come together."""
    table = _table(len(blocks), BLOCK_HEADERS)
    table.verticalHeader().hide()
    for row, block in enumerate(blocks):
        table.setItem(row, 0, _Cell(block.file))
        table.setItem(row, 1, _Cell(block.block))
        done, whole = _values(block.translated), _values(block.total)
        for column, (d, w) in enumerate(zip(done, whole, strict=True), start=2):
            key = (d / w if w else 0.0, w)
            table.setItem(row, column, _Cell(f"{d:,} / {w:,}", key, right=True))
        share = done[1] / whole[1] if whole[1] else 0.0
        table.setItem(
            row,
            len(BLOCK_HEADERS) - 1,
            _Cell(_percent(done[1], whole[1]), share, right=True),
        )
    header = table.horizontalHeader()
    header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
    header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
    table.setSortingEnabled(True)
    table.sortItems(0, Qt.SortOrder.AscendingOrder)
    return table


class StatisticsDialog(QDialog):
    """The project's totals by status, and a sortable row per block."""

    def __init__(
        self, blocks: list[BlockStatistics], parent: QWidget | None = None
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(f"{APP_NAME} — Project Statistics")
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.totals = totals_table(summed(blocks))
        self.blocks = blocks_table(blocks)
        note = QLabel(
            "Counted on the original text. Translated is every string no longer"
            " its original: edited, for review or done."
        )
        note.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addWidget(self.totals)
        layout.addWidget(note)
        layout.addWidget(QLabel(f"Blocks ({len(blocks)})"))
        layout.addWidget(self.blocks, 1)
        layout.addWidget(close_box(self))
        self.resize(720, 560)


__all__ = ["StatisticsDialog"]
