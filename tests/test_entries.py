"""Entries: the Files panel, the entry clipboard, dialogs, drops, the quit gate."""

from __future__ import annotations

from pathlib import Path

import pytest
from PySide6.QtCore import QMimeData, QPoint, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QAbstractItemView, QApplication, QMessageBox

from helpers import pointer_rom
from mapchar.core.block import (
    NestedPointerSource,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    Status,
    StringRecord,
)
from mapchar.project.projectfile import entries_from_payload, entries_payload
from mapchar.project.tables import same_table
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.entry_text import sorted_entries
from mapchar.ui.files_panel import STATUS_COL, FilesPanel
from mapchar.ui.main_window import MainWindow
from window_helpers import (
    TABLE,
    add_block,
    arm_scheme,
    make_window,
    open_rom_and_table,
)

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
    block.slice_offset = 0x50
    assert window._block_file_offset(block) == 0x50
    window._jump_to_source(block)
    # The file shows the packed structure at that address, so the scheme that
    # reads it is armed in the Compression preview.
    assert window._entry is file_entry and window._offset == 0x50
    assert window._preview_scheme == "rle1"


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
    window._set_translation(block, 0, "BB[end]")
    text = entries_payload([file_entry, block])
    assert str(tmp_path) in text
    copied = entries_from_payload(text)
    assert [e.name for e in copied] == ["rom.bin", "b"]
    assert copied[1].parent is copied[0]
    assert copied[1].pending_strings[0].original == "AB[end]"
    assert copied[1].pending_strings[0].status is Status.EDITED
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


def test_duplicate_applies_to_children_but_not_to_a_rom(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    clip = QApplication.clipboard().text()
    window._duplicate_entries([block])
    assert [b.name for b in window.workspace.of_kind(EntryKind.BLOCK)] == ["b", "b (2)"]
    assert QApplication.clipboard().text() == clip  # never touches the clipboard
    window._duplicate_entries([file_entry])
    assert len(window.workspace.files()) == 1


def test_duplicating_a_table_copies_it_with_no_file_of_its_own(window, tmp_path):
    """The way to a new table that starts from an existing one: the copy holds
    the same entries under a free id, and the project carries it until a
    Save As File gives it a file."""
    open_rom_and_table(window, tmp_path, DATA)
    table = window.workspace.of_kind(EntryKind.TABLE)[0]
    window._duplicate_entries([table])
    tables = window.workspace.of_kind(EntryKind.TABLE)
    assert [e.name for e in tables] == ["main.tbl", "main_2.tbl"]
    copy = tables[1]
    assert copy.path is None
    assert copy.table.id == "main_2"
    assert copy.table.own_entries() == table.table.own_entries()
    # Every entry is overlay, so the project carries the whole table.
    assert copy.table_overlay and copy.dirty
    # An edit to the copy is the copy's own.
    assert copy.table is not table.table
    # Undone in one step, and nothing was written.
    window.undo_stack.undo()
    assert len(window.workspace.of_kind(EntryKind.TABLE)) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["main.tbl", "rom.bin"]


def test_a_table_pasted_into_a_second_window_is_read_from_its_file(
    qtbot, monkeypatch, tmp_path
):
    """The payload carries a table's path and the edits over it, never the
    file's own entries — so the paste reads the file, the way opening it does."""
    first = make_window(qtbot, monkeypatch)
    open_rom_and_table(first, tmp_path, DATA)
    table = first.workspace.of_kind(EntryKind.TABLE)[0]
    first._copy_entries([table])

    second = make_window(qtbot, monkeypatch)
    second._paste_entries(None)
    pasted = second.workspace.of_kind(EntryKind.TABLE)
    assert [e.name for e in pasted] == ["main.tbl"]
    assert pasted[0].table is not None
    assert same_table(pasted[0].table, table.table)
    assert "main" in second.workspace.loaded_tables()


def test_entries_paste_into_a_second_window(qtbot, monkeypatch, tmp_path):
    """The clipboard carries absolute paths, so a copy crosses windows.

    Two live windows share one system clipboard, which is the whole mechanism:
    nothing is handed over in memory, so what pastes is what a payload can say.
    """
    first = make_window(qtbot, monkeypatch)
    file_entry = open_rom_and_table(first, tmp_path, DATA)
    block = add_block(first, file_entry, "b", RangeSource(0, 6))
    first._set_translation(block, 0, "BB[end]")
    first._copy_entries([file_entry])  # the block comes with it

    second = make_window(qtbot, monkeypatch)
    second._paste_entries(None)
    pasted = second.workspace.files()
    assert [e.name for e in pasted] == ["rom.bin"]
    assert pasted[0].path == file_entry.path  # absolute: it resolves over there
    blocks = second.workspace.of_kind(EntryKind.BLOCK)
    assert [b.name for b in blocks] == ["b"]
    assert blocks[0].parent is pasted[0]
    assert blocks[0].pending_strings[0].original == "AB[end]"


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
    window._set_translation(block, 0, "BB[end]")
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
    # Without the panel's focus the same key is the byte search: the Find bar.
    monkeypatch.setattr(window.files_panel, "has_focus", lambda: False)
    window._find_bytes()
    assert window.focusWidget() is window.find_row.field


def test_a_multi_selection_leaves_only_remove_the_moves_and_new_folder_live(
    window, tmp_path
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    first = add_block(window, file_entry, "aaa", RangeSource(0, 3))
    second = add_block(window, file_entry, "bbb", RangeSource(3, 6))
    for entry in (first, second):
        item_for(window, entry).setSelected(True)
    menu = window._build_files_menu(first)
    live = {
        a.text().replace("&", "") for a in menu.actions() if a.text() and a.isEnabled()
    }
    assert live == {"Remove", "Move Up", "Move Down", "New Folder"}


def _menu_state(menu) -> dict[str, bool]:
    return {
        a.text().replace("&", ""): a.isEnabled() for a in menu.actions() if a.text()
    }


def test_a_string_rows_menu_greys_what_would_edit_its_block(window, tmp_path):
    """A string row's menu is its block's, but the rows that edit the row name
    the block rather than the string clicked, so they go dead."""
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    state = _menu_state(window._build_files_menu(block, 0))
    dead = {"Rename…", "Cut", "Copy", "Duplicate", "Remove", "Move Up", "Move Down"}
    dead |= {"Sort By", "New Folder"}
    assert {row for row, live in state.items() if not live} >= dead
    assert state["Write"] and state["Export"]
    # The block's own row keeps every one of them.
    assert all(_menu_state(window._build_files_menu(block))[row] for row in dead)


def test_write_and_duplicate_are_dead_where_they_cannot_act(window, tmp_path):
    """A bookmark has no bytes and a table is written with Save As File…; a ROM
    is its path, so it has no second row to make. A table does: the copy has no
    file of its own."""
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    window._go_to(3)
    window._new_bookmark()
    bookmark = window.workspace.of_kind(EntryKind.BOOKMARK)[0]
    table = window.workspace.of_kind(EntryKind.TABLE)[0]
    for entry, dead in (
        (bookmark, {"Write"}),
        (table, {"Write"}),
        (file_entry, {"Duplicate"}),
    ):
        state = _menu_state(window._build_files_menu(entry))
        assert {row for row in dead if not state[row]} == dead, entry.name
    assert _menu_state(window._build_files_menu(table))["Duplicate"]
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    state = _menu_state(window._build_files_menu(block))
    assert state["Write"] and state["Duplicate"]


# -- folders -------------------------------------------------------------------


def _contents(window, container) -> list[str]:
    return [e.name for e in window.workspace.contents(container)]


def test_new_folder_gathers_a_selection_in_its_place_as_one_step(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    a = add_block(window, file_entry, "a", RangeSource(0, 3))
    b = add_block(window, file_entry, "b", RangeSource(3, 6))
    add_block(window, file_entry, "c", RangeSource(0, 6))
    window.undo_stack.clear()
    folder = window._new_folder(a, [a, b])
    assert folder.kind is EntryKind.FOLDER and folder.name == "New Folder"
    assert _contents(window, file_entry) == ["New Folder", "c"]
    assert _contents(window, folder) == ["a", "b"]
    assert a.parent is file_entry  # a folder never changes a row's file
    item = item_for(window, folder)
    assert item.parent() is item_for(window, file_entry)
    assert [item.child(i).text(0).split()[0] for i in range(2)] == ["a", "b"]
    assert window.undo_stack.count() == 1
    window.undo_stack.undo()
    assert folder not in window.workspace.entries
    assert _contents(window, file_entry) == ["a", "b", "c"]
    window.undo_stack.redo()
    assert _contents(window, folder) == ["a", "b"]


def test_new_folder_on_a_file_or_a_folder_makes_an_empty_one_inside(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    add_block(window, file_entry, "a", RangeSource(0, 3))
    outer = window._new_folder(file_entry, [file_entry])
    inner = window._new_folder(outer, [outer])
    assert _contents(window, file_entry) == ["a", "New Folder"]
    assert inner.folder is outer and inner.name == "New Folder (2)"
    window.files_panel._on_editor_closed(None, None)  # the name left as it came
    assert item_for(window, outer).text(0) == "New Folder  (1 item)"
    assert item_for(window, inner).text(0) == "New Folder (2)  (0 items)"


def test_a_folder_renames_inline_and_its_name_numbers_nothing(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    add_block(window, file_entry, "Names", RangeSource(0, 3))
    folder = window._new_folder(file_entry, [file_entry])
    item = item_for(window, folder)
    assert item.text(0) == "New Folder"  # New Folder opened it for its name
    item.setText(0, "Names")
    assert folder.name == "Names"
    add_block(window, file_entry, "Menus", RangeSource(3, 6))
    assert window._free_name("Names") == "Names (2)"  # the block's, not the folder's
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert folder.name == "New Folder"


def test_a_folder_row_counts_its_rows_and_adds_up_its_blocks(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    a = add_block(window, file_entry, "a", RangeSource(0, 3))
    b = add_block(window, file_entry, "b", RangeSource(3, 6))
    outer = window._new_folder(a, [a, b])
    inner = window._new_folder(b, [b])
    window._set_translation(a, 0, "BB[end]")
    b.doc.strings[0].status = Status.REVIEW
    window.files_panel.refresh_labels()
    assert item_for(window, outer).text(0) == (
        "New Folder  (2 items, 1 edited, 1 review)"
    )
    assert item_for(window, inner).text(0) == "New Folder (2)  (1 item, 1 review)"


def test_rows_dropped_on_and_between_rows_change_folder_as_one_step(window, tmp_path):
    drop = QAbstractItemView.DropIndicatorPosition
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    a = add_block(window, file_entry, "a", RangeSource(0, 3))
    b = add_block(window, file_entry, "b", RangeSource(3, 6))
    folder = window._new_folder(file_entry, [file_entry])
    inner = window._new_folder(folder, [folder])
    tree = window.files_panel.tree

    def dropping(rows, target, position):
        tree._dragged = [item_for(window, r) for r in rows]
        try:
            landing = tree.landing(item_for(window, target), position)
            if landing is not None:
                tree.drop_rows(tree._dragged, *landing)
            return landing
        finally:
            tree._dragged = []

    window.undo_stack.clear()
    assert dropping([a, b], folder, drop.OnItem) is not None
    assert _contents(window, folder) == ["New Folder (2)", "a", "b"]
    assert window.undo_stack.count() == 1
    # Never into itself, never into a row that is not a file or a folder, and
    # a file only between the rows of its group.
    assert dropping([folder], inner, drop.OnItem) is None
    assert dropping([folder], folder, drop.OnItem) is None
    assert dropping([a], b, drop.OnItem) is None
    assert dropping([file_entry], folder, drop.OnItem) is None
    # Out again, in front of the folder.
    assert dropping([b], folder, drop.AboveItem) is not None
    assert _contents(window, file_entry) == ["b", "New Folder"]
    assert b.folder is None
    window.undo_stack.undo()
    assert b.folder is folder and _contents(window, folder)[-1] == "b"
    window.undo_stack.undo()
    assert _contents(window, file_entry) == ["a", "b", "New Folder"]


def test_removing_a_folder_removes_what_it_holds_and_undo_puts_it_back(
    window, tmp_path, monkeypatch
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    a = add_block(window, file_entry, "a", RangeSource(0, 3))
    b = add_block(window, file_entry, "b", RangeSource(3, 6))
    add_block(window, file_entry, "c", RangeSource(0, 6))
    folder = window._new_folder(a, [a, b])
    assert window._write_project(str(tmp_path / "p.mapchar"))
    order = list(window.workspace.entries)
    asked: list[str] = []
    monkeypatch.setattr(window, "_ask", lambda title, msg: asked.append(msg) or True)
    window._remove_entries([folder, a])
    assert asked[0] == "Remove New Folder, a?\nAlso removes: b."
    assert not {folder, a, b} & set(window.workspace.entries)
    assert window._project_dirty()
    window.undo_stack.undo()
    assert window.workspace.entries == order and b.folder is folder
    assert not window._project_dirty()


def test_a_folder_cuts_copies_pastes_and_duplicates_with_what_it_holds(
    window, tmp_path
):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    a = add_block(window, file_entry, "a", RangeSource(0, 3))
    b = add_block(window, file_entry, "b", RangeSource(3, 6))
    folder = window._new_folder(a, [a, b])
    window._commit_rename(folder, "Group")
    window._copy_entries([folder])
    window._paste_entries(file_entry)
    copy = window.workspace.of_kind(EntryKind.FOLDER)[1]
    assert copy.name == "Group" and copy.folder is None
    assert _contents(window, copy) == ["a (2)", "b (2)"]
    window.undo_stack.undo()
    assert window.workspace.of_kind(EntryKind.FOLDER) == [folder]
    # A loose row pasted onto a row inside a folder lands in that folder.
    window._copy_entries([a])
    window._paste_entries(b)
    assert _contents(window, folder) == ["a", "b", "a (2)"]
    window.undo_stack.undo()
    # A duplicate stays in its folder; a duplicated folder takes its rows.
    window._duplicate_entries([b])
    assert _contents(window, folder) == ["a", "b", "b (2)"]
    window.undo_stack.undo()
    window._duplicate_entries([folder, a])
    twin = window.workspace.of_kind(EntryKind.FOLDER)[1]
    assert _contents(window, twin) == ["a (2)", "b (2)"]
    assert _contents(window, file_entry) == ["Group", "Group"]
    window._cut_entries([twin])
    assert twin not in window.workspace.entries
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == 2


def test_the_filter_opens_the_folders_a_match_is_in_then_closes_them(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    alpha = add_block(window, file_entry, "alpha", RangeSource(0, 3))
    beta = add_block(window, file_entry, "beta", RangeSource(3, 6))
    folder = window._new_folder(alpha, [alpha])
    window._commit_rename(folder, "Group")
    panel = window.files_panel
    item_for(window, folder).setExpanded(False)
    panel.rebuild()
    assert not item_for(window, folder).isExpanded()  # remembered over a rebuild
    panel.filter.setText("alpha")
    assert item_for(window, folder).isExpanded()
    assert not item_for(window, alpha).isHidden()
    assert item_for(window, beta).isHidden()
    panel.filter.setText("")
    assert not item_for(window, folder).isExpanded()
    assert not item_for(window, beta).isHidden()


def test_sort_by_orders_the_rows_of_one_folder(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    late = add_block(window, file_entry, "zebra", RangeSource(3, 6))
    early = add_block(window, file_entry, "apple", RangeSource(0, 3))
    loose = add_block(window, file_entry, "loose", RangeSource(3, 6))
    folder = window._new_folder(late, [late, early])
    window._sort_entries(late, "Name")
    assert window.files_panel.siblings(late) == [early, late]
    assert window.files_panel.siblings(loose) == [folder, loose]
    window._sort_entries(loose, "Name")  # folders first, as a file manager lists
    assert window.files_panel.siblings(loose) == [folder, loose]
    rows_of = window.workspace.contents
    assert sorted_entries([loose, folder], "Offset", rows_of) == [folder, loose]


def test_selecting_a_folder_leaves_the_view_where_it_was(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "a", RangeSource(0, 3))
    folder = window._new_folder(file_entry, [file_entry])
    window._activate_entry(block)
    window.files_panel.entry_activated.emit(folder)
    assert window.workspace.current is block
    menu = window._build_files_menu(folder)
    rows = {a.text().replace("&", "") for a in menu.actions() if a.text()}
    assert {"New Folder", "Rename…", "Remove"} <= rows
    assert "Write" not in rows


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


# -- a nested block opens to its inner tables ---------------------------------

NESTED_ROM = bytes.fromhex(
    "08 00 0C 00  14 00 18 00"  # two records: (table $8, base $C), ($14, $18)
    "01 00 04 00  FF 41 42 00"  # inner table 0 reaches AB[end] at $D
    "42 00 FF FF  01 00 03 00"  # B[end] at $10; inner table 1 at $14
    "FF 41 00 42  00 FF FF FF"  # A[end] at $19 and B[end] at $1B, from base $18
)
"""Two nested records, two strings each, so the block has two groups."""

NESTED = NestedPointerSource(0, 8, 2, 4, null=0, inner_size=2, inner_null=0)


def nested_block(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, NESTED_ROM)
    return add_block(window, entry, "script", NESTED)


def test_a_nested_block_opens_to_a_row_per_inner_table(window, tmp_path):
    block = nested_block(window, tmp_path)
    assert [s.current_text() for s in block.doc.strings] == [
        "AB[end]",
        "B[end]",
        "A[end]",
        "B[end]",
    ]
    item = item_for(window, block)
    item.setExpanded(True)  # builds the rows
    # One row per inner table, named for the table rather than the base, each
    # closed over its own strings.
    assert [item.child(i).text(0) for i in range(item.childCount())] == [
        "8  2 strings",
        "14  2 strings",
    ]
    assert "inner pointer table at 8" in item.child(0).toolTip(0)
    assert "its pointers count from C" in item.child(0).toolTip(0)
    panel = window.files_panel
    assert panel.group_of(item.child(0)) == (block, 0xC)
    assert panel.group_of(item.child(1)) == (block, 0x18)
    # Closed: a block of hundreds of groups costs only the one being read.
    assert [item.child(i).childCount() for i in range(2)] == [1, 1]
    assert panel._is_stub(item.child(0).child(0))


def test_opening_a_group_row_builds_that_group_s_strings_alone(window, tmp_path):
    block = nested_block(window, tmp_path)
    item = item_for(window, block)
    item.setExpanded(True)
    item.child(1).setExpanded(True)
    panel = window.files_panel
    assert (id(block), 0x18) in panel._expanded_groups
    # The rows are rebuilt, so the group row is a new item.
    group = item.child(1)
    assert [group.child(i).text(0) for i in range(group.childCount())] == [
        "2  A[end]",
        "3  B[end]",
    ]
    assert [panel.string_of(group.child(i)) for i in range(2)] == [
        (block, 2),
        (block, 3),
    ]
    # The other group is untouched.
    assert panel._is_stub(item.child(0).child(0))
    item.child(1).setExpanded(False)
    assert (id(block), 0x18) not in panel._expanded_groups
    assert panel._is_stub(item.child(1).child(0))


def test_clicking_a_group_takes_the_view_to_its_inner_table(window, tmp_path):
    block = nested_block(window, tmp_path)
    item = item_for(window, block)
    item.setExpanded(True)
    window.files_panel._activate(item.child(1))
    # The block stays current with all of its strings: a group is where to
    # look, not a reading of its own, so nothing is confined.
    assert window._entry is block
    assert len(block.doc.strings) == 4
    assert window._offset == 0x14
    assert window._bounds is None
    assert window.strings.table.rowCount() == 4
    assert window.files_panel.tree.currentItem() is item.child(1)


def test_showing_a_string_of_a_nested_block_opens_the_group_it_is_under(
    window, tmp_path
):
    block = nested_block(window, tmp_path)
    window._show_string(block, 3)
    panel = window.files_panel
    assert (id(block), 0x18) in panel._expanded_groups
    item = item_for(window, block)
    assert panel.string_of(panel.tree.currentItem()) == (block, 3)
    assert panel._string_child(item, 3) is panel.tree.currentItem()


def test_opening_a_group_row_with_a_key_stays_on_that_row(window, tmp_path):
    """The row the key is on must survive being opened: taking it out of the
    tree leaves Qt's current row on another entry, which reads as a jump."""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    group = item.child(1)
    panel._select_item(group)
    visited: list[object] = []
    panel.entry_activated.connect(visited.append)

    def press(key):
        panel.tree.keyPressEvent(
            QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        )

    press(Qt.Key.Key_Right)
    assert panel.tree.currentItem() is group
    assert group.isExpanded() and group.childCount() == 2
    press(Qt.Key.Key_Left)
    assert panel.tree.currentItem() is group
    assert not group.isExpanded()
    # Nothing else in the panel was ever shown.
    assert visited == []
    assert window._entry is block


def test_the_filter_opens_a_closed_group_holding_a_match(window, tmp_path):
    """A group's rows are built only while it is open, so the filter matches
    its strings by what their rows would say, and builds and opens the one
    holding a match — and only that one."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    first, second = item.child(0), item.child(1)
    panel.filter.setText("AB[end]")
    # The block keeps the match visible, as an unnested block's would.
    assert not item.isHidden()
    assert first.isExpanded() and not first.isHidden()
    assert [first.child(i).text(0) for i in range(first.childCount())] == [
        "0  AB[end]",
        "1  B[end]",
    ]
    assert not first.child(0).isHidden()
    assert first.child(1).isHidden()
    # The group with nothing matching is neither built nor opened.
    assert second.isHidden()
    assert panel._is_stub(second.child(0))
    # Clearing the filter leaves the block as lazy as the filter found it.
    panel.filter.setText("")
    assert not first.isExpanded()
    assert panel._is_stub(first.child(0))
    assert (id(block), 0xC) not in panel._expanded_groups
    assert panel._filter_groups == set()


def test_the_filter_leaves_a_group_the_view_is_in_open(window, tmp_path):
    """A string shown from inside a group the filter opened keeps its row:
    the group is the view's now, not the filter's to close."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    panel.filter.setText("A[end]")
    window._show_string(block, 2)
    panel.filter.setText("")
    assert (id(block), 0x18) in panel._expanded_groups
    assert panel.string_of(panel.tree.currentItem()) == (block, 2)


def test_a_rebuild_forgets_the_open_groups_of_a_block_that_is_gone(window, tmp_path):
    """``id()`` keys the open groups, and a removed entry's id is one a new
    Entry can be handed — which would open with the old one's groups."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    item.child(1).setExpanded(True)
    assert (id(block), 0x18) in panel._expanded_groups
    window._remove_entries([block])
    assert block not in window.workspace.entries
    assert panel._expanded_groups == set()


def test_opening_a_group_whose_strings_are_built_leaves_its_rows_alone(
    window, tmp_path, monkeypatch
):
    """A group row built with its block is opened again on the way in: doing
    the work twice would drop the very rows the reader is on."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    group = item.child(1)
    group.setExpanded(True)
    rows = [group.child(i) for i in range(group.childCount())]
    built: list[int] = []
    made = FilesPanel._string_item
    monkeypatch.setattr(
        FilesPanel,
        "_string_item",
        lambda self, entry, rec: built.append(rec.index) or made(self, entry, rec),
    )
    panel._on_expanded(group)
    assert built == []
    assert [group.child(i) for i in range(group.childCount())] == rows


def test_a_group_under_no_base_stands_there_with_its_strings(window, tmp_path):
    """A string no inner pointer reached is grouped under no base, so there is
    no table to go to and nothing to open: its row is built once, and holds no
    expander over a stub nothing would ever replace."""
    block = nested_block(window, tmp_path)
    panel = window.files_panel
    item = item_for(window, block)
    item.setExpanded(True)
    block.doc.strings.append(StringRecord(4, 0x1C * 8, 0x1E * 8, [], original="A[end]"))
    panel._update_item(block)
    last = item.child(item.childCount() - 1)
    assert last.text(0) == "?  1 string"
    assert panel.group_of(last) is None  # nowhere to go: a click does nothing
    assert not panel._is_stub(last.child(0))
    assert panel.string_of(last.child(0)) == (block, 4)
    panel.select_string(block, 4)
    assert panel.tree.currentItem() is panel._string_child(item, 4)


def test_a_rebuild_puts_an_open_group_back_open(window, tmp_path):
    """A row added or removed rebuilds the panel; a group that was open comes
    back open over its strings, as its block does."""
    block = nested_block(window, tmp_path)
    item = item_for(window, block)
    item.setExpanded(True)
    item.child(1).setExpanded(True)
    window.files_panel.rebuild()
    item = item_for(window, block)
    assert item.isExpanded()
    group = item.child(1)
    assert group.isExpanded()
    assert [group.child(i).text(0) for i in range(group.childCount())] == [
        "2  A[end]",
        "3  B[end]",
    ]
    assert not item.child(0).isExpanded()
