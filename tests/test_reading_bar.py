"""The Format and Reading bars: a block's settings edited live, the Strings and
Pointers modes, the encodings under the loaded tables, and a file's reading."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint

from helpers import pointer_rom
from mapchar.core.block import (
    BlockConfig,
    FixedLength,
    NextPointer,
    PointerTableSource,
    RangeSource,
)
from mapchar.project.projectfile import load_project, save_project
from mapchar.ui.raw_widget import POINTER_TOKENS
from mapchar.ui.reading_bar import RANGE
from mapchar.ui.widgets import select_data
from window_helpers import add_block, make_window, open_rom_and_table

ROM = pointer_rom((0x10, 0x13), "41 42 00 42 00")
"""Pointers at 0 and 2 to AB[end] at $10 and B[end] at $13."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _items(combo) -> list[object]:
    return [combo.itemData(i) for i in range(combo.count())]


def test_the_format_list_offers_the_encodings(window):
    items = _items(window.format_pick)
    assert "ascii" in items and "shift-jis" in items and "utf-16le" in items


def test_with_nothing_open_the_bar_shows_a_plain_range_reading(window, tmp_path):
    window.show()
    bar = window.reading_bar
    sections = bar.sections

    def default() -> None:
        assert bar.source_kind.currentData() == RANGE
        assert sections["Source"].isVisibleTo(window)
        assert sections["Strings"].isVisibleTo(window)
        assert not sections["Pointers"].isVisibleTo(window)
        assert not sections["Writing"].isVisibleTo(window)

    default()
    entry = open_rom_and_table(window, tmp_path, ROM)
    add_block(window, entry, "b", PointerTableSource(0, 4, 1, 1))
    assert sections["Pointers"].isVisibleTo(window)
    # The last entry closed leaves the default reading, not the block's.
    for open_entry in list(window.workspace.entries):
        window.apply_entry_remove(open_entry)
    default()


def _pointers(window):
    """Press the Pointers button, as the user does."""
    window.mode_toggle.button(True).click()


def test_the_mode_shows_only_its_own_settings(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    window._activate_entry(entry)
    window.show()
    bar = window.reading_bar
    assert not window.resolve_group.isVisibleTo(window)
    assert not bar.sections["Pointers"].isVisibleTo(window)
    # A file has no addresses to write back to.
    assert not bar.sections["Writing"].isVisibleTo(window)
    _pointers(window)
    assert window.resolve_group.isVisibleTo(window)
    assert bar.sections["Pointers"].isVisibleTo(window)


def test_a_file_with_no_table_reads_as_ascii(window, tmp_path):
    rom = tmp_path / "rom.bin"
    rom.write_bytes(b"HI\x00")
    window.open_rom(str(rom))
    assert window.format_pick.currentData() == "ascii"
    assert "".join(t.text() for t in window.raw._model.tokens) == "HI[end]"


def test_a_loaded_table_comes_before_the_encodings_and_is_picked(window, tmp_path):
    open_rom_and_table(window, tmp_path, ROM)
    items = _items(window.format_pick)
    assert items.index("main") < items.index("ascii")
    assert window.format_pick.currentData() == "main"


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


def test_skip_ranges_are_edited_in_a_popup_list(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x15))
    window.show()
    window.undo_stack.clear()
    picker = window.reading_bar.skips
    assert picker.currentText() == "none"
    picker.showPopup()
    popup = picker.popup
    assert popup.isVisible()
    popup.add_button.click()
    # A row typed one side at a time applies once both sides read.
    popup.table.item(0, 0).setText("11")
    assert block.config.skips == ()
    popup.table.item(0, 1).setText("13")
    assert block.config.skips == ((0x11, 0x13),)
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]"]
    assert picker.currentText() == "11>13"
    # The reload after the edit left the list alone, so the row is still
    # there to keep editing; a run of edits is one undo step.
    popup.table.item(0, 1).setText("14")
    assert block.config.skips == ((0x11, 0x14),)
    assert window.undo_stack.count() == 1
    # Removing it again in the same run leaves nothing to undo.
    popup.remove_button.click()
    assert block.config.skips == ()
    assert picker.currentText() == "none"
    assert window.undo_stack.count() == 0
    picker.add(0x11, 0x14)
    assert block.config.skips == ((0x11, 0x14),)
    window.undo_stack.undo()
    assert block.config.skips == ()
    assert popup.table.rowCount() == 0
    window.undo_stack.redo()
    assert popup.table.rowCount() == 1 and picker.currentText() == "11>14"


def test_a_skip_is_added_from_the_selection(window, tmp_path, monkeypatch):
    from mapchar.ui.main_window import raw_view

    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x15))
    window.undo_stack.clear()
    rows = []

    class ListedMenu(raw_view.QMenu):
        def exec(self, *a):  # noqa: A003 - QMenu's name
            rows.extend(self.actions())

    monkeypatch.setattr(raw_view, "QMenu", ListedMenu)
    window._raw_menu(QPoint(0, 0))
    assert "Add S&kip from Selection" not in [a.text() for a in rows]
    window._select_bytes(0x11, 2)
    rows.clear()
    window._raw_menu(QPoint(0, 0))
    row = next(a for a in rows if a.text() == "Add S&kip from Selection")
    row.trigger()
    assert block.config.skips == ((0x11, 0x13),)
    assert window.reading_bar.skips.currentText() == "11>13"
    # Each one is its own step, whatever was selected before.
    window._select_bytes(0x14, 1)
    row.trigger()
    assert block.config.skips == ((0x11, 0x13), (0x14, 0x15))
    stack = window.undo_stack
    edits = [i for i in range(stack.count()) if stack.text(i) == "Edit b"]
    assert len(edits) == 2
    # A file has no skip ranges, so its menu has no row for one.
    window._activate_entry(entry)
    window._select_bytes(0x11, 2)
    rows.clear()
    window._raw_menu(QPoint(0, 0))
    assert "Add S&kip from Selection" not in [a.text() for a in rows]


def test_a_block_keeps_its_mode_and_edits_its_other_settings(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", PointerTableSource(0, 4, 1, 1))
    toggle = window.mode_toggle
    assert toggle.value() is True
    assert window.reading_bar.sections["Pointers"].isVisibleTo(window)
    assert window.reading_bar.sections["Strings"].isVisibleTo(window)
    toggle.button(False).click()
    assert block.config.source == PointerTableSource(0, 4, 1, 1)
    toggle.button(True).click()
    bar = window.reading_bar
    bar.ptr_size.setValue(2)
    bar.ptr_stride.setValue(2)
    assert block.config.source == PointerTableSource(0, 4, 2, 2)
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]
    # A block without pointers has none to show.
    plain = add_block(window, entry, "r", RangeSource(0x10, 0x15))
    assert toggle.value() is False and not toggle.button(True).isEnabled()
    toggle.button(True).click()
    assert plain.config.source == RangeSource(0x10, 0x15)
    # A file's mode is still the user's to switch.
    window._activate_entry(entry)
    assert toggle.button(True).isEnabled()


def test_a_pointer_block_s_mode_shows_its_table_or_all_its_strings(window, tmp_path):
    # Out of order, one target twice, and a byte between the two strings.
    rom = pointer_rom((0x14, 0x10, 0x14), "41 42 00 FF 42 00")
    entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, entry, "b", PointerTableSource(0, 6, 2, 2))
    config = block.config
    toggle, sections = window.mode_toggle, window.reading_bar.sections
    assert window._bounds == (0, 6)
    toggle.button(False).click()
    assert block.config == config
    assert window._bounds == (0x10, 0x16) and window._offset == 0x10
    assert toggle.value() is False and not window.resolve_group.isVisibleTo(window)
    assert not sections["Pointers"].isVisibleTo(window)
    text = "".join(t.text() for t in window.raw._model.tokens)
    assert text.startswith("AB[end]") and text.endswith("B[end]")
    toggle.button(True).click()
    assert block.config == config and window._bounds == (0, 6)
    assert window.raw._model.tokens[0].table_id == POINTER_TOKENS
    assert sections["Pointers"].isVisibleTo(window)


def test_a_pointer_block_s_string_opened_alone_reads_as_text(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", PointerTableSource(0, 4, 2, 2))
    window._show_string(block, 0)
    assert "".join(t.text() for t in window.raw._model.tokens) == "AB[end]"
    # Shown as strings, with only the settings that shape a string.
    sections = window.reading_bar.sections
    assert window.mode_toggle.value() is False
    assert not window.resolve_group.isVisibleTo(window)
    assert sections["Strings"].isVisibleTo(window)
    assert not sections["Source"].isVisibleTo(window)
    assert not sections["Pointers"].isVisibleTo(window)
    window._show_view("text")
    assert window.text.edit.toPlainText().startswith("AB")
    # Back on its whole source, the block's view is its pointers again.
    window._view_source(block)
    window._show_view("raw")
    assert window.raw._model.tokens[0].table_id == POINTER_TOKENS
    assert window.mode_toggle.value() is True
    assert sections["Source"].isVisibleTo(window)
    assert sections["Pointers"].isVisibleTo(window)


def test_a_file_read_as_pointers_shows_where_each_points(window, tmp_path):
    open_rom_and_table(window, tmp_path, ROM)
    _pointers(window)
    window.reading_bar.ptr_size.setValue(2)
    window.reading_bar.ptr_stride.setValue(2)
    tokens = window.raw._model.tokens
    assert [t.text() for t in tokens[:2]] == ["→10", "→13"]
    assert all(t.table_id == POINTER_TOKENS for t in tokens)
    tip = window.raw._model.tips[0]
    assert tip.startswith("pointer $0010 → 10") and "AB" in tip
    # Resolved, a pointer's cells read what it reaches.
    window.resolve_pointers.setChecked(True)
    assert window.raw._model.tokens[1].text() == "B▪"
    window._show_view("text")
    body = window.text.edit.toPlainText()
    assert body.splitlines()[0] == "000000  $0010 → 10  AB▪"


def test_a_file_s_reading_is_its_session_and_is_saved(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    _pointers(window)
    window.reading_bar.ptr_size.setValue(2)
    window.resolve_pointers.setChecked(True)
    assert entry.session.config.source.size == 2
    path = tmp_path / "p.mapchar"
    window._capture_session()
    save_project(str(path), window.workspace.entries, entry)
    loaded = load_project(str(path))
    session = loaded.entries[0].session
    assert session.config == entry.session.config
    assert session.resolve_pointers


def test_new_block_from_selection_starts_from_the_bars(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    _pointers(window)
    window.reading_bar.ptr_size.setValue(2)
    window.reading_bar.ptr_stride.setValue(2)
    window._new_block(0, 4)
    block = window._entry
    assert block.parent is entry
    assert block.config.source == PointerTableSource(0, 4, 2, 2)
    assert window.mode_toggle.value() is True
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]


def test_a_preview_is_read_once_and_marked_where_it_was_cut():
    from mapchar.core.table import Entry, TokenKind
    from mapchar.core.tokens import Token
    from mapchar.ui.pointer_tokens import preview_reader

    reads: list[int] = []

    def read(target: int):
        reads.append(target)
        return [Token("", 0, 8, Entry("", TokenKind.TEXT, "Hi"))], target == 2

    seen: dict[int, str] = {}
    preview = preview_reader(read, seen)
    assert preview(1) == "Hi" and preview(2) == "Hi…" and preview(None) == ""
    assert preview_reader(read, seen)(1) == "Hi" and reads == [1, 2]
    assert preview_reader(None)(1) == ""


def test_switching_a_file_to_strings_and_back_restores_its_pointers(window, tmp_path):
    rom = tmp_path / "game.smc"
    rom.write_bytes(bytes(0x10000))
    entry = window.open_rom(str(rom))
    assert window._suggested_mapping() not in (None, "linear")
    config = BlockConfig(
        PointerTableSource(0, 0, 2, 4, "big", "linear", 3, 1), NextPointer(), "ascii"
    )
    window._read_file_as(entry, config, entry.session)
    window.mode_toggle.button(False).click()
    assert isinstance(entry.session.config.source, RangeSource)
    window.mode_toggle.button(True).click()
    assert entry.session.config == config
