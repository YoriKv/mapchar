"""The bytes a block's translations live in: who holds them, who is unsaved
because of them, and what survives a re-reading.

Since translations are edited straight into the bytes, a compressed block's
payload is the only copy of its edits, and every block over the same slot reads
that one payload. These are the rules that follow: a re-reading keeps the
payload, a write never skips a block, and the unsaved mark is on every entry
whose buffer changed.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from mapchar.core.block import RangeSource
from mapchar.core.context import PipelineContext
from mapchar.plugins.builtins.compression import GbaLz77
from mapchar.project.workspace import EntryKind
from window_helpers import add_block, make_window, open_rom_and_table

PAYLOAD = bytes.fromhex("41 42 00 42 41 00 41 41 00") + b"\xee" * 7
"""Three end-terminated strings — ``AB``, ``BA``, ``AA`` — and fill after them."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _packed() -> tuple[bytes, int, bytes]:
    """The payload compressed, the slot it needs, and a ROM holding it at 16."""
    packed = GbaLz77().compress(PAYLOAD, PipelineContext())
    slot = len(packed) + 16  # room for a re-compression that packs worse
    return packed, slot, b"\xff" * 16 + packed + b"\xff" * (slot - len(packed) + 8)


def _compressed_block(window, tmp_path, name="z", stop=6, rom_name="rom.bin"):
    _packed_bytes, slot, data = _packed()
    file_entry = open_rom_and_table(window, tmp_path, data, rom_name=rom_name)
    block = add_block(
        window,
        file_entry,
        name,
        RangeSource(0, stop),
        fill=b"\xee",
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=slot,
    )
    return file_entry, block


# --- a re-reading keeps the payload ----------------------------------------


def test_a_block_edit_keeps_a_compressed_block_s_unsaved_bytes(
    window, tmp_path, monkeypatch
):
    """Re-pointing a compressed block re-reads its payload, never the slot.

    The payload holds the edits and nothing else does, so dropping it would
    throw them away while the block still read unsaved and a write still
    compressed the bytes from before.
    """
    _file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    # Its strings are edited, so the re-cut asks first.
    monkeypatch.setattr(type(window), "_ask", lambda *a: True)
    assert block.dirty and block.doc.data[:3] == bytes.fromhex("42 00 EE")

    window._push_block_edit(
        block, config=replace(block.config, source=RangeSource(0, 9))
    )

    assert block.doc is not None
    assert block.doc.data[:3] == bytes.fromhex("42 00 EE")
    assert block.dirty
    texts = [rec.current_text() for rec in block.doc.strings]
    assert texts == ["B[end]", "BA[end]", "AA[end]"]
    # The string the new reading did not cut differently keeps its original.
    assert block.doc.strings[0].original == "AB[end]"


def test_undoing_that_block_edit_keeps_them_too(window, tmp_path, monkeypatch):
    _file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    monkeypatch.setattr(type(window), "_ask", lambda *a: True)
    window._push_block_edit(
        block, config=replace(block.config, source=RangeSource(0, 9))
    )
    window.undo_stack.undo()
    assert block.config.source == RangeSource(0, 6)
    assert block.doc.data[:3] == bytes.fromhex("42 00 EE")
    assert block.doc.strings[0].current_text() == "B[end]" and block.dirty


def test_changing_the_scheme_asks_and_a_yes_leaves_nothing_unsaved(
    window, tmp_path, monkeypatch
):
    """The one block edit that cannot keep the payload asks before it happens,
    and what it discards it discards whole: a block still marked unsaved would
    claim edits no buffer holds and no write could resolve."""
    _file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    asked: list[str] = []
    monkeypatch.setattr(
        type(window), "_ask", lambda _s, _t, message: asked.append(message) or True
    )
    window._push_block_edit(block, compression_id=None)
    assert asked and "discards them" in asked[0]
    assert block.compression_id is None and not block.dirty


def test_changing_the_scheme_can_be_refused(window, tmp_path, monkeypatch):
    _file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    monkeypatch.setattr(type(window), "_ask", lambda *a: False)
    steps = window.undo_stack.count()
    window._push_block_edit(block, compression_id=None)
    assert block.compression_id == "gba_lz77" and window.undo_stack.count() == steps
    assert block.dirty and block.doc.data[:3] == bytes.fromhex("42 00 EE")


def test_a_container_edit_that_discards_edits_leaves_the_entries_clean(
    window, tmp_path
):
    """``Discard`` at the container gate reads the file from disk again, so the
    edits are gone — and with them the unsaved mark that claimed them."""
    file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    window._activate_entry(file_entry)
    window.overtype_bytes(0, b"\x42")
    assert file_entry.dirty and block.dirty

    window.apply_container(file_entry, "raw", file_entry.paths)

    assert not file_entry.dirty and not block.dirty
    # Both buffers went; the file's is back, read from the disk it never left.
    assert file_entry.doc.data[0] == 0xFF and block.doc is None


# --- a write never skips a block -------------------------------------------


def test_a_write_loads_a_compressed_block_whose_document_was_dropped(window, tmp_path):
    """A dropped document is not a block with nothing to write: its slot is
    read again and written, so the block cannot stay unsaved for ever."""
    file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    block.doc = None  # as a drop that kept the unsaved mark would leave it

    assert window._write_blocks([block])
    assert not block.dirty and not file_entry.dirty
    out = GbaLz77().decompress(
        Path(file_entry.path).read_bytes()[16:], PipelineContext()
    )
    assert out[:16] == PAYLOAD


def test_a_write_resolves_a_block_unsaved_for_its_notes(window, tmp_path):
    """A plain block has no slot to compress, but it is still written with its
    file — so ``Write All`` can actually clear it, which is what the project
    save's gate waits on."""
    file_entry = open_rom_and_table(
        window, tmp_path, PAYLOAD + b"\xff" * 8, rom_name="plain.bin"
    )
    block = add_block(window, file_entry, "b", RangeSource(0, 9), fill=b"\xee")
    window._on_notes_edited(0, "check this")
    assert block.dirty and window._writable_dirty() == [block]

    assert window._write_all()
    assert not block.dirty and window._writable_dirty() == []


def test_a_slot_that_cannot_be_read_is_reported_rather_than_skipped(window, tmp_path):
    file_entry, block = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    block.doc = None
    block.slice_offset = 4  # not where the compressed stream is

    assert not window._write_blocks([block])
    assert Path(file_entry.path).read_bytes()[:16] == b"\xff" * 16


# --- the unsaved mark is on every buffer that changed -----------------------


def _two_over_one_slot(window, tmp_path):
    file_entry, first = _compressed_block(window, tmp_path)
    second = add_block(
        window,
        file_entry,
        "second",
        RangeSource(6, 9),
        fill=b"\xee",
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=first.slice_length,
    )
    return file_entry, first, second


def test_a_sibling_over_the_same_slot_is_unsaved_too(window, tmp_path):
    """The edit is in the payload both blocks read, so both are unsaved by it.
    One left unstamped shows no mark and answers Write with "nothing to write"
    while its own bytes say otherwise."""
    file_entry, first, second = _two_over_one_slot(window, tmp_path)
    window._activate_entry(first)
    window._on_translation_edited(0, "B[end]")

    assert first.dirty and second.dirty
    assert second.doc.data[:3] == bytes.fromhex("42 00 EE")

    window._write_entry(second)
    assert not first.dirty and not second.dirty
    out = GbaLz77().decompress(
        Path(file_entry.path).read_bytes()[16:], PipelineContext()
    )
    assert out[:3] == bytes.fromhex("42 00 EE")


def test_a_write_counts_the_compressed_blocks_it_packed(window, tmp_path):
    """Both blocks over the slot are written with it, so both are what the write
    reports — the only blocks a write lands as things of their own."""
    file_entry, first, _second = _two_over_one_slot(window, tmp_path)
    window._activate_entry(first)
    window._on_translation_edited(0, "B[end]")

    assert window._write_all()
    message = window.statusBar().currentMessage()
    assert message == f"Wrote {file_entry.name} (2 compressed block(s))"
    # The file itself was clean — only the blocks over its slot were dirty — so
    # its revisions are the same on both sides of the step, and only the side
    # itself can say which of the two it is.
    window.undo_stack.undo()
    assert window.statusBar().currentMessage().startswith(f"Restored {file_entry.name}")


def test_an_editing_run_that_ends_where_it_began_leaves_every_sibling_clean(
    window, tmp_path
):
    """The net-zero run drops its step and hands the revision back — to every
    entry it stamped, not only the one the cell was in."""
    _file_entry, first, second = _two_over_one_slot(window, tmp_path)
    window._activate_entry(first)
    steps = window.undo_stack.count()
    window._on_translation_edited(0, "B[end]")
    window._on_translation_edited(0, "AB[end]")

    assert window.undo_stack.count() == steps
    assert not first.dirty and not second.dirty


def test_a_block_loaded_after_a_sibling_s_edit_inherits_its_unsaved_state(
    window, tmp_path
):
    file_entry, first = _compressed_block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    later = add_block(
        window,
        file_entry,
        "later",
        RangeSource(6, 9),
        fill=b"\xee",
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=first.slice_length,
    )
    assert later.doc.data[:3] == bytes.fromhex("42 00 EE")
    assert later.dirty


# --- a file with a compression of its own ----------------------------------


def test_a_compressed_file_shares_its_buffer_with_its_plain_blocks(window, tmp_path):
    """A file's own compression is the whole file's: the pipeline decodes it on
    the way in and the buffer is the result, which its plain blocks read. It is
    not a slot, and looking for one would leave those blocks showing bytes that
    are no longer there."""
    packed, _slot, _data = _packed()
    open_rom_and_table(window, tmp_path, b"\xff" * 4, rom_name="other.bin")
    rom = tmp_path / "packed.bin"
    rom.write_bytes(packed)
    file_entry = window.workspace.add(
        window.workspace.new_file(str(rom), compression_id="gba_lz77")
    )
    window._activate_entry(file_entry)
    assert file_entry.doc is not None and file_entry.doc.data == PAYLOAD
    block = add_block(window, file_entry, "b", RangeSource(0, 9), fill=b"\xee")

    assert window.workspace.entries_sharing(file_entry) == [file_entry, block]

    window._activate_entry(file_entry)
    window.overtype_bytes(0, b"\x42")
    assert block.doc.data[0] == 0x42
    assert file_entry.dirty
    assert [e for e in window.workspace.of_kind(EntryKind.FILE) if e.dirty] == [
        file_entry
    ]
