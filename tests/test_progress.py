"""What the block bar says about how far the translation has got."""

from __future__ import annotations

import pytest

from mapchar.core.block import Status, StringRecord
from mapchar.core.context import PipelineContext
from mapchar.core.document import Document
from mapchar.project.progress import progress_text
from mapchar.project.workspace import Entry, EntryKind, StringState, Workspace


def doc(*statuses: Status) -> Document:
    """A document of one string per status, which is all progress counts."""
    document = Document(b"", PipelineContext(), True)
    document.strings = [
        StringRecord(i, i * 8, i * 8 + 8, [], status=status)
        for i, status in enumerate(statuses)
    ]
    return document


@pytest.fixture
def ws() -> Workspace:
    return Workspace()


def test_a_project_of_one_block_says_only_the_block(ws):
    d = doc(Status.UNTOUCHED, Status.EDITED, Status.DONE)
    f = ws.open_file("/roms/a.nes")
    ws.add(Entry(EntryKind.BLOCK, "b", parent=f, doc=d))
    assert progress_text(ws, d) == "translated 2 / 3 (66%), done 1"


def test_the_project_is_counted_beside_the_block(ws):
    d = doc(Status.EDITED)
    f = ws.open_file("/roms/a.nes")
    ws.add(Entry(EntryKind.BLOCK, "b", parent=f, doc=d))
    ws.add(Entry(EntryKind.BLOCK, "other", parent=f, doc=doc(Status.UNTOUCHED)))
    assert progress_text(ws, d) == "translated 1 / 1 (100%) · project 1 / 2 (50%)"


def test_a_block_not_yet_read_is_counted_from_the_state_the_project_keeps(ws):
    """Otherwise a project's progress would climb as its blocks were opened."""
    d = doc(Status.EDITED)
    f = ws.open_file("/roms/a.nes")
    ws.add(Entry(EntryKind.BLOCK, "b", parent=f, doc=d))
    unread = Entry(EntryKind.BLOCK, "unread", parent=f)
    unread.pending_strings = {
        0: StringState(status=Status.DONE),
        1: StringState(status=Status.UNTOUCHED),
    }
    ws.add(unread)
    assert progress_text(ws, d) == (
        "translated 1 / 1 (100%) · project 2 / 3 (66%), done 1"
    )


def test_a_block_with_no_strings_at_all_reads_as_nothing_over_nothing(ws):
    d = doc()
    ws.add(Entry(EntryKind.BLOCK, "b", parent=ws.open_file("/roms/a.nes"), doc=d))
    assert progress_text(ws, d) == "translated 0 / 0"
