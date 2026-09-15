"""The Codecs and Reading bars: a block's settings edited live, Pointer in the
Table list, the encodings under the loaded tables, and a file's reading."""

from __future__ import annotations

import pytest

from helpers import pointer_rom
from mapchar.core.block import (
    FixedLength,
    PointerTableSource,
    RangeSource,
)
from mapchar.project.projectfile import load_project, save_project
from mapchar.ui.main_window.codecs_bar import POINTER
from mapchar.ui.raw_widget import POINTER_TOKENS
from mapchar.ui.widgets import select_data
from window_helpers import add_block, make_window, open_rom_and_table

ROM = pointer_rom((0x10, 0x13), "41 42 00 42 00")
"""Pointers at 0 and 2 to AB[end] at $10 and B[end] at $13."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _items(combo) -> list[object]:
    return [combo.itemData(i) for i in range(combo.count())]


def test_the_table_list_heads_with_pointer_then_tables_then_encodings(window):
    items = _items(window.table_pick)
    assert items[0] == POINTER
    assert "ascii" in items and "shift-jis" in items and "utf-16le" in items
    assert POINTER not in _items(window.strings_pick)


def test_a_file_with_no_table_reads_as_ascii(window, tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(b"HI\x00")
    window.open_rom(str(rom))
    assert window.table_pick.currentData() == "ascii"
    assert "".join(t.text() for t in window.raw._model.tokens) == "HI[end]"


def test_a_loaded_table_comes_before_the_encodings_and_is_picked(window, tmp_path):
    open_rom_and_table(window, tmp_path, ROM)
    items = _items(window.table_pick)
    assert items.index("main") < items.index("ascii")
    assert window.table_pick.currentData() == "main"


def test_a_block_setting_applies_as_it_changes_and_undoes(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x15))
    assert len(block.doc.strings) == 2
    window.undo_stack.clear()
    bar = window.reading_bar
    select_data(bar.string_type, "fixed")
    assert isinstance(block.config.string_type, FixedLength)
    assert [s.start for s in block.doc.strings] == [0x10, 0x11, 0x12, 0x13, 0x14]
    # Stepping a spin box is a run on one control: one undo step for all of it.
    bar.fixed_length.setValue(2)
    bar.fixed_length.setValue(3)
    assert [s.start for s in block.doc.strings] == [0x10, 0x13]
    assert window.undo_stack.count() == 2
    window.undo_stack.undo()
    assert block.config.string_type == FixedLength(1)
    assert bar.fixed_length.value() == 1
    window.undo_stack.undo()
    assert len(block.doc.strings) == 2


def test_pointer_turns_a_block_into_a_pointer_table_read_through_its_table(
    window, tmp_path
):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0, 4))
    select_data(window.table_pick, POINTER)
    assert block.config.source == PointerTableSource(0, 4, 1, 1)
    assert block.config.table_id == "main"
    assert window.strings_pick.currentData() == "main"
    bar = window.reading_bar
    bar.ptr_size.setValue(2)
    bar.ptr_stride.setValue(2)
    assert block.config.source == PointerTableSource(0, 4, 2, 2)
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]
    # Back to a table, the region reads as text again.
    select_data(window.table_pick, "main")
    assert block.config.source == RangeSource(0, 4)


def test_a_file_read_as_pointers_shows_where_each_points(window, tmp_path):
    open_rom_and_table(window, tmp_path, ROM)
    select_data(window.table_pick, POINTER)
    window.reading_bar.ptr_size.setValue(2)
    window.reading_bar.ptr_stride.setValue(2)
    tokens = window.raw._model.tokens
    assert [t.text() for t in tokens[:2]] == ["→10", "→13"]
    assert all(t.table_id == POINTER_TOKENS for t in tokens)
    tip = window.raw._model.tips[0]
    assert tip.startswith("pointer $0010 → 10") and "AB" in tip
    # With the strings shown, a pointer's cells read what it reaches.
    window.show_strings.setChecked(True)
    assert window.raw._model.tokens[1].text() == "B▪"
    window._show_view("text")
    body = window.text.edit.toPlainText()
    assert body.splitlines()[0] == "000000  $0010 → 10  AB▪"


def test_a_file_s_reading_is_its_session_and_is_saved(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    select_data(window.table_pick, POINTER)
    window.reading_bar.ptr_size.setValue(2)
    window.show_strings.setChecked(True)
    assert entry.session.config.source.size == 2
    path = tmp_path / "p.mapchar"
    window._capture_session()
    save_project(str(path), window.workspace.entries, entry)
    loaded = load_project(str(path))
    session = loaded.entries[0].session
    assert session.config == entry.session.config
    assert session.show_strings


def test_new_block_from_selection_starts_from_the_bars(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    select_data(window.table_pick, POINTER)
    window.reading_bar.ptr_size.setValue(2)
    window.reading_bar.ptr_stride.setValue(2)
    window._new_block(0, 4)
    block = window._entry
    assert block.parent is entry
    assert block.config.source == PointerTableSource(0, 4, 2, 2)
    assert window.table_pick.currentData() == POINTER
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]
