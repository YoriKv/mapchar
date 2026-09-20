"""The Files panel: the row it draws for an entry, the menu it builds over one,
and the folders that group them."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QAbstractItemView

from mapchar.core.block import RangeSource, Status
from mapchar.project.entry import EntryKind
from mapchar.ui.entry_text import sorted_entries
from mapchar.ui.files_panel import STATUS_COL
from window_helpers import (
    ab_ba_rom,
    add_block,
    item_for,
    make_yes_window,
    menu_state,
    open_rom_and_table,
)

DATA = ab_ba_rom(20)


@pytest.fixture
def window(qtbot, monkeypatch):
    """A live window whose every modal answers Yes / Discard without showing."""
    return make_yes_window(qtbot, monkeypatch)


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


def test_a_string_rows_menu_greys_what_would_edit_its_block(window, tmp_path):
    """A string row's menu is its block's, but the rows that edit the row name
    the block rather than the string clicked, so they go dead."""
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    state = menu_state(window._build_files_menu(block, 0))
    dead = {"Rename…", "Cut", "Copy", "Duplicate", "Remove", "Move Up", "Move Down"}
    dead |= {"Sort By", "New Folder"}
    assert {row for row, live in state.items() if not live} >= dead
    assert state["Write"] and state["Export"]
    # The block's own row keeps every one of them.
    assert all(menu_state(window._build_files_menu(block))[row] for row in dead)


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
        state = menu_state(window._build_files_menu(entry))
        assert {row for row in dead if not state[row]} == dead, entry.name
    assert menu_state(window._build_files_menu(table))["Duplicate"]
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    state = menu_state(window._build_files_menu(block))
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
