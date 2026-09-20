"""The searches that walk a whole file from the window: a scanned region
becoming the block that reads it, and pointer discovery under a Stop button."""

from __future__ import annotations

from helpers import texts
from mapchar.core.block import RangeSource
from mapchar.project.entry import EntryKind
from window_helpers import TABLE, add_block, open_rom_and_table


def test_pointer_discovery_runs_under_a_stop_button(window, tmp_path, monkeypatch):
    """The promise of architecture §1: every long operation pumps the event loop
    through a progress callback and can be cancelled. The search here is every
    mapping crossed with every width and endianness over the whole file."""
    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00" + b"\xff" * 8)
    add_block(window, file_entry, "b", RangeSource(0, 3))
    seen = {}

    def stub(data, starts, mappings, *, progress=None, **kw):
        seen["hooked"] = progress is not None
        window._pointer_progress.cancel()  # the Stop button, pressed
        seen["told_to_stop"] = progress(1, 2) is False
        return []

    monkeypatch.setattr("mapchar.ui.dialogs.PointerSearchDialog.exec", lambda self: 1)
    monkeypatch.setattr("mapchar.ui.main_window.pointers.discover", stub)
    window._find_pointers()
    assert seen["hooked"] and seen["told_to_stop"]
    assert any("stopped" in message for message in window.errors)


def test_a_block_from_a_scanned_region_undoes_with_its_end_token(window, tmp_path):
    """The guessed terminator and the block are one gesture, so one undo takes
    both back out — otherwise undoing the block leaves the entry behind."""
    from mapchar.engines.textscan import Region
    from mapchar.project.formats.table_native import HEADER

    # No end token of its own: the scan's guessed terminator becomes the block's.
    open_rom_and_table(
        window,
        tmp_path,
        b"AB\x0fCD\x0f",
        table=f"{HEADER}\n@table main\n41=A\n42=B\n",
    )
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    blocks = len(window.workspace.of_kind(EntryKind.BLOCK))
    window._block_from_region(Region(0, 6, 1.0, terminator=0x0F))
    assert len(table_entry.table.entries) == before + 1
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == blocks + 1
    window.undo_stack.undo()
    assert len(table_entry.table.entries) == before
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == blocks


def test_a_block_from_a_scanned_region_keeps_a_table_that_already_has_an_end(
    window, tmp_path
):
    """A table that already labels [end] on other bits keeps it: the guessed
    terminator is not added, and the block still comes out."""
    from mapchar.engines.textscan import Region

    open_rom_and_table(
        window,
        tmp_path,
        b"AB\x0fCD\x0f",
        table=TABLE,
    )
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    blocks = len(window.workspace.of_kind(EntryKind.BLOCK))
    window._block_from_region(Region(0, 6, 1.0, terminator=0x0F))
    assert len(table_entry.table.entries) == before
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == blocks + 1


def test_a_block_from_a_scanned_record_chain_reads_it_behind_its_header(
    window, tmp_path
):
    """A region the scan found as a chain of length-prefixed records becomes a
    block that reads it that way — the length prefix and the header in front of
    it — and no end token is guessed for a string that carries its own length."""
    from mapchar.core.block import Pascal
    from mapchar.engines.textscan import Records, Region
    from mapchar.project.formats.table_native import HEADER

    data = b"\x90\xa1\x02AB\x90\xa1\x03ABC"
    open_rom_and_table(
        window, tmp_path, data, table=f"{HEADER}\n@table main\n41=A\n42=B\n43=C\n"
    )
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    window._block_from_region(Region(0, len(data), 1.0, records=Records(header=2)))
    entry = window._entry
    assert entry.config.string_type == Pascal(1) and entry.config.header == 2
    assert texts(entry.doc.strings) == ["AB", "ABC"]
    assert len(table_entry.table.entries) == before
    window.undo_stack.undo()
    assert window._entry is not entry


def test_a_block_from_a_scanned_region_reads_through_the_current_table(
    window, tmp_path
):
    """The scan scored the region through the table the file is read through,
    so the block it becomes reads it through that one — a record chain as much
    as a terminated region, which keeps the whole of the current reading."""
    from mapchar.engines.textscan import Records, Region
    from mapchar.project.formats.table_native import HEADER

    data = b"\x90\xa1\x02AB\x90\xa1\x03ABC"
    entry = open_rom_and_table(
        window, tmp_path, data, table=f"{HEADER}\n@table main\n41=X\n42=Y\n43=Z\n"
    )
    other = tmp_path / "other.tbl"
    other.write_text(f"{HEADER}\n@table other\n41=A\n42=B\n43=C\n/00=[end]\n")
    window.open_table(str(other))
    window._activate_entry(entry)
    window._choose_table("other")
    window._block_from_region(Region(0, len(data), 1.0, records=Records(header=2)))
    block = window._entry
    assert block.config.table_id == "other"
    assert texts(block.doc.strings) == ["AB", "ABC"]
    window._activate_entry(entry)
    window._block_from_region(Region(0, 6, 1.0, terminator=0x00))
    assert window._entry.config.table_id == "other"


def test_the_scan_window_says_how_each_region_cuts_its_strings():
    """The Strings column is the Reading bar's words, never a class name."""
    from mapchar.engines.textscan import Records, Region
    from mapchar.ui.scan_window import _strings_of

    assert _strings_of(Region(0, 8, 1.0, records=Records(header=2))) == (
        "Length prefix, header 2"
    )
    assert _strings_of(Region(0, 8, 1.0, records=Records())) == "Length prefix"
    assert _strings_of(Region(0, 8, 1.0, terminator=0x00)) == "End token 00"
    assert _strings_of(Region(0, 8, 1.0)) == ""
