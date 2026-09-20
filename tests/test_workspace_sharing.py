"""The workspace's two scans over a file's blocks: which are blocks, and which
of them hold the same bytes."""

from __future__ import annotations

import pytest

from mapchar.core.context import PipelineContext
from mapchar.core.document import Document
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.workspace import Workspace


def doc(data: bytes = b"") -> Document:
    return Document(data, PipelineContext(), True)


@pytest.fixture
def ws() -> Workspace:
    return Workspace()


def test_blocks_of_skips_bookmarks_and_other_files(ws):
    f = ws.open_file("/roms/a.nes")
    other = ws.open_file("/roms/b.nes")
    b1 = ws.add(Entry(EntryKind.BLOCK, "b1", parent=f))
    ws.add(Entry(EntryKind.BOOKMARK, "mark", parent=f))
    b2 = ws.add(Entry(EntryKind.BLOCK, "b2", parent=f))
    ws.add(Entry(EntryKind.BLOCK, "elsewhere", parent=other))
    assert ws.blocks_of(f) == [b1, b2]
    assert ws.blocks_of(other) == ws.children(other)


def test_blocks_of_loaded_keeps_only_the_ones_with_a_document(ws):
    f = ws.open_file("/roms/a.nes")
    b1 = ws.add(Entry(EntryKind.BLOCK, "b1", parent=f, doc=doc()))
    ws.add(Entry(EntryKind.BLOCK, "b2", parent=f))
    assert ws.blocks_of(f, loaded=True) == [b1]


def test_a_file_shares_with_its_plain_blocks(ws):
    f = ws.open_file("/roms/a.nes", doc=doc())
    plain = ws.add(Entry(EntryKind.BLOCK, "plain", parent=f, doc=doc()))
    ws.add(Entry(EntryKind.BLOCK, "unloaded", parent=f))
    ws.add(Entry(EntryKind.BOOKMARK, "mark", parent=f, doc=doc()))
    ws.add(Entry(EntryKind.BLOCK, "packed", parent=f, compression_id="lz", doc=doc()))
    assert ws.entries_sharing(f) == [f, plain]
    assert ws.entries_sharing(plain) == [f, plain]


def test_an_unloaded_file_is_not_named_by_its_own_blocks(ws):
    f = ws.open_file("/roms/a.nes")
    plain = ws.add(Entry(EntryKind.BLOCK, "plain", parent=f, doc=doc()))
    assert ws.entries_sharing(plain) == [plain]


def test_a_compressed_block_shares_its_slot_only(ws):
    f = ws.open_file("/roms/a.nes", doc=doc())
    ws.add(Entry(EntryKind.BLOCK, "plain", parent=f, doc=doc()))
    one = ws.add(
        Entry(
            EntryKind.BLOCK,
            "one",
            parent=f,
            compression_id="lz",
            slot_offset=0x20,
            doc=doc(),
        )
    )
    two = ws.add(
        Entry(
            EntryKind.BLOCK,
            "two",
            parent=f,
            compression_id="lz",
            slot_offset=0x20,
            doc=doc(),
        )
    )
    # Another offset, another scheme and another file are each a slot of their own.
    ws.add(
        Entry(
            EntryKind.BLOCK,
            "far",
            parent=f,
            compression_id="lz",
            slot_offset=0x80,
            doc=doc(),
        )
    )
    ws.add(
        Entry(
            EntryKind.BLOCK,
            "rle",
            parent=f,
            compression_id="rle",
            slot_offset=0x20,
            doc=doc(),
        )
    )
    g = ws.open_file("/roms/b.nes", doc=doc())
    ws.add(
        Entry(
            EntryKind.BLOCK,
            "twin",
            parent=g,
            compression_id="lz",
            slot_offset=0x20,
            doc=doc(),
        )
    )
    assert ws.entries_sharing(one) == [one, two]
    assert ws.entries_sharing(two) == [one, two]


def test_an_unloaded_block_over_a_slot_names_the_others_not_itself(ws):
    f = ws.open_file("/roms/a.nes", doc=doc())
    held = ws.add(
        Entry(EntryKind.BLOCK, "held", parent=f, compression_id="lz", doc=doc())
    )
    fresh = ws.add(Entry(EntryKind.BLOCK, "fresh", parent=f, compression_id="lz"))
    assert ws.entries_sharing(fresh) == [held]


def test_a_files_own_compression_is_not_a_slot(ws):
    """The pipeline decodes it on the way in, so the file's buffer is the
    payload and its plain blocks read it."""
    f = ws.open_file("/roms/a.nes", compression_id="lz", doc=doc())
    plain = ws.add(Entry(EntryKind.BLOCK, "plain", parent=f, doc=doc()))
    assert ws.entries_sharing(f) == [f, plain]
