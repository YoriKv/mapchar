"""Compressed bytes in the window: the Format bar's Compression picker, the
Decompressed View it arms, and the blocks that read and write through a slot."""

from __future__ import annotations

from pathlib import Path

import pytest

from conftest import ROOT
from mapchar.core.block import RangeSource
from mapchar.project.entry import EntryKind
from window_helpers import (
    ASCII_TABLE,
    add_block,
    arm_scheme,
    gba_packed,
    gba_packed_rom,
    open_rom_and_table,
)


def test_compressed_block_roundtrip(window, tmp_path, monkeypatch):
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    # The compressed slot has spare room at its end.
    _packed, slot, data = gba_packed_rom(payload, spare=9, tail=24)
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    arm_scheme(window, "gba_lz77")
    window._go_to(16)
    assert "compressed bytes at 10 → 90 bytes" in window.decompress_window.status.text()
    block = add_block(
        window,
        file_entry,
        "Z",
        RangeSource(0, len(payload)),
        # The fill byte is one the ASCII table maps nothing to, so what a
        # shorter string leaves behind is padding and not text.
        fill=b"\xff",
        compression_id="gba_lz77",
        slot_offset=16,
        slot_length=slot,
    )
    assert block.doc.data == payload and len(block.doc.strings) == 6
    window._on_translation_edited(0, "HI HI HI[end]")
    assert window._write_blocks([block])
    written = Path(file_entry.path).read_bytes()
    assert written[:16] == b"\xff" * 16 and written[16 + slot :] == b"\xff" * 24
    out = GbaLz77().decompress(written[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00" + b"\xff" * 9 + b"WORLD WORLD\x00")
    assert block.doc.strings[0].current_text() == "HI HI HI[end]"
    assert block.doc.strings[0].original == "HELLO HELLO HELLO[end]"


# -- the Compression picker and the Decompressed View ------------------------

RNC2_PAYLOAD = b"HELLO HELLO HELLO\x00WORLD WORLD WORLD\x00" * 4
"""Text enough to read in the preview's Text tab, and repetitive enough to pack."""


def _rnc2_rom(payload: bytes = RNC2_PAYLOAD, *, gap: int = 16) -> tuple[bytes, bytes]:
    """A ROM holding one RNC 2 stream at ``gap``, and the stream itself."""
    from mapchar.plugins.builtins.compression import rnc

    stream = rnc.compress(payload, method=2)
    return b"\xff" * gap + stream + b"\xff" * 32, stream


def test_the_compression_picker_arms_the_preview_and_turns_it_off(window, tmp_path):
    """A scheme picked on the Format bar is what the Decompressed View reads
    through, in both of its readings of the payload; ``none`` reads nothing."""
    data, stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    window._go_to(16)
    arm_scheme(window, "rnc2")
    assert window._preview_scheme == "rnc2" and view.isVisible()
    assert f"{len(stream):,} compressed bytes at 10 →" in view.status.text()
    # One decode, shown twice: the bytes and the text they read as.
    assert view.raw._model.data.startswith(b"HELLO")
    assert "HELLO HELLO HELLO" in view.text.edit.toPlainText()
    arm_scheme(window, "")
    assert window._preview_scheme is None and not view.isVisible()


def test_a_signature_arms_the_preview_by_itself_and_leaving_hides_it(window, tmp_path):
    data, stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    # The picker starts on automatic, and nothing announces itself at 0.
    assert window.compression_pick.currentData() is None
    assert not view.isVisible() and window._preview_scheme is None
    window._go_to(16)
    assert window._preview_scheme == "rnc2" and view.isVisible()
    # The picker only says it was left to the bytes, so the view names what
    # recognised them.
    assert view.status.text().startswith("RNC 2")
    # The file's own view washes the structure's compressed bytes.
    assert window.raw.structure() == (16, 16 + len(stream))
    window._go_to(17)
    assert window._preview_scheme is None and not view.isVisible()
    assert window.raw.structure() is None
    # A click on the stream's first byte arms it again: a selection is the
    # nearest thing the byte views have to a cursor.
    window._select_bytes(16, 1)
    assert window._preview_scheme == "rnc2" and view.isVisible()


def test_a_block_states_its_own_scheme_and_cannot_be_repicked(window, tmp_path):
    data, stream = _rnc2_rom()
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    plain = add_block(window, file_entry, "plain", RangeSource(0, 6))
    assert window.compression_pick.currentData() == ""
    assert not window.compression_pick.isEnabled()
    packed = add_block(
        window,
        file_entry,
        "packed",
        RangeSource(0, len(RNC2_PAYLOAD)),
        compression_id="rnc2",
        slot_offset=16,
        slot_length=len(stream),
    )
    assert packed.doc.data == RNC2_PAYLOAD
    assert window.compression_pick.currentData() == "rnc2"
    assert not window.compression_pick.isEnabled()
    # Back on the file, the pick is the file's own again.
    window._activate_entry(file_entry)
    assert window.compression_pick.isEnabled()
    assert window.compression_pick.currentData() is None
    assert plain.name and packed.name  # both rows are still there


def test_the_compression_pick_survives_a_project_save_and_load(window, tmp_path):
    data, _stream = _rnc2_rom()
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    arm_scheme(window, "rnc2")
    assert file_entry.session.preview_scheme == "rnc2"
    project = tmp_path / "p.mapchar"
    window.project_path = str(project)
    assert window._save_project()
    assert '"preview_scheme": "rnc2"' in project.read_text(encoding="utf-8")
    assert window.open_project(str(project))
    assert window.workspace.files()[0].session.preview_scheme == "rnc2"
    assert window.compression_pick.currentData() == "rnc2"


def test_find_all_lists_the_structures_and_selecting_one_moves_the_view(
    window, tmp_path
):
    from mapchar.plugins.builtins.compression import rnc

    first = rnc.compress(RNC2_PAYLOAD, method=2)
    second = rnc.compress(b"SECOND SECOND SECOND\x00" * 3, method=2)
    data = b"\xff" * 16 + first + b"\xff" * 8 + second + b"\xff" * 16
    at_second = 16 + len(first) + 8
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    # The view is opened from the menu, which is how it is reached anywhere but
    # on a structure; nothing decodes at 0, and it stays open.
    window._show_decompress()
    assert view.isVisible()
    # On automatic, Find All covers every scheme that announces itself.
    view.find_button.click()
    assert "2 structure(s)" in view.found.text()
    assert view.results.rowCount() == 2
    assert [view.results.item(r, 0).text() for r in range(2)] == [
        "10",
        f"{at_second:X}",
    ]
    assert view.results.item(0, 1).text() == f"{len(first):,}"
    assert view.results.item(0, 2).text() == f"{len(RNC2_PAYLOAD):,}"
    # The payload reads as text, which is what the Text column scores.
    assert float(view.results.item(0, 3).text()) > 0.5
    view.results.selectRow(1)
    assert window._offset == at_second and window._preview_scheme == "rnc2"
    # The list is one file's: another file on screen drops it.
    open_rom_and_table(window, tmp_path, b"\xff" * 64, rom_name="other.bin")
    assert view.results.rowCount() == 0


def test_to_block_under_automatic_arming_records_the_scheme_that_decoded(
    window, tmp_path
):
    data, stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    window._go_to(16)
    window.decompress_window.block.click()
    block = window.workspace.of_kind(EntryKind.BLOCK)[0]
    assert block.compression_id == "rnc2"
    assert (block.slot_offset, block.slot_length) == (16, len(stream))
    assert block.doc.data == RNC2_PAYLOAD


def test_the_decompressed_view_opens_from_the_menu_away_from_a_structure(
    window, tmp_path
):
    """The one way to reach the view — and its Find All — off a structure.

    A window opened by hand stays open when nothing decodes and says so in both
    readings; only one the preview opened for itself hides again.
    """
    data, stream = _rnc2_rom(gap=0x400)
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    assert window.decompress_action.isEnabled() and not view.isVisible()
    window._show_decompress()
    assert view.isVisible()
    assert "No scheme is armed" in view.status.text()
    assert "No scheme is armed" in view.hex_note.text()
    assert "No scheme is armed" in view.text_note.text()
    # A scheme picked by name reads nothing here either, and says why.
    arm_scheme(window, "rnc2")
    assert view.isVisible()
    assert "Nothing decodes at 0" in view.status.text()
    assert "no RNC magic" in view.hex_note.text()
    # Find All is in reach, and what it finds is what opens the structure.
    view.find_button.click()
    assert "1 structure(s)" in view.found.text()
    view.results.selectRow(0)
    assert window._offset == 0x400
    assert f"{len(stream):,} compressed bytes at 400 →" in view.status.text()
    # Leaving the structure no longer takes the window with it.
    window._select_bytes(0x401, 1)
    assert view.isVisible() and "Nothing decodes" in view.status.text()


def test_the_reason_nothing_decodes_is_worked_out_for_a_window_on_screen(
    window, tmp_path, monkeypatch
):
    """It costs a decode, and a hidden window has nobody to read it."""
    data, _stream = _rnc2_rom(gap=0x400)
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    arm_scheme(window, "rnc2")  # reads nothing at 0, and the view is hidden
    asked = []
    real = window._nothing_decodes
    monkeypatch.setattr(
        window,
        "_nothing_decodes",
        lambda doc, offset: asked.append(offset) or real(doc, offset),
    )
    window._select_bytes(4, 1)
    assert not view.isVisible() and asked == []
    # Opened, it fills both readings — and every refresh after keeps them.
    window._show_decompress()
    assert asked == [4] and "no RNC magic" in view.hex_note.text()
    window._select_bytes(8, 1)
    assert asked == [4, 8]


def test_scan_under_automatic_walks_every_scheme_that_announces_itself(
    window, tmp_path
):
    """Scan from a window opened anywhere: automatic is the default pick.

    Nothing is armed at 0, so a Scan that only walked the armed scheme would do
    nothing at all — which is the first thing a search for a structure does.
    """
    data, stream = _rnc2_rom(gap=0x200)
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    window._show_decompress()
    assert window.compression_pick.currentData() is None
    view.scan.click()
    assert window._offset == 0x200 and window._selection == (0x200, 0x201)
    # Automatic arming names what answered, as it does wherever the view lands.
    assert window._preview_scheme == "rnc2"
    assert f"{len(stream):,} compressed bytes at 200 →" in view.status.text()
    # With nothing to walk for, the window says so rather than doing nothing.
    arm_scheme(window, "")
    view.scan.click()
    assert "Pick a scheme" in view.status.text()
    assert window._offset == 0x200


def test_a_jump_and_a_drag_cost_one_preview_decode_at_most(window, tmp_path):
    """The preview reads from the selection's first byte, and only that.

    Selecting bytes moves the view and the selection both, and each of them
    used to refresh the preview; a drag over the bytes re-decoded at every step
    although the byte it reads from had not moved.
    """
    data, _stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    arm_scheme(window, "rnc2")
    decodes = []
    real = window._decompress_at

    def counted(doc, offset, **kw):
        decodes.append(offset)
        return real(doc, offset, **kw)

    window._decompress_at = counted
    window._select_bytes(16, 1)  # the view moves with the selection
    assert decodes == [16]
    window._select_bytes(16, 4)  # the view is already there
    assert decodes == [16, 16]
    decodes.clear()
    # A drag out from the same first byte: the same preview, not decoded again.
    window._on_selection(16, 24)
    window._on_selection(16, 32)
    assert decodes == []
    # Dragging the other way moves the byte it reads from, and does decode.
    window._on_selection(15, 32)
    assert decodes == [15]


def test_a_selection_never_pins_the_preview_where_a_jump_took_the_view(
    window, tmp_path
):
    """Every jump this view makes puts the cursor on what it jumped to.

    A click selects one byte, and the preview reads from the selection — so a
    jump that moved the view alone would leave the preview, Jump to Next and
    Scan all reading the byte the user last clicked.
    """
    from mapchar.plugins.builtins.compression import rnc

    first = rnc.compress(RNC2_PAYLOAD, method=2)
    second = rnc.compress(b"SECOND SECOND SECOND\x00" * 3, method=2)
    data = b"\xff" * 16 + first + b"\xff" * 8 + second + b"\xff" * 16
    at_second = 16 + len(first) + 8
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    # Picked by name, so the scheme holds between the streams as well as on them.
    arm_scheme(window, "rnc2")
    # A click on the stream's first byte, as the documented way to pick it out.
    window._select_bytes(16, 1)
    assert "compressed bytes at 10 →" in view.status.text()
    view.next.click()
    after = 16 + len(first)
    assert window._offset == after and window._selection == (after, after + 1)
    # And Scan continues from there rather than from the click.
    view.scan.click()
    assert window._offset == at_second and window._selection[0] == at_second
    assert f"compressed bytes at {at_second:X} →" in view.status.text()
    # A Structures row, with a selection somewhere else entirely.
    window._select_bytes(4, 3)
    view.find_button.click()
    view.results.selectRow(0)
    assert window._offset == 16 and window._selection == (16, 17)
    assert view.isVisible() and "compressed bytes at 10 →" in view.status.text()


def test_a_scan_and_find_all_refuse_to_run_inside_each_other(window, tmp_path):
    """One walk owns the view: the other's button is off and its handler refuses.

    Driven through a scheme that announces itself in no way, so Find All walks
    the buffer a byte at a time and reports progress on the way.
    """
    payload = b"HELLO HELLO HELLO\x00" * 4
    packed = gba_packed(payload)
    data = b"\xff" * 300 + packed + b"\xff" * 300
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    window._show_decompress()
    arm_scheme(window, "gba_lz77")
    live = []

    def progress(done, total, _real=view.progress):
        live.append((view.scan.isEnabled(), view.find_button.isEnabled()))
        window._scan_next_structure()  # refused: a walk is already running
        return _real(done, total)

    view.progress = progress
    view.find_button.click()
    assert live and not any(scan or find for scan, find in live)
    assert "1 structure(s)" in view.found.text()
    assert window._offset == 0 and not window._scanning
    # The window is usable again, and the walk's list survived it.
    assert view.scan.isEnabled() and view.find_button.isEnabled()
    assert view.results.rowCount() == 1 and view.isVisible()


def test_a_bookmark_and_jump_to_source_leave_a_pick_that_is_not_theirs(
    window, tmp_path
):
    """Only a compressed block, or a bookmark made under a scheme, picks one."""
    data, stream = _rnc2_rom()
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    arm_scheme(window, "")  # none, picked by name
    window._go_to(4)
    window._new_bookmark()
    bookmark = window.workspace.of_kind(EntryKind.BOOKMARK)[0]
    assert bookmark.compression_id is None
    window._go_to(0)
    window._jump_to_bookmark(bookmark)
    assert window._offset == 4 and file_entry.session.preview_scheme == ""
    # A plain block's Jump to Source leaves a named pick standing.
    arm_scheme(window, "rnc1")
    plain = add_block(window, file_entry, "plain", RangeSource(0, 6))
    window._jump_to_source(plain)
    assert file_entry.session.preview_scheme == "rnc1" and window._offset == 0
    # A bookmark made where a signature armed the preview picks nothing either:
    # automatic is what was set, and jumping back has to leave it automatic.
    arm_scheme(window, None)
    window._select_bytes(16, 1)
    assert window._preview_scheme == "rnc2"
    window._new_bookmark()
    on_stream = window.workspace.of_kind(EntryKind.BOOKMARK)[1]
    assert on_stream.compression_id is None
    window._jump_to_bookmark(on_stream)
    assert file_entry.session.preview_scheme is None
    # A compressed block's own scheme is still armed by jumping to its source.
    packed = add_block(
        window,
        file_entry,
        "packed",
        RangeSource(0, len(RNC2_PAYLOAD)),
        compression_id="rnc2",
        slot_offset=16,
        slot_length=len(stream),
    )
    window._jump_to_source(packed)
    assert file_entry.session.preview_scheme == "rnc2"
    assert window._offset == 16 and window._selection == (16, 17)


def test_a_pick_no_plugin_provides_says_so_and_is_still_the_files_own(window, tmp_path):
    """A scheme nothing can read is shown as missing rather than as automatic.

    Shown as automatic it would arm nothing while claiming to arm the bytes,
    and re-picking automatic would change nothing to change — so the state
    could not be got out of.
    """
    data, _stream = _rnc2_rom()
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    file_entry.session.preview_scheme = "gone_scheme"
    window._refresh_view()
    pick = window.compression_pick
    assert pick.currentText() == "gone_scheme (missing)"
    assert pick.currentData() == "gone_scheme"
    # A block's missing scheme is that block's, and does not stay on offer.
    block = add_block(
        window,
        file_entry,
        "gone",
        RangeSource(0, 4),
        compression_id="other_gone",
        slot_offset=0,
        slot_length=4,
    )

    def on_offer():
        return [pick.itemText(i) for i in range(pick.count())]

    assert pick.currentText() == "other_gone (missing)"
    window._activate_entry(file_entry)
    assert pick.currentText() == "gone_scheme (missing)"
    assert "other_gone (missing)" not in on_offer()
    # And picking a scheme that exists works, dead row and all.
    arm_scheme(window, "rnc2")
    assert file_entry.session.preview_scheme == "rnc2"
    assert "gone_scheme (missing)" not in on_offer()
    assert block.name  # the row is still there


def test_find_all_lists_every_structure_in_the_mk2_rom(window):
    """The 29 RNC 2 streams Mortal Kombat II (GB) carries, when the ROM is here.

    The ROM never enters the repository; without it this skips. What it pins is
    Find All against a real packer's output: the walk over a whole ROM finds
    every stream and nothing else.
    """
    rom = ROOT / "sample-projects" / "MK2" / "Mortal Kombat II (USA, Europe).gb"
    if not rom.exists():
        pytest.skip("the Mortal Kombat II ROM is not present")
    window.open_rom(str(rom))
    view = window.decompress_window
    view.find_button.click()
    assert "29 structure(s)" in view.found.text()
    assert view.results.item(0, 0).text() == "AC54"
    assert view.results.item(0, 1).text() == "1,951"
    assert view.results.item(0, 2).text() == "3,056"


def test_two_blocks_over_one_slot_write_together(window, tmp_path):
    """One compressed slot holds one stream, so both blocks over it fold into it.

    Compressing each block's own buffer separately would have the second splice
    at the slot's offset replace the first, and one of the two edits would vanish
    without anything being reported.
    """
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    _packed, slot, data = gba_packed_rom(payload, tail=24)
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    first = add_block(
        window,
        file_entry,
        "front",
        RangeSource(0, 30),
        fill=b"\xff",
        compression_id="gba_lz77",
        slot_offset=16,
        slot_length=slot,
    )
    second = add_block(
        window,
        file_entry,
        "back",
        RangeSource(30, 60),
        fill=b"\xff",
        compression_id="gba_lz77",
        slot_offset=16,
        slot_length=slot,
    )
    window._activate_entry(first)
    window._on_translation_edited(0, "HI HI HI[end]")
    window._activate_entry(second)
    window._on_translation_edited(0, "BYE[end]")
    assert window._write_blocks([first, second])
    written = Path(file_entry.path).read_bytes()
    out = GbaLz77().decompress(written[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00" + b"\xff" * 9)
    assert out[30:].startswith(b"BYE\x00" + b"\xff" * 14)
    assert not first.dirty and not second.dirty


def test_siblings_over_a_slot_share_its_payload_and_write_together(window, tmp_path):
    """Blocks over one compressed slot read one stream, so an edit in any of
    them is in the payload the others read, and a write of one writes the slot
    with every edit in it — and marks every block over it written."""
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    _packed, slot, data = gba_packed_rom(payload, tail=24)
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    slice_fields = dict(
        fill=b"\xff", compression_id="gba_lz77", slot_offset=16, slot_length=slot
    )
    written = add_block(
        window, file_entry, "written", RangeSource(0, 30), **slice_fields
    )
    clean = add_block(window, file_entry, "clean", RangeSource(30, 60), **slice_fields)
    edited = add_block(
        window, file_entry, "edited", RangeSource(60, 90), **slice_fields
    )
    window._on_translation_edited(0, "KEPT[end]")
    assert edited.dirty and edited.doc is not None and clean.doc is not None
    assert written.doc.data == edited.doc.data  # one stream, shared

    window._activate_entry(written)
    window._on_translation_edited(0, "HI HI HI[end]")
    assert window._write_blocks([written])

    assert not written.dirty and not edited.dirty and not clean.dirty
    assert edited.doc.strings[0].current_text() == "KEPT[end]"
    out = GbaLz77().decompress(
        Path(file_entry.path).read_bytes()[16:], PipelineContext()
    )
    assert out.startswith(b"HI HI HI") and out[60:].startswith(b"KEPT\x00")
