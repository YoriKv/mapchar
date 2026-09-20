"""The entry clipboard and the file drops that fill it: what a payload can
say, and that a copy crosses windows because a payload is all it carries."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QMimeData, QPoint, QUrl
from PySide6.QtGui import QDropEvent
from PySide6.QtWidgets import QApplication

from mapchar.core.block import RangeSource, Status
from mapchar.project.entry import EntryKind
from mapchar.project.projectfile import entries_from_payload, entries_payload
from mapchar.project.tables import same_table
from window_helpers import (
    TABLE,
    ab_ba_rom,
    add_block,
    make_window,
    make_yes_window,
    open_rom_and_table,
)

DATA = ab_ba_rom(20)


@pytest.fixture
def window(qtbot, monkeypatch):
    """A live window whose every modal answers Yes / Discard without showing."""
    return make_yes_window(qtbot, monkeypatch)


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
