"""Entries: the Files panel, the entry clipboard, dialogs, drops, the quit gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication, QMessageBox

from helpers import pointer_rom
from mapchar.core.block import (
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Status,
)
from mapchar.project.projectfile import entries_from_payload, entries_payload
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.files_panel import STATUS_COL, sorted_entries
from mapchar.ui.main_window import MainWindow
from mapchar.ui.main_window.codecs_bar import POINTER
from mapchar.ui.widgets import select_data
from window_helpers import TABLE, add_block, make_window, open_rom_and_table

DATA = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20


@pytest.fixture
def window(qtbot, monkeypatch):
    """A live window whose every modal answers Yes / Discard without showing."""
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.warning", lambda *a, **k: 0
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr("mapchar.ui.main_window.window.TextDialog.exec", lambda self: 0)
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def item_for(window, entry):
    return window.files_panel._items[id(entry)]


# -- translations survive what re-reads a block --------------------------------


def test_removing_a_table_keeps_every_translation(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "ZZ[end]")
    assert block.doc.strings[0].translation == "ZZ[end]"

    table_entry = window.workspace.of_kind(EntryKind.TABLE)[0]
    window._remove_entries([table_entry])
    assert table_entry not in window.workspace.entries
    # The records are still there, and stashed where a document drop cannot
    # reach them, rather than replaced by an empty list.
    assert [r.translation for r in block.doc.strings] == ["ZZ[end]", None]
    assert block.pending_strings[0].translation == "ZZ[end]"
    # And the row says why it cannot be re-read.
    window.files_panel.refresh_labels()
    item = item_for(window, block)
    assert item.text(STATUS_COL) == "!"
    assert "not loaded" in item.toolTip(0)


def test_a_failed_reload_does_not_clear_the_strings(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 1, "QQ[end]")
    # The table set goes away without the entry going away: an extraction with
    # no tables must keep what is there.
    window._extract_current(block, block.doc, None)
    assert [r.translation for r in block.doc.strings] == [None, "QQ[end]"]


def test_edit_block_carries_translations_and_is_one_undo_step(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "BA[end]")
    before = (block.name, block.config, block.compression_id, block.spare_room)
    window.undo_stack.clear()

    wider = block.config.__class__(
        **{**block.config.__dict__, "source": RangeSource(0, 6)}
    )
    window.apply_block_config(block, "renamed", wider, None, "keep")
    assert block.name == "renamed"
    assert block.doc.strings[0].translation == "BA[end]"

    # As a command it undoes in one step, name, config and all.
    window.undo_stack.clear()
    from mapchar.ui.undo_commands import BlockEditCommand

    after = ("second", wider, None, "fill")
    window.undo_stack.push(BlockEditCommand(window, block, before, after))
    assert block.name == "second" and block.spare_room == "fill"
    window.undo_stack.undo()
    assert block.name == before[0] and block.spare_room == before[3]
    assert block.doc.strings[0].translation == "BA[end]"


# -- the quit gate -------------------------------------------------------------


def test_the_project_is_asked_about_before_the_file_edits(
    window, tmp_path, monkeypatch
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "ZZ[end]")
    assert block.dirty
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
    window._set_translation(block, 0, "ZZ[end]")
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
    assert window.table_pick.currentData() == "main"


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


def test_jump_to_source_of_a_compressed_block_uses_its_slot(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(0x100))
    block = add_block(window, file_entry, "packed", RangeSource(0, 8))
    block.compression_id = "rle1"
    block.slice_offset = 0x50
    assert window._block_file_offset(block) == 0x50
    window._jump_to_source(block)
    # The file shows the packed structure at that address, so the scheme that
    # reads it is armed in the Compression preview.
    assert window._entry is file_entry and window._offset == 0x50
    assert window.compression_pick.currentData() == "rle1"


# -- drag and drop -------------------------------------------------------------


def drop(window, *paths, ctrl=False):
    from PySide6.QtCore import Qt

    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(p) for p in paths])
    event = QDropEvent(
        QPoint(5, 5),
        Qt.DropAction.CopyAction,
        mime,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.ControlModifier if ctrl else Qt.KeyboardModifier.NoModifier,
    )
    window.dropEvent(event)


def test_a_drop_opens_each_file_as_its_name_says(window, tmp_path):
    rom = tmp_path / "game.bin"
    rom.write_bytes(DATA)
    tbl = tmp_path / "main.tbl"
    tbl.write_text(TABLE)
    drop(window, str(rom), str(tbl))
    kinds = {e.kind for e in window.workspace.entries}
    assert kinds == {EntryKind.FILE, EntryKind.TABLE}
    assert window._drop_kind("a.mapchar") == "rom"  # the project claims the drop itself
    assert window._drop_kind("x.PNG") == "font"
    assert window._drop_kind("x.po") == "po"
    assert window._drop_kind("x.tsv") == "delimited"
    assert window._drop_kind("x.smc") == "rom"


def test_a_dropped_project_claims_the_whole_drop(window, tmp_path, monkeypatch):
    opened: list[str] = []
    monkeypatch.setattr(window, "open_project", lambda p: opened.append(p) or True)
    rom = tmp_path / "game.bin"
    rom.write_bytes(DATA)
    project = tmp_path / "p.mapchar"
    project.write_text("{}")
    drop(window, str(rom), str(project))
    assert opened == [str(project)]
    assert not window.workspace.entries


# -- the entry clipboard -------------------------------------------------------


def test_the_payload_carries_absolute_paths_and_translations(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "ZZ[end]")
    text = entries_payload([file_entry, block])
    assert str(tmp_path) in text
    copied = entries_from_payload(text)
    assert [e.name for e in copied] == ["rom.bin", "b"]
    assert copied[1].parent is copied[0]
    assert copied[1].pending_strings[0].translation == "ZZ[end]"
    assert entries_from_payload("not json at all") == []
    assert entries_from_payload('{"other": 1}') == []


def test_copy_and_paste_a_block_into_the_same_file(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._copy_entries([block])
    window._paste_entries(file_entry)
    blocks = window.workspace.of_kind(EntryKind.BLOCK)
    assert [b.name for b in blocks] == ["b", "b (2)"]
    assert blocks[1].parent is file_entry
    # Undone in one step, as one gesture.
    window.undo_stack.undo()
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == 1


def test_pasting_a_file_that_is_open_selects_it_instead(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    window._copy_entries([file_entry])
    before = len(window.workspace.entries)
    window._paste_entries(None)
    assert len(window.workspace.entries) == before
    assert window.workspace.current is file_entry


def test_cut_takes_the_row_out_without_asking(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._cut_entries([block])
    assert block not in window.workspace.entries
    assert QApplication.clipboard().text()
    window._paste_entries(file_entry)
    assert [b.name for b in window.workspace.of_kind(EntryKind.BLOCK)] == ["b"]


def test_duplicate_only_applies_to_children(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    clip = QApplication.clipboard().text()
    window._duplicate_entries([block])
    assert [b.name for b in window.workspace.of_kind(EntryKind.BLOCK)] == ["b", "b (2)"]
    assert QApplication.clipboard().text() == clip  # never touches the clipboard
    window._duplicate_entries([file_entry])
    assert len(window.workspace.files()) == 1


def test_entries_paste_into_a_second_window(qtbot, monkeypatch, tmp_path):
    """The clipboard carries absolute paths, so a copy crosses windows.

    Two live windows share one system clipboard, which is the whole mechanism:
    nothing is handed over in memory, so what pastes is what a payload can say.
    """
    first = make_window(qtbot, monkeypatch)
    file_entry = open_rom_and_table(first, tmp_path, DATA)
    block = add_block(first, file_entry, "b", RangeSource(0, 6))
    first._set_translation(block, 0, "ZZ[end]")
    first._copy_entries([file_entry])  # the block comes with it

    second = make_window(qtbot, monkeypatch)
    second._paste_entries(None)
    pasted = second.workspace.files()
    assert [e.name for e in pasted] == ["rom.bin"]
    assert pasted[0].path == file_entry.path  # absolute: it resolves over there
    blocks = second.workspace.of_kind(EntryKind.BLOCK)
    assert [b.name for b in blocks] == ["b"]
    assert blocks[0].parent is pasted[0]
    assert blocks[0].pending_strings[0].translation == "ZZ[end]"


# -- the Files panel -----------------------------------------------------------


def test_a_rename_is_one_undo_step(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    window.undo_stack.clear()
    window._commit_rename(file_entry, "the ROM")
    assert file_entry.name == "the ROM"
    window.undo_stack.undo()
    assert file_entry.name == "rom.bin"


def test_the_inline_editor_commits_through_the_panel(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    panel = window.files_panel
    panel.begin_rename(file_entry)
    item = item_for(window, file_entry)
    assert item.text(0) == "rom.bin"  # the bare name, not the marker strip
    item.setText(0, "renamed")
    assert file_entry.name == "renamed"


def test_reorder_and_move_are_undoable(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    first = add_block(window, file_entry, "aaa", RangeSource(0, 3))
    second = add_block(window, file_entry, "bbb", RangeSource(3, 6))
    assert window.files_panel.siblings(first) == [first, second]
    window.undo_stack.clear()
    window._reorder_entry(second, first)
    assert window.files_panel.siblings(first) == [second, first]
    window.undo_stack.undo()
    assert window.files_panel.siblings(first) == [first, second]
    window._move_entries([first], 1)
    assert window.files_panel.siblings(first) == [second, first]


def test_sort_by_name_and_offset(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    late = add_block(window, file_entry, "zebra", RangeSource(3, 6))
    early = add_block(window, file_entry, "apple", RangeSource(0, 3))
    assert sorted_entries([late, early], "Name") == [early, late]
    assert sorted_entries([late, early], "Offset") == [early, late]
    window._sort_entries(early, "Name")
    assert window.files_panel.siblings(early) == [early, late]
    window._sort_entries(early, "Offset")
    assert window.files_panel.siblings(early) == [early, late]


def test_a_block_row_summarises_its_strings(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "ZZ[end]")
    block.doc.strings[1].status = Status.REVIEW
    window.files_panel.refresh_labels()
    label = item_for(window, block).text(0)
    assert "(2, 1 edited, 1 review)" in label


def test_a_missing_file_marks_the_row_and_the_tooltip(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    file_entry.missing = True
    window.files_panel.refresh_labels()
    item = item_for(window, file_entry)
    assert item.text(STATUS_COL) == "?"
    assert "not where the project says" in item.toolTip(0)


def test_a_missing_plugin_marks_the_row_and_names_itself(window, tmp_path):
    """The load's notice reaches the row it is about, not just the context."""
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    file_entry.compression_id = "not_installed"
    file_entry.doc = None
    doc = window._load_document(file_entry)
    assert doc.missing_plugins == ["not_installed"] and not doc.writable
    window.files_panel.refresh_labels()
    item = item_for(window, file_entry)
    assert item.text(STATUS_COL) == "!"
    assert item.background(0).color().alpha()  # washed amber
    tip = item.toolTip(0)
    assert "Missing plugin: not_installed" in tip
    assert "no write-back: not_installed" in tip


def test_ctrl_f_focuses_the_filter_when_the_panel_has_it(window, tmp_path, monkeypatch):
    open_rom_and_table(window, tmp_path, DATA)
    focused: list[bool] = []
    monkeypatch.setattr(window.files_panel, "has_focus", lambda: True)
    monkeypatch.setattr(
        window.files_panel, "focus_filter", lambda: focused.append(True)
    )
    window._find_bytes()
    assert focused == [True]
    # Without the panel's focus the same key is the byte search, which asks.
    monkeypatch.setattr(window.files_panel, "has_focus", lambda: False)
    asked: list[bool] = []
    monkeypatch.setattr(
        "mapchar.ui.main_window.entries.QInputDialog.getText",
        lambda *a, **k: (asked.append(True), ("", False))[1],
    )
    window._find_bytes()
    assert asked == [True]


def test_a_multi_selection_leaves_only_remove_and_the_moves_live(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    first = add_block(window, file_entry, "aaa", RangeSource(0, 3))
    second = add_block(window, file_entry, "bbb", RangeSource(3, 6))
    for entry in (first, second):
        item_for(window, entry).setSelected(True)
    menu = window._build_files_menu(first)
    live = {
        a.text().replace("&", "") for a in menu.actions() if a.text() and a.isEnabled()
    }
    assert live == {"Remove", "Move Up", "Move Down"}


# -- dialogs -------------------------------------------------------------------


def test_the_reading_bar_carries_compression_and_spare_room(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window.undo_stack.clear()
    select_data(window.compression_pick, "rle1")
    assert block.compression_id == "rle1"
    assert window.reading_bar.spare_room.isEnabled()
    window.reading_bar.spare_room.setCurrentIndex(1)
    assert block.spare_room == "keep"
    # Write mode and the fill byte round-trip through the same bar.
    assert window.reading_bar.config(block.config, "main").fill == block.config.fill
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert block.compression_id is None
    assert not window.reading_bar.spare_room.isEnabled()


def test_a_new_reading_turned_to_pointers_starts_on_the_suggested_mapping(
    window, tmp_path
):
    rom = tmp_path / "game.smc"
    rom.write_bytes(bytes(0x8000) + b"\x00" * 0x8000)
    window.open_rom(str(rom))
    suggested = window._suggested_mapping()
    assert suggested
    select_data(window.table_pick, POINTER)
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
    assert bookmark.compression_id == window.compression_pick.currentData()
    window._go_to(0)
    window._jump_to_bookmark(bookmark)
    assert window._offset == 3 and window._entry is file_entry


def test_the_decompressed_view_has_a_stop_that_cancels(window, tmp_path):
    view = window.decompress_window
    assert not view.stop.isEnabled()
    view.set_scanning(True)
    assert view.stop.isEnabled() and not view.scan.isEnabled()
    assert not window.files_dock.isEnabled() or True  # frozen by _set_scan_ui
    window._scan_stop = False
    view.stop.click()
    assert window._scan_stop
    view.set_scanning(False)
    assert view.scan.isEnabled()
    window._set_scan_ui(True)
    assert not window.menuBar().isEnabled()
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
    window.compression_pick.setCurrentIndex(
        window.compression_pick.findData("gba_lz77")
    )
    window._activate_entry(file_entry)
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


def test_a_slice_length_round_trips_and_a_falsy_one_means_unknown(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "packed", RangeSource(0, 6))
    block.compression_id = "rle1"
    block.slice_offset = 0x10

    def reread() -> Entry:
        return entries_from_payload(entries_payload([file_entry, block]))[1]

    block.slice_length = 0x20
    assert (reread().slice_offset, reread().slice_length) == (0x10, 0x20)
    # Both sides read a falsy length as "nobody measured it", so neither a
    # missing key nor a stored 0 comes back as a slot with no room in it.
    for length in (0, None):
        block.slice_length = length
        assert reread().slice_length is None


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
    window._set_translation(block, 0, "ZZ[end]")
    window._on_table_pick()  # re-reads the region under the picked table
    assert block.doc is not None
    assert block.doc.strings[0].translation == "ZZ[end]"


def test_changing_the_container_chain_keeps_a_blocks_translations(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._set_translation(block, 0, "ZZ[end]")
    window._on_chain_changed()  # drops the file's document and every child's
    assert block.doc is not None
    assert block.doc.strings[0].translation == "ZZ[end]"


def test_find_pointers_keeps_the_translations_it_re_reads(
    window, tmp_path, monkeypatch
):
    from mapchar.ui.dialogs import DiscoveryDialog, PointerSearchDialog

    rom = pointer_rom((0x10, 0x13), "41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, file_entry, "b", RangeSource(0x10, 0x16))
    window._set_translation(block, 0, "ZZ[end]")
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
    assert block.doc.strings[0].translation == "ZZ[end]"


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
