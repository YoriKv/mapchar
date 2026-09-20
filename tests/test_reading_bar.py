"""The Format and Reading bars: a block's settings edited live, the Strings and
Pointers modes, the encodings under the loaded tables, and a file's reading."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QPoint

from helpers import pointer_rom, texts
from mapchar.core.block import (
    BlockConfig,
    FixedLength,
    Lines,
    NestedPointerSource,
    NextPointer,
    Pascal,
    PointerTableSource,
    RangeSource,
)
from mapchar.project.projectfile import load_project, save_project
from mapchar.ui.reading_bar import END, LIST, NESTED, RANGE
from mapchar.ui.token_text import POINTER_TOKENS
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
        # A file's text has one source kind and no addresses: every section is
        # there, so nothing moves when a block opens, and those are greyed.
        assert bar.source_kind.currentData() == RANGE
        assert all(section.isVisibleTo(window) for section in sections.values())
        # (Asked of the bar: with nothing open the window greys all of it.)
        assert not bar.start.isEnabledTo(bar)
        assert not bar.source_kind.isEnabledTo(bar)
        assert bar.string_type.isEnabledTo(bar)
        assert not bar.ptr_size.isEnabledTo(bar)
        assert not bar.writing.isEnabledTo(bar)

    default()
    entry = open_rom_and_table(window, tmp_path, ROM)
    add_block(window, entry, "b", PointerTableSource(0, 4, 1, 1))
    assert bar.start.isEnabled() and bar.source_kind.isEnabled()
    assert bar.ptr_size.isEnabled() and bar.writing.isEnabled()
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
    assert not window.resolve_group.isEnabled()
    assert not bar.ptr_size.isEnabled()
    # A file has no addresses to write back to.
    assert not bar.writing.isEnabled()
    _pointers(window)
    assert window.resolve_group.isEnabled()
    assert bar.ptr_size.isEnabled()


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


def test_the_edit_button_opens_the_picked_table_and_not_an_encoding(window, tmp_path):
    open_rom_and_table(window, tmp_path, ROM)
    assert window.table_edit.isEnabled()
    window.table_edit.click()
    assert window.table_editor.entry is window.workspace.entry_for_table("main")
    select_data(window.format_pick, "ascii")
    assert not window.table_edit.isEnabled()


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


def test_the_skips_picker_is_there_in_either_mode_of_a_block(window, tmp_path):
    """Skips shape a string's bytes, not where a source's addresses are, so the
    picker sits with the Strings settings and a view of the strings keeps it."""
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", PointerTableSource(0, 4, 2, 2))
    window.show()
    picker = window.reading_bar.skips
    assert picker.isEnabled()
    window.mode_toggle.button(False).click()
    assert picker.isEnabled()
    window._show_string(block, 0)
    assert picker.isEnabled()
    # A file has no addresses of its own, so no skips either.
    window._activate_entry(entry)
    assert picker.isVisibleTo(window) and not picker.isEnabled()


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
    toggle, bar = window.mode_toggle, window.reading_bar
    assert window._bounds == (0, 6)
    toggle.button(False).click()
    assert block.config == config
    assert window._bounds == (0x10, 0x16) and window._offset == 0x10
    assert toggle.value() is False and not window.resolve_group.isEnabled()
    assert not bar.ptr_size.isEnabled()
    text = "".join(t.text() for t in window.raw._model.tokens)
    assert text.startswith("AB[end]") and text.endswith("B[end]")
    toggle.button(True).click()
    assert block.config == config and window._bounds == (0, 6)
    assert window.raw._model.tokens[0].table_id == POINTER_TOKENS
    assert bar.ptr_size.isEnabled()


def test_a_block_comes_back_on_the_strings_it_was_left_reading(window, tmp_path):
    rom = pointer_rom((0x14, 0x10, 0x14), "41 42 00 FF 42 00")
    entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, entry, "b", PointerTableSource(0, 6, 2, 2))
    window.mode_toggle.button(False).click()
    assert window._bounds == (0x10, 0x16)
    window._activate_entry(entry)
    window._show_entry(block)  # its Files row
    assert window._bounds == (0x10, 0x16) and window._offset == 0x10
    assert window.mode_toggle.value() is False
    # The row of the block already on screen still takes the view back out.
    window._show_entry(block)
    assert window._bounds == (0, 6) and window.mode_toggle.value() is True
    window._activate_entry(entry)
    window._show_entry(block)
    assert window._bounds == (0, 6) and window.mode_toggle.value() is True


def test_one_string_is_not_the_mode_a_block_is_left_in(window, tmp_path):
    """A string opened from the Files panel is laid over the block's mode: the
    block's row brings it back on its source, and Back out of the string lands
    on the mode the string was opened over."""
    rom = pointer_rom((0x14, 0x10, 0x14), "41 42 00 FF 42 00")
    entry = open_rom_and_table(window, tmp_path, rom)
    block = add_block(window, entry, "b", PointerTableSource(0, 6, 2, 2))
    window._show_string(block, 1)
    window._activate_entry(entry)
    window._show_entry(block)
    assert window._bounds == (0, 6) and window.mode_toggle.value() is True
    # Opened over the Strings mode, Back returns to the Strings mode.
    window.mode_toggle.button(False).click()
    window._show_string(block, 1)
    assert window._bounds == (0x14, 0x16)
    window._history_step(-1)
    assert window._bounds == (0x10, 0x16) and window.mode_toggle.value() is False
    window._history_step(1)  # onto the string again
    window._activate_entry(entry)
    window._history_step(-1)  # the string, from another entry
    assert window._entry is block and window._bounds == (0x14, 0x16)
    window._history_step(-1)  # and out of it, onto the mode it was opened over
    assert window._bounds == (0x10, 0x16) and window.mode_toggle.value() is False


def test_a_pointer_block_s_string_opened_alone_reads_as_text(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", PointerTableSource(0, 4, 2, 2))
    window._show_string(block, 0)
    assert "".join(t.text() for t in window.raw._model.tokens) == "AB[end]"
    # Shown as strings, with only the settings that shape a string live.
    bar = window.reading_bar
    assert window.mode_toggle.value() is False
    assert not window.resolve_group.isEnabled()
    assert bar.string_type.isEnabled() and bar.writing.isEnabled()
    assert not bar.start.isEnabled()
    assert not bar.ptr_size.isEnabled()
    window._show_view("text")
    assert window.text.edit.toPlainText().startswith("AB")
    # Back on its whole source, the block's view is its pointers again.
    window._view_source(block)
    window._show_view("raw")
    assert window.raw._model.tokens[0].table_id == POINTER_TOKENS
    assert window.mode_toggle.value() is True
    assert bar.start.isEnabled()
    assert bar.ptr_size.isEnabled()


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


def test_a_nested_source_s_outer_pointers_are_labelled_not_followed():
    """Following one would read an inner pointer table's bytes as characters."""
    from mapchar.core.tokens import render
    from mapchar.pipeline.view_read import PointerCell
    from mapchar.ui.pointer_tokens import hex_tokens, text_tokens

    cells = [
        PointerCell(0, 2, 8, 8, role="table"),
        PointerCell(2, 2, 0xC, 0xC, role="base"),
        PointerCell(8, 2, 1, 0xD),
    ]
    preview = lambda target: "AB"  # noqa: E731 - every target reads as text
    assert render(text_tokens(cells, 0, preview, True)).splitlines() == [
        "000000  $0008 → 8  inner table",
        "000002  $000C → C  base",
        "000008  $0001 → D  AB",
    ]
    # The label stands whether or not Follow pointers is on: it says what the
    # pointer is, not what a reading of its target came to.
    assert render(text_tokens(cells, 0, preview, False)).splitlines() == [
        "000000  $0008 → 8  inner table",
        "000002  $000C → C  base",
        "000008  $0001 → D",
    ]
    tokens, tips = hex_tokens(cells, 0, preview, True)
    assert render(tokens) == "→8→CAB"
    assert [tips[t.bit_start] for t in tokens] == [
        "pointer $0008 → 8\ninner table",
        "pointer $000C → C\nbase",
        "pointer $0001 → D\nAB",
    ]


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


def test_the_mapping_is_listed_by_name_and_the_bank_only_where_it_reads(
    window, tmp_path
):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", PointerTableSource(0, 4, 2, 2))
    window.show()
    bar = window.reading_bar
    assert bar.ptr_mapping.currentText() == "Linear (file offset)"
    # Greyed where the mapping reads no bank, rather than gone: it keeps its
    # place, so picking a mapping moves nothing.
    assert bar.ptr_bank.isVisibleTo(window) and not bar.ptr_bank.isEnabled()
    select_data(bar.ptr_mapping, "lorom")
    bar.ptr_mapping.activated.emit(bar.ptr_mapping.currentIndex())
    assert block.config.source.mapping_id == "lorom"
    assert bar.ptr_bank.isEnabled()
    # An id the list does not know is still an id, and keeps its bank.
    bar.ptr_mapping.setCurrentText("banked:8000:4000")
    bar.ptr_mapping.lineEdit().editingFinished.emit()
    assert block.config.source.mapping_id == "banked:8000:4000"
    assert bar.ptr_bank.isEnabled()


def test_a_length_prefix_wider_than_a_byte_has_a_byte_order(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x15))
    window.show()
    bar = window.reading_bar
    select_data(bar.string_type, "pascal")
    assert not bar.pascal_endian.isVisibleTo(window)
    bar.pascal_width.setValue(2)
    assert bar.pascal_endian.isVisibleTo(window)
    select_data(bar.pascal_endian, "big")
    assert block.config.string_type == Pascal(2, False, "big")
    bar.pascal_tokens.setChecked(True)
    assert block.config.string_type == Pascal(2, True, "big")


def test_lines_shows_a_count(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x15))
    window.show()
    bar = window.reading_bar
    assert not bar._groups["lines"].isVisibleTo(window)
    select_data(bar.string_type, "lines")
    assert bar._groups["lines"].isVisibleTo(window)
    assert not bar._groups["spp"].isVisibleTo(window)
    bar.lines.setValue(8)
    assert block.config.string_type == Lines(8)


def test_the_writing_section_says_what_a_blank_bound_and_automatic_mean(
    window, tmp_path
):
    entry = open_rom_and_table(window, tmp_path, ROM)
    add_block(window, entry, "p", PointerTableSource(0, 4, 2, 2))
    bar = window.reading_bar
    assert bar.write_mode.itemText(0) == "Automatic (packed)"
    # A pointer block's strings end at $15, and the fill behind them is room
    # a shorter layout left: the end of that run bounds it.
    assert bar.bound.placeholderText() == window.address_spelling.format(0x1D)
    add_block(window, entry, "r", RangeSource(0x10, 0x15))
    assert bar.write_mode.itemText(0) == "Automatic (slotted)"
    assert bar.bound.placeholderText() == window.address_spelling.format(0x15)


def test_packed_is_unavailable_where_skips_or_a_header_force_slotted(window, tmp_path):
    """A write cannot lay end to end what it has to step around, so Packed is
    disabled there and says what the block is written as instead of leaving an
    edit silently slotted."""
    from mapchar.core.block import WriteMode

    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x15))
    bar = window.reading_bar
    at = bar.write_mode.findData(WriteMode.PACKED)
    select_data(bar.write_mode, WriteMode.PACKED)
    assert bar.write_mode.model().item(at).isEnabled()
    assert bar.writing.currentText().startswith("Packed ·")
    bar.header.setValue(2)
    assert not bar.write_mode.model().item(at).isEnabled()
    assert bar.write_mode.itemText(at) == "Packed (slotted)"
    assert bar.writing.currentText().startswith("Packed (slotted) ·")
    # The block keeps the mode it holds, so taking the header away is enough
    # to write it packed again.
    assert block.config.write_mode is WriteMode.PACKED
    assert block.config.effective_write_mode is WriteMode.SLOTTED
    bar.header.setValue(0)
    assert bar.write_mode.model().item(at).isEnabled()
    assert bar.writing.currentText().startswith("Packed ·")
    # Skip ranges force it just the same.
    add_block(
        window,
        entry,
        "s",
        RangeSource(0x10, 0x15),
        skips=((0x11, 0x12),),
        write_mode=WriteMode.PACKED,
    )
    assert not bar.write_mode.model().item(at).isEnabled()
    assert bar.writing.currentText().startswith("Packed (slotted) ·")


def test_fixed_length_strings_on_a_range_have_a_count_that_sets_stop(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    block = add_block(window, entry, "b", RangeSource(0x10, 0x16))
    window.show()
    bar = window.reading_bar
    assert not bar._groups["count"].isVisibleTo(window)
    select_data(bar.string_type, "fixed")
    bar.fixed_length.setValue(3)
    assert bar._groups["count"].isVisibleTo(window) and bar.count.value() == 2
    bar.count.setValue(3)
    assert block.config.source == RangeSource(0x10, 0x19)
    assert block.config.string_type == FixedLength(3)
    assert [s.start for s in block.doc.strings] == [0x10, 0x13, 0x16]


def test_the_count_of_fixed_records_steps_over_their_header(window, tmp_path):
    """A record behind a header is header + length bytes long, so that is what
    Count counts and what a count typed in sets Stop by. It counts the records
    the reading extracts, a last one cut short by Stop included."""
    from mapchar.project.formats.table_native import HEADER

    data = b"\x00\x00AAAA\x00\x01BBBB\x00\x02CCCC\x00\x03DDDD"
    table = f"{HEADER}\n@table main\n41=A\n42=B\n43=C\n44=D\n/00=[end]\n"
    entry = open_rom_and_table(window, tmp_path, data, table=table)
    block = add_block(
        window, entry, "b", RangeSource(0, 0x18), FixedLength(4), header=2
    )
    bar = window.reading_bar
    assert bar.count.value() == 4
    assert texts(block.doc.strings) == ["AAAA", "BBBB", "CCCC", "DDDD"]
    bar.count.setValue(3)
    assert block.config.source == RangeSource(0, 0x12)
    assert texts(block.doc.strings) == ["AAAA", "BBBB", "CCCC"]
    # The header taken away, the same range holds four whole records and a
    # fourth cut short by Stop.
    bar.header.setValue(0)
    assert bar.count.value() == 5
    assert len(block.doc.strings) == 5


NESTED_ROM = bytes.fromhex(
    "08 00 0C 00 00 00 00 00  01 00 04 00 FF 41 42 00  42 00 FF FF"
)
"""One record naming an inner table at 8 counting from $C, reaching AB[end] and
B[end], and a null record."""


def test_a_nested_source_is_picked_and_edited_in_the_bar(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, NESTED_ROM)
    block = add_block(window, entry, "b", PointerTableSource(0, 8, 2, 2))
    window.show()
    bar = window.reading_bar
    assert not bar._groups["inner"].isVisibleTo(window)
    select_data(bar.source_kind, NESTED)
    assert isinstance(block.config.source, NestedPointerSource)
    assert bar._groups["inner"].isVisibleTo(window)
    assert bar._groups["ptr_stride"].isVisibleTo(window)
    bar.ptr_stride.setValue(4)
    bar.ptr_null.setText("0")
    bar.ptr_null.editingFinished.emit()
    bar.inner_null.setText("0")
    bar.inner_null.editingFinished.emit()
    assert block.config.source == NestedPointerSource(
        0, 8, 2, 4, null=0, inner_size=2, inner_null=0
    )
    assert [s.current_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]
    assert bar.bound.placeholderText() == "each group's end"
    bar.fill.setText("EEDD")
    bar.fill.editingFinished.emit()
    assert block.config.fill == b"\xee\xdd"
    # What the bar shows is what it reads back.
    bar.load(block.config, block=True)
    assert bar.config(block.config, "main") == block.config
    assert bar.fill.text() == "EEDD"
    # A file has no strings of its own to group.
    window._activate_entry(entry)
    window._on_mode(True)
    assert not bar.source_kind.model().item(2).isEnabled()


def test_an_edit_in_a_nested_block_reads_back_only_its_group_and_undoes(
    window, tmp_path
):
    entry = open_rom_and_table(window, tmp_path, NESTED_ROM)
    block = add_block(
        window, entry, "b", NestedPointerSource(0, 8, 2, 4, null=0, inner_null=0)
    )
    assert window._set_translation(block, 1, "BB[end]") == []
    assert window._doc.data[8:0x14] == bytes.fromhex(
        "01 00 04 00 FF 41 42 00 42 42 00 FF"
    )
    assert [s.current_text() for s in block.doc.strings] == ["AB[end]", "BB[end]"]
    window.undo_stack.undo()
    assert window._doc.data == NESTED_ROM
    assert [s.current_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]


def test_a_version_1_project_s_fixed_strings_open_without_their_end_token(
    window, tmp_path
):
    import json

    open_rom_and_table(window, tmp_path, bytes.fromhex("41 42 00 FF  42 41 00 FF"))
    proj = tmp_path / "p.mapchar"
    proj.write_text(
        json.dumps(
            {
                "version": 1,
                "entries": [
                    {"kind": "file", "name": "rom.bin", "path": "rom.bin"},
                    {"kind": "table", "name": "main.tbl", "path": "main.tbl"},
                    {
                        "kind": "block",
                        "name": "names",
                        "path": "rom.bin",
                        "parent": 0,
                        "config": "source=range start=$0 stop=$8 "
                        "type=fixed:4:stop table=main",
                        "strings": [
                            {"i": 0, "o": "AB[end]"},
                            {"i": 1, "o": "AA[end]", "s": "edited"},
                        ],
                    },
                ],
            }
        )
    )
    window._new_project()
    assert window.open_project(str(proj))
    block = next(e for e in window.workspace.entries if e.name == "names")
    window._activate_entry(block)
    assert [
        (s.original, s.current_text(), s.status.value) for s in block.doc.strings
    ] == [
        ("AB", "AB", "untouched"),
        ("AA", "BA", "edited"),
    ]
    assert not block.fixed_ends_shown


def test_a_header_is_a_range_block_s_setting(window, tmp_path):
    """Records of ``[2 bytes][length][text]`` read once the bar is told the
    header, with no skip range per record; a pointer block has no such field."""
    data = bytes.fromhex("05 CB 02 41 42  06 CB 01 42") + b"\xff" * 4
    entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, entry, "b", RangeSource(0, 9), Pascal(1))
    bar = window.reading_bar
    assert bar.header.isEnabled() and bar.header.text() == "off"
    bar.header.setValue(2)
    assert block.config.header == 2
    assert [s.current_text() for s in block.doc.strings] == ["AB", "B"]
    assert bar.write_mode.itemText(0) == "Automatic (slotted)"
    window.undo_stack.undo()
    assert block.config.header == 0 and bar.header.value() == 0
    pointers = add_block(
        window, entry, "p", PointerTableSource(0, 2, 2, 2, "little", "linear", 0)
    )
    assert pointers.config.header == 0 and not bar.header.isEnabled()


@pytest.mark.parametrize("width", (1920, 2560, 3000))
def test_the_bar_keeps_its_sections_in_place_at_any_width(window, tmp_path, width):
    """Nothing moves: at a width where sections share a row as much as at one
    where they do not, picking another source kind or string type leaves every
    section where it was and the bar as tall as it was."""
    from PySide6.QtWidgets import QApplication

    from mapchar.ui.reading_bar import FIXED_LENGTH, LINES, PASCAL, TABLE

    entry = open_rom_and_table(window, tmp_path, ROM)
    add_block(window, entry, "b", PointerTableSource(0, 4, 2, 2))
    window.resize(width, 900)
    window.show()
    bar = window.reading_bar

    def placed():
        QApplication.processEvents()
        return [(bar.sections[t].x(), bar.height()) for t in bar.sections]

    laid_out = placed()
    for kind in (LIST, NESTED, TABLE):
        select_data(bar.source_kind, kind)
        assert placed() == laid_out, kind
    for string_type in (FIXED_LENGTH, PASCAL, LINES, END):
        select_data(bar.string_type, string_type)
        assert placed() == laid_out, string_type
