"""File ▸ Project Statistics: the words, lines and strings the project holds,
and how many of them are translated."""

from __future__ import annotations

import pytest

from mapchar.core.block import Status, StringRecord
from mapchar.core.context import PipelineContext
from mapchar.core.document import Document
from mapchar.project.entry import Entry, EntryKind, StringState
from mapchar.project.statistics import (
    Tally,
    project_statistics,
    summed,
    text_tally,
)
from mapchar.project.workspace import Workspace


def doc(*strings: tuple[Status, str]) -> Document:
    document = Document(b"", PipelineContext(), True)
    document.strings = [
        StringRecord(i, i * 8, i * 8 + 8, [], status=status, original=text)
        for i, (status, text) in enumerate(strings)
    ]
    return document


@pytest.fixture
def ws() -> Workspace:
    return Workspace()


@pytest.mark.parametrize(
    ("text", "words", "lines"),
    [
        ("Hello there!", 2, 1),
        ("Hello,\nworld.", 2, 2),
        # A code separates words and is not one; a line of codes alone is none.
        ("Take the[KEY]sword[END]", 3, 1),
        ("[WAIT]\nYes", 1, 1),
        ("... !", 0, 1),
        ("don't stop", 2, 1),
        ("", 0, 0),
        (None, 0, 0),
    ],
)
def test_a_string_counts_its_words_and_the_lines_that_hold_text(text, words, lines):
    assert text_tally(text) == Tally(1, words, lines)


def test_text_the_script_grammar_refuses_is_still_counted():
    assert text_tally("a [broken") == Tally(1, 2, 1)


def test_blocks_count_by_status_and_translated_is_every_touched_one(ws):
    f = ws.open_file("/roms/a.nes", "a.nes")
    ws.add(
        Entry(
            EntryKind.BLOCK,
            "menu",
            parent=f,
            doc=doc(
                (Status.UNTOUCHED, "New game"),
                (Status.EDITED, "Load game"),
                (Status.REVIEW, "Options\nand more"),
                (Status.DONE, "Quit"),
            ),
        )
    )
    (block,) = project_statistics(ws)
    assert (block.file, block.block) == ("a.nes", "menu")
    assert block.total == Tally(4, 8, 5)
    assert block.translated == Tally(3, 6, 4)
    assert block.of(Status.UNTOUCHED) == Tally(1, 2, 1)
    assert block.of(Status.DONE) == Tally(1, 1, 1)


def test_a_block_not_yet_read_counts_the_originals_the_project_keeps(ws):
    """Otherwise the project's totals would grow as its blocks were opened."""
    f = ws.open_file("/roms/a.nes")
    ws.add(Entry(EntryKind.BLOCK, "read", parent=f, doc=doc((Status.DONE, "Hi"))))
    unread = Entry(EntryKind.BLOCK, "unread", parent=f)
    unread.pending_strings = {
        0: StringState("Two words", Status.EDITED),
        1: StringState("Three more words", Status.UNTOUCHED),
        2: StringState(None, Status.UNTOUCHED),
    }
    ws.add(unread)
    blocks = project_statistics(ws)
    assert [b.block for b in blocks] == ["read", "unread"]
    total = summed(blocks)
    assert total.total == Tally(4, 6, 3)
    assert total.translated == Tally(2, 3, 2)


def test_a_block_with_nothing_read_or_kept_counts_nothing(ws):
    ws.add(Entry(EntryKind.BLOCK, "empty", parent=ws.open_file("/roms/a.nes")))
    (block,) = project_statistics(ws)
    assert block.total == Tally()


def test_the_dialog_shows_the_totals_and_a_row_per_block(qtbot, ws):
    from mapchar.ui.statistics_dialog import STATUS_ROWS, StatisticsDialog

    f = ws.open_file("/roms/a.nes", "a.nes")
    ws.add(Entry(EntryKind.BLOCK, "b", parent=f, doc=doc((Status.DONE, "One two"))))
    ws.add(Entry(EntryKind.BLOCK, "a", parent=f, doc=doc((Status.UNTOUCHED, "Three"))))
    dialog = StatisticsDialog(project_statistics(ws))
    qtbot.addWidget(dialog)
    rows = [label for label, _ in STATUS_ROWS]
    translated = rows.index("Translated")
    assert dialog.totals.item(translated, 1).text() == "2 (66%)"
    assert dialog.totals.item(rows.index("Total"), 0).text() == "2"
    assert dialog.blocks.rowCount() == 2
    dialog.blocks.sortItems(5)  # by the share translated
    assert [dialog.blocks.item(r, 1).text() for r in range(2)] == ["a", "b"]
    assert dialog.blocks.item(1, 3).text() == "2 / 2"
    assert dialog.blocks.item(1, 5).text() == "100%"
