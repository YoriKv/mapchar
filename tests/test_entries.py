"""What an entry holds and what may never be lost with it: the translations a
re-read, a block edit or a container change has to keep, the quit gate, Jump to
Source, and the reading bar's own fields."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtWidgets import QMessageBox

from helpers import pointer_rom
from mapchar.core.block import (
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Status,
)
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.projectfile import entries_from_payload, entries_payload
from mapchar.ui.files_panel import STATUS_COL
from window_helpers import (
    ab_ba_rom,
    add_block,
    arm_scheme,
    item_for,
    make_yes_window,
    open_rom_and_table,
)

DATA = ab_ba_rom(20)


@pytest.fixture
def window(qtbot, monkeypatch):
    """A live window whose every modal answers Yes / Discard without showing."""
    return make_yes_window(qtbot, monkeypatch)


# -- string state survives what re-reads a block -------------------------------


def test_removing_a_table_keeps_every_translation(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    assert block.doc.strings[0].current_text() == "BB[end]"

    table_entry = window.workspace.of_kind(EntryKind.TABLE)[0]
    window._remove_entries([table_entry])
    assert table_entry not in window.workspace.entries
    # The records are still there, and stashed where a document drop cannot
    # reach them, rather than replaced by an empty list.
    assert [r.current_text() for r in block.doc.strings] == ["BB[end]", "BA[end]"]
    assert block.pending_strings[0].original == "AB[end]"
    # And the row says why it cannot be re-read.
    window.files_panel.refresh_labels()
    item = item_for(window, block)
    assert item.text(STATUS_COL) == "!"
    assert "not loaded" in item.toolTip(0)


def test_a_failed_reload_does_not_clear_the_strings(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 1, "AA[end]")
    # The table set goes away without the entry going away: an extraction with
    # no tables must keep what is there.
    window._extract_current(block, block.doc, None)
    assert [r.current_text() for r in block.doc.strings] == ["AB[end]", "AA[end]"]


def test_edit_block_carries_translations_and_is_one_undo_step(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BA[end]")
    before = (
        block.name,
        block.config,
        block.compression_id,
        block.spare_room,
        block.room,
    )
    window.undo_stack.clear()

    wider = block.config.__class__(
        **{**block.config.__dict__, "source": RangeSource(0, 6)}
    )
    window.apply_block_config(block, "renamed", wider, None, "keep")
    assert block.name == "renamed"
    # The strings sit at the same bits, so their originals stay with them.
    assert block.doc.strings[0].current_text() == "BA[end]"
    assert block.doc.strings[0].original == "AB[end]"

    # As a command it undoes in one step, name, config and all.
    window.undo_stack.clear()
    from mapchar.ui.undo_commands import BlockEditCommand

    after = ("second", wider, None, "fill", None)
    window.undo_stack.push(BlockEditCommand(window, block, before, after))
    assert block.name == "second" and block.spare_room == "fill"
    window.undo_stack.undo()
    assert block.name == before[0] and block.spare_room == before[3]
    assert block.doc.strings[0].current_text() == "BA[end]"
    assert block.doc.strings[0].original == "AB[end]"


def test_a_block_edit_that_cuts_a_string_elsewhere_takes_its_original_afresh(
    window, tmp_path
):
    """A string the new reading cuts at other bits is not the same string, so
    the original it shows is what those bytes say, not what an older string at
    that index said — and neither is it the string that was marked or annotated.
    """
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    assert block.doc.strings[0].original == "AB[end]"
    block.doc.strings[0].status = Status.DONE
    block.doc.strings[0].notes = "checked"
    block.doc.strings[1].status = Status.REVIEW
    block.doc.strings[1].notes = "kept"
    from dataclasses import replace

    window.apply_block_config(
        block, block.name, replace(block.config, source=RangeSource(1, 6)), None, "fill"
    )
    assert [r.current_text() for r in block.doc.strings] == ["B[end]", "BA[end]"]
    assert [r.original for r in block.doc.strings] == ["B[end]", "BA[end]"]
    # The mark and the notes were about text that is no longer there: the
    # string cut elsewhere starts afresh, and the one still at its own bits
    # keeps everything. A held mark would otherwise survive ``refresh_status``.
    assert [r.status for r in block.doc.strings] == [Status.UNTOUCHED, Status.REVIEW]
    assert [r.notes for r in block.doc.strings] == ["", "kept"]


# -- the quit gate -------------------------------------------------------------


def test_the_project_is_asked_about_before_the_file_edits(
    window, tmp_path, monkeypatch
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    assert file_entry.dirty
    window._saved_snapshot = "{}"  # a project that has been saved and has moved on
    assert window._project_dirty()

    asked: list[str] = []
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: (
            asked.append("project"),
            QMessageBox.StandardButton.Discard,
        )[1],
    )
    monkeypatch.setattr(
        window,
        "_resolve_dirty_entries",
        lambda *a, **k: (asked.append("entries"), True)[1],
    )
    assert window._confirm_discard("quit")
    assert asked == ["project", "entries"]


def test_the_file_gate_offers_write_all_continue_without_cancel(
    window, tmp_path, monkeypatch
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    labels: list[str] = []

    def capture(box):
        labels.extend(b.text().replace("&", "") for b in box.buttons())
        return 0

    # Qt lays the buttons out by role, so the set is what the labels promise.
    monkeypatch.setattr(QMessageBox, "exec", capture)
    window._resolve_dirty_entries("Unsaved edits are lost")
    assert sorted(labels) == ["Cancel", "Continue Without", "Write All"]


# -- Jump to Source ------------------------------------------------------------


def test_jump_to_source_lands_on_the_block_offset(window, tmp_path):
    rom = bytes(0x40) + DATA
    file_entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, file_entry, "b", RangeSource(0x40, 0x46))
    window._jump_to_source(block)
    assert window._entry is file_entry
    assert window._offset == 0x40
    assert window.format_pick.currentData() == "main"


def test_jump_to_source_uses_the_first_pointer_of_a_pointer_list(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(0x100))
    block = Entry(
        EntryKind.BLOCK,
        "ptrs",
        file_entry.path,
        parent=file_entry,
        config=add_block(
            window, file_entry, "seed", RangeSource(0, 4)
        ).config.__class__(
            source=PointerListSource((0x30, 0x20), size=2), table_id="main"
        ),
    )
    window._push_add(block)
    assert window._block_file_offset(block) == 0x20
    window._jump_to_source(block)
    assert window._offset == 0x20


def test_jump_to_source_of_a_pointer_table_uses_the_table_address(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(0x100))
    seed = add_block(window, file_entry, "seed", RangeSource(0, 4))
    block = Entry(
        EntryKind.BLOCK,
        "ptab",
        file_entry.path,
        parent=file_entry,
        config=seed.config.__class__(
            source=PointerTableSource(0x40, 0x44, 2, 2), table_id="main"
        ),
    )
    window._push_add(block)
    assert window._block_file_offset(block) == 0x40
    window._jump_to_source(block)
    assert window._entry is file_entry and window._offset == 0x40
    # The file is read the way the block reads it: as a pointer table.
    assert window.reading_bar.source_kind.currentData() == "table"


def test_jump_to_source_of_a_string_row_opens_that_string(window, tmp_path):
    """A string's source is the string, wherever the pointer reaching it put
    it — the Strings view on it, not the table the block's own row jumps to."""
    rom = pointer_rom((0x10, 0x13), "41 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, file_entry, "ptab", PointerTableSource(0, 4, 2, 2))
    window._jump_to_source(block)
    assert window._entry is file_entry
    menu = window._build_files_menu(block, 1)
    next(a for a in menu.actions() if a.text() == "&Jump to Source").trigger()
    assert window._entry is block and window._current_view() == "strings"
    assert window.strings.selected_indices() == [1]
    assert window._offset == block.doc.strings[1].start


def test_jump_to_source_of_a_compressed_block_uses_its_slot(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(0x100))
    block = add_block(window, file_entry, "packed", RangeSource(0, 8))
    block.compression_id = "rle1"
    block.slot_offset = 0x50
    assert window._block_file_offset(block) == 0x50
    window._jump_to_source(block)
    # The file shows the packed structure at that address, so the scheme that
    # reads it is armed in the Compression preview.
    assert window._entry is file_entry and window._offset == 0x50
    assert window._preview_scheme == "rle1"


# -- dialogs -------------------------------------------------------------------


def test_the_reading_bar_carries_spare_room_for_a_compressed_block(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    plain = add_block(window, file_entry, "plain", RangeSource(0, 6))
    assert not window.reading_bar.writing.spare_room.isEnabled()
    block = add_block(window, file_entry, "b", RangeSource(0, 6), compression_id="rle1")
    window.undo_stack.clear()
    assert window.reading_bar.writing.spare_room.isEnabled()
    window.reading_bar.writing.spare_room.setCurrentIndex(1)
    assert block.spare_room == "keep" and block.compression_id == "rle1"
    # Write mode and the fill byte round-trip through the same bar.
    assert window.reading_bar.config(block.config, "main").fill == block.config.fill
    window.undo_stack.undo()
    assert block.spare_room == "fill"
    window._activate_entry(plain)
    assert not window.reading_bar.writing.spare_room.isEnabled()


def test_a_new_reading_turned_to_pointers_starts_on_the_suggested_mapping(
    window, tmp_path
):
    rom = tmp_path / "game.smc"
    rom.write_bytes(bytes(0x8000) + b"\x00" * 0x8000)
    window.open_rom(str(rom))
    suggested = window._suggested_mapping()
    assert suggested
    window.mode_toggle.button(True).click()
    assert window._reading().source.mapping_id == suggested


def test_editing_the_container_is_one_undo_step(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    second = tmp_path / "chip2.bin"
    second.write_bytes(b"\x00" * 8)
    window.undo_stack.clear()
    from mapchar.ui.undo_commands import ContainerCommand

    before = (file_entry.container_id, file_entry.paths)
    after = ("raw", (file_entry.path, str(second)))
    window.undo_stack.push(ContainerCommand(window, file_entry, before, after))
    assert file_entry.extra_paths == (str(second),)
    assert block.extra_paths == (str(second),)  # the children follow the join
    window.undo_stack.undo()
    assert file_entry.extra_paths == ()
    assert block.extra_paths == ()


# -- writing and the decompressed view ----------------------------------------


def test_write_on_a_bookmark_or_table_explains_itself(window, tmp_path, monkeypatch):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    said: list[str] = []
    monkeypatch.setattr(window, "_error", said.append)
    bookmark = Entry(
        EntryKind.BOOKMARK, "bm", file_entry.path, parent=file_entry, bookmark_offset=4
    )
    window._push_add(bookmark)
    window._write_entry(bookmark)
    window._write_entry(window.workspace.of_kind(EntryKind.TABLE)[0])
    assert len(said) == 2
    assert "saved position" in said[0]
    assert "Save As File" in said[1]


def test_a_bookmark_snapshots_the_settings_it_was_made_under(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    window._go_to(3)
    window._new_bookmark()
    bookmark = window.workspace.of_kind(EntryKind.BOOKMARK)[0]
    assert bookmark.bookmark_offset == 3
    assert bookmark.session.table_id == "main"
    assert bookmark.compression_id == window._preview_scheme
    window._go_to(0)
    window._jump_to_bookmark(bookmark)
    assert window._offset == 3 and window._entry is file_entry


def test_the_decompressed_view_has_a_stop_that_cancels(window, tmp_path):
    view = window.decompress_window
    assert not view.stop.isEnabled()
    with view.running():
        assert view.stop.isEnabled() and not view.scan.isEnabled()
        assert not view.cancelled
        view.stop.click()
        assert view.cancelled
        assert not view.progress(1, 2)  # False asks the scan to stop
    assert view.scan.isEnabled() and not view.stop.isEnabled()
    window._set_scan_ui(True)
    assert not window.menuBar().isEnabled()
    assert not view.raw.isEnabled()
    window._set_scan_ui(False)
    assert window.menuBar().isEnabled()


def test_a_partial_decode_is_never_a_structure_to_act_on(window, tmp_path):
    """Jump to Next and To Block need a *complete* structure, read strictly.

    A prefix's ``consumed`` is how far the window reached, not where the
    structure ends, so stepping by it lands mid-stream and recording it as a
    block's slot length hands the write-back a boundary nobody measured.
    """
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    packed = GbaLz77().compress(b"HELLO HELLO HELLO\x00" * 4, PipelineContext())
    cut = packed[: len(packed) // 2]  # the stream stops before its end marker
    file_entry = open_rom_and_table(window, tmp_path, b"\xff" * 16 + cut)
    window._activate_entry(file_entry)
    arm_scheme(window, "gba_lz77")
    window._go_to(16)
    # The preview reads it, and says so; the two actions refuse it.
    assert window._decompress_at(window._doc, 16) is not None
    assert window._complete_structure_at(window._doc, 16) is None
    window._jump_next_structure()
    assert window._offset == 16
    before = len(window.workspace.entries)
    window._structure_to_block()
    assert len(window.workspace.entries) == before


def test_the_reading_bar_keeps_a_target_offset_it_cannot_read(window, tmp_path):
    """A number nobody can read keeps the value there was, and the app's one
    number spelling is what it reads, signs and $hex alike."""
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", PointerTableSource(0, 4, 2, 2))
    bar = window.reading_bar
    bar.ptr_offset.setText("$-10")
    bar.ptr_offset.editingFinished.emit()
    assert block.config.source.offset == -0x10
    bar.ptr_offset.setText("-")
    bar.ptr_offset.editingFinished.emit()
    assert block.config.source.offset == -0x10


def test_a_slot_length_round_trips_and_a_falsy_one_means_unknown(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "packed", RangeSource(0, 6))
    block.compression_id = "rle1"
    block.slot_offset = 0x10

    def reread() -> Entry:
        return entries_from_payload(entries_payload([file_entry, block]))[1]

    block.slot_length = 0x20
    assert (reread().slot_offset, reread().slot_length) == (0x10, 0x20)
    # Both sides read a falsy length as "nobody measured it", so neither a
    # missing key nor a stored 0 comes back as a slot with no room in it.
    for length in (0, None):
        block.slot_length = length
        assert reread().slot_length is None


# -- dropping a document never drops the work ----------------------------------


def test_every_ui_document_drop_goes_through_the_workspace():
    """``entry.doc = None`` in the UI is how translations went missing.

    Once a load has consumed ``pending_strings`` the document is the only place
    they exist, so the one place allowed to forget one is
    :meth:`~mapchar.project.workspace.Workspace.drop_document`, which stashes
    them first. This is the invariant, so a new drop cannot reintroduce the bug.
    """
    import mapchar.ui

    root = Path(mapchar.ui.__file__).parent
    offenders = [
        f"{path.relative_to(root)}:{n}"
        for path in sorted(root.rglob("*.py"))
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if ".doc = None" in line
    ]
    assert offenders == []


def test_re_picking_the_start_table_keeps_the_translations(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    window._on_format_pick()  # re-reads the region under the picked table
    assert block.doc is not None
    assert block.doc.strings[0].current_text() == "BB[end]"
    assert block.doc.strings[0].original == "AB[end]"


def test_changing_the_container_chain_keeps_a_blocks_originals(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    assert window._write_blocks([block])
    # Drops the file's document and every child's: the file is read again.
    window.apply_container(file_entry, file_entry.container_id, file_entry.paths)
    assert block.doc is not None
    assert block.doc.strings[0].current_text() == "BB[end]"
    assert block.doc.strings[0].original == "AB[end]"


def test_a_container_edit_over_unsaved_edits_goes_through_the_gate(
    window, tmp_path, monkeypatch
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BB[end]")
    asked: list[str] = []
    monkeypatch.setattr(
        window,
        "_resolve_dirty_entries",
        lambda *a, **k: (asked.append("gate"), False)[1],
    )
    window._edit_container(file_entry)
    assert asked == ["gate"]  # and Cancel left the dialog unopened


def test_find_pointers_keeps_the_translations_it_re_reads(
    window, tmp_path, monkeypatch
):
    from mapchar.ui.dialogs import DiscoveryDialog, PointerSearchDialog

    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, file_entry, "b", RangeSource(0x10, 0x16))
    window._set_translation(block, 0, "BB[end]")
    monkeypatch.setattr(
        PointerSearchDialog,
        "exec",
        lambda self: PointerSearchDialog.DialogCode.Accepted,
    )
    monkeypatch.setattr(
        DiscoveryDialog, "exec", lambda self: DiscoveryDialog.DialogCode.Accepted
    )
    window._find_pointers()
    assert isinstance(block.config.source, PointerTableSource)
    assert block.doc is not None
    assert block.doc.strings[0].current_text() == "BB[end]"
    assert block.doc.strings[0].original == "AB[end]"


def test_a_container_edit_carries_the_children_and_the_row_name(window, tmp_path):
    """The file list is the entry's identity: the blocks and bookmarks under it
    are keyed by it, and a row still named after its first file follows."""
    from mapchar.ui.undo_commands import ContainerCommand

    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    mark = Entry(
        EntryKind.BOOKMARK, "bm", file_entry.path, parent=file_entry, bookmark_offset=2
    )
    window._push_add(mark)
    first = tmp_path / "chip1.bin"
    first.write_bytes(DATA)
    second = tmp_path / "chip2.bin"
    second.write_bytes(b"\x00" * 8)
    window.undo_stack.clear()
    before = (file_entry.container_id, file_entry.paths)
    after = ("raw", (str(first), str(second)))
    window.undo_stack.push(ContainerCommand(window, file_entry, before, after))
    for e in (file_entry, block, mark):
        assert e.path == str(first) and e.extra_paths == (str(second),)
    assert file_entry.name == "chip1.bin"  # it was named after its file
    window.undo_stack.undo()
    assert file_entry.name == "rom.bin"
    for e in (file_entry, block, mark):
        assert e.path == str(tmp_path / "rom.bin") and e.extra_paths == ()


def test_a_container_edit_will_not_point_a_row_at_a_file_already_open(
    window, tmp_path, monkeypatch
):
    """One file, one entry: two rows on one path leaves a block unable to say
    which of them is its parent."""
    from mapchar.ui.dialogs import ContainerDialog

    first = open_rom_and_table(window, tmp_path, DATA, rom_name="one.bin")
    other = tmp_path / "two.bin"
    other.write_bytes(DATA)
    second = window.open_rom(str(other))
    said: list[str] = []
    monkeypatch.setattr(window, "_error", said.append)
    monkeypatch.setattr(
        ContainerDialog, "exec", lambda self: ContainerDialog.DialogCode.Accepted
    )
    monkeypatch.setattr(ContainerDialog, "paths", lambda self: (str(other),))
    window.undo_stack.clear()
    window._edit_container(first)
    assert said and "already open" in said[0]
    assert first.path == str(tmp_path / "one.bin")
    assert second.path == str(other)
    assert window.undo_stack.count() == 0
