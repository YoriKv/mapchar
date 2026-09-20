"""Where the view sits, how that position is spelled, and how it is reached.

Three things: the address formats (:mod:`mapchar.core.address`, Qt-free), the
application-wide key filter that lets a navigation key work wherever the focus
is, and the visit trail Back and Forward walk.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPointF, Qt
from PySide6.QtGui import QKeyEvent, QMouseEvent

from mapchar.core.address import (
    BANK_PRESETS,
    PRESETS_BY_ID,
    BankLayout,
    SplitBankLayout,
    format_address,
    parse_address,
)
from mapchar.core.block import PointerListSource, RangeSource
from mapchar.core.numbers import clamp, format_hex_offset, parse_hex_offset
from mapchar.project.formats.table_native import HEADER
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.main_window.navigation import CUSTOM_ID
from window_helpers import add_block, make_window, open_rom_and_table

DATA = bytes(range(64))


# --- the address formats, without a window ---------------------------------


@pytest.mark.parametrize("preset", BANK_PRESETS, ids=lambda p: p.id)
def test_every_preset_round_trips(preset):
    """Every offset the layout can address formats to something it reads back."""
    layout = preset.layout
    if isinstance(layout, BankLayout):
        size = layout.bank_size
        offsets = (0, 1, size - 1, size, size * 3 + 7)
    else:
        # A split layout addresses exactly two windows of ``split`` bytes; past
        # that there is no bank left to name, so the range stops there.
        offsets = (0, layout.split - 1, layout.split, layout.split + 7)
    for offset in offsets:
        assert layout.parse(layout.format(offset)) == offset


def test_the_two_lorom_spellings_of_a_byte_agree():
    lorom = PRESETS_BY_ID["snes-lorom"].layout
    assert lorom.format(0x10) == "$00:8010"
    # The mirror docs write the same byte under is folded onto the first anchor.
    assert lorom.parse("$80:8010") == 0x10
    assert lorom.parse("$00:8010") == 0x10


def test_hirom_writes_a_whole_bank_per_64k():
    hirom = PRESETS_BY_ID["snes-hirom"].layout
    assert hirom.format(0) == "$C0:0000"
    assert hirom.format(0x10000) == "$C1:0000"
    assert hirom.parse("$40:0000") == 0  # the Super FX mirror


def test_a_split_layout_crosses_its_boundary():
    layout = PRESETS_BY_ID["snes-exhirom"].layout
    assert isinstance(layout, SplitBankLayout)
    assert layout.format(0x3FFFFF) == "$FF:FFFF"
    assert layout.format(0x400000) == "$40:0000"
    assert layout.parse("$40:0000") == 0x400000


def test_parse_refuses_an_address_outside_the_bank_window():
    lorom = PRESETS_BY_ID["snes-lorom"].layout
    assert lorom.parse("$00:0000") is None  # RAM, not ROM
    assert lorom.parse("nonsense") is None
    assert lorom.parse("") is None


def test_a_bare_six_digit_address_reads_as_bank_plus_offset():
    lorom = PRESETS_BY_ID["snes-lorom"].layout
    assert lorom.parse("018010") == 0x8010


def test_a_wider_bank_takes_more_address_digits():
    gba = PRESETS_BY_ID["gba"].layout
    assert gba.addr_digits == 6
    assert gba.format(0x1234) == "$08:001234"


# --- the address format on the navigation bar ------------------------------


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _opened(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, DATA)
    window._activate_entry(entry)
    return entry


def test_the_offset_box_spells_the_chosen_format(window, tmp_path):
    _opened(window, tmp_path)
    assert window.offset_box.text() == "000000"
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    window._go_to(0x10)
    assert window.offset_box.text() == "$00:8010"


def test_the_status_bar_spells_the_selection_in_the_chosen_format(window, tmp_path):
    _opened(window, tmp_path)
    window._select_bytes(0x10, 4)
    assert "selected 000010–000013 (4 bytes)" in window.nav_status.text()
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    assert "selected $00:8010–$00:8013 (4 bytes)" in window.nav_status.text()


def test_the_offset_box_reads_the_chosen_format_back(window, tmp_path):
    _opened(window, tmp_path)
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    window.offset_box.setText("$00:8020")
    window._on_offset_typed()
    assert window._offset == 0x20
    # A flat offset still reads, so a position copied from elsewhere lands.
    window.offset_box.setText("10")
    window._on_offset_typed()
    assert window._offset == 0x10


def test_an_unreadable_address_reverts_the_box(window, tmp_path):
    _opened(window, tmp_path)
    assert window.address_pick.currentData() == "hex"
    window._go_to(4)
    window.offset_box.setText("zzz")
    window._on_offset_typed()
    assert window._offset == 4 and window.offset_box.text() == "000004"


def test_the_custom_bank_fields_make_a_layout(window, tmp_path):
    _opened(window, tmp_path)
    window.address_pick.setCurrentIndex(window.address_pick.findData(CUSTOM_ID))
    assert window.custom_bank_row.isVisibleTo(window)
    window.bank_size_box.setText("20")
    window.addr_base_box.setText("0")
    window.bank_base_box.setText("0")
    window._on_custom_bank()  # what editing a field emits
    assert window._format_address(0x21) == "$01:0001"
    assert window._parse_address("$01:0001") == 0x21


def test_the_chosen_format_is_remembered(window, tmp_path, qtbot, monkeypatch):
    _opened(window, tmp_path)
    window.address_pick.setCurrentIndex(window.address_pick.findData("gb"))
    other = make_window(qtbot, monkeypatch)
    assert other.address_pick.currentData() == "gb"


def test_an_address_reads_its_layout_then_flat_hex():
    lorom = PRESETS_BY_ID["snes-lorom"].layout
    assert format_address(0x10) == "000010"
    assert format_address(0x10, lorom) == "$00:8010"
    assert parse_address("$00:8010", lorom) == 0x10
    assert parse_address("$10", lorom) == 0x10
    assert parse_address("0x10") == 0x10
    for unreadable in ("", "  ", "-10", "zz", "$00:8010"):
        assert parse_address(unreadable) is None, unreadable


def test_an_offset_is_signed_hex_with_an_optional_dollar():
    assert [parse_hex_offset(t) for t in ("1F0", "$1F0", "-10", "-$10", "$-10")] == [
        0x1F0,
        0x1F0,
        -0x10,
        -0x10,
        -0x10,
    ]
    assert format_hex_offset(0x1F0) == "1F0" and format_hex_offset(-0x10) == "-10"
    for unreadable in ("", "-", "$", "1-2", "zz"):
        with pytest.raises(ValueError):
            parse_hex_offset(unreadable)


def test_clamp_holds_a_value_in_range_and_gives_the_low_end_of_an_empty_one():
    assert [clamp(v, 0, 10) for v in (-1, 0, 5, 10, 11)] == [0, 0, 5, 10, 10]
    # An empty file has no last byte: the upper bound falls below the lower one
    # and the answer is still the lower one.
    assert clamp(7, 0, -1) == 0


def test_every_address_field_follows_the_address_format(window, tmp_path):
    """The Reading bar's and the Hex panel's addresses are spelled as the
    navigation bar spells a position, and re-spelled when its format changes."""
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0x10, 0x20))
    window._activate_entry(block)
    bar, panel = window.reading_bar, window.hex_panel
    assert (bar.start.text(), bar.stop.text()) == ("000010", "000020")
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    assert (bar.start.text(), bar.stop.text()) == ("$00:8010", "$00:8020")
    panel.goto.setText("$00:8004")
    panel.goto.editingFinished.emit()
    assert panel.goto.text() == "$00:8004" and panel.goto.value() == 4
    bar.stop.setText("$00:8018")
    bar.stop.editingFinished.emit()
    assert block.config.source.stop == 0x18
    window.address_pick.setCurrentIndex(window.address_pick.findData("hex"))
    assert (bar.stop.text(), panel.goto.text()) == ("000018", "000004")


def test_a_pointer_list_is_spelled_in_the_address_format(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", PointerListSource((2, 4), 2))
    window._activate_entry(block)
    bar = window.reading_bar
    assert bar.ptr_addresses.text() == "000002, 000004"
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    assert bar.ptr_addresses.text() == "$00:8002, $00:8004"
    bar.ptr_addresses.setText("$00:8002, 6")
    bar.ptr_addresses.editingFinished.emit()
    assert block.config.source.addresses == (2, 6)


# --- the key filter --------------------------------------------------------


def _press(window, key, modifier=Qt.KeyboardModifier.NoModifier):
    """One key press through the application filter, as Qt would deliver it.

    The filter only answers for the active window, which an offscreen window
    never is, so it is told that it is — the alternative is to skip the filter
    and test the handler alone, which is the half that was never broken.
    """
    window.isActiveWindow = lambda: True
    event = QKeyEvent(QEvent.Type.KeyPress, key, modifier)
    return window.eventFilter(window, event)


def _focus(monkeypatch, widget):
    """Say which widget has the keyboard.

    An offscreen window is never activated, so ``setFocus`` leaves
    ``QApplication.focusWidget()`` as it found it — and that is the one thing the
    filter's yield rule reads.
    """
    from PySide6.QtWidgets import QApplication

    monkeypatch.setattr(QApplication, "focusWidget", staticmethod(lambda: widget))


def test_a_navigation_key_works_while_a_button_has_focus(window, tmp_path, monkeypatch):
    """The bug the filter fixes: a focused control used to eat the whole row of
    navigation keys, because the window's own ``keyPressEvent`` never ran."""
    # More than the view holds: with the whole file in view the keys are off.
    window._activate_entry(open_rom_and_table(window, tmp_path, bytes(range(256)) * 4))
    _focus(monkeypatch, window.block_export)
    assert _press(window, Qt.Key.Key_Down)
    assert window._offset == BYTES_PER_ROW
    assert _press(window, Qt.Key.Key_Right)
    assert window._offset == BYTES_PER_ROW + 1
    assert _press(window, Qt.Key.Key_Home)
    assert window._offset == 0


def test_navigation_keys_yield_to_a_text_field(window, tmp_path, monkeypatch):
    _opened(window, tmp_path)
    _focus(monkeypatch, window.offset_box)
    assert not _press(window, Qt.Key.Key_Down)
    assert window._offset == 0


def test_navigation_keys_yield_to_the_files_list(window, tmp_path, monkeypatch):
    _opened(window, tmp_path)
    _focus(monkeypatch, window.files_panel.tree)
    assert not _press(window, Qt.Key.Key_Down)
    assert window._offset == 0


def test_alt_arrows_are_left_to_the_history_shortcuts(window, tmp_path, monkeypatch):
    _opened(window, tmp_path)
    _focus(monkeypatch, window.block_export)
    assert not _press(window, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
    assert window._offset == 0


def test_a_running_scan_swallows_navigation_keys(window, tmp_path, monkeypatch):
    _opened(window, tmp_path)
    _focus(monkeypatch, window.block_export)
    window._scanning = True
    assert _press(window, Qt.Key.Key_Down)
    assert window._offset == 0


# --- the visit trail ------------------------------------------------------


def test_back_and_forward_walk_the_entries_visited(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    assert window._entry is block
    assert window._back_action.isEnabled()
    assert not window._forward_action.isEnabled()
    window._history_step(-1)
    assert window._entry is file_entry
    assert window._forward_action.isEnabled()
    window._history_step(1)
    assert window._entry is block


def test_walking_the_trail_does_not_rewrite_it(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    window._history_step(-1)
    window._history_step(1)
    window._history_step(-1)
    assert window._entry is file_entry
    assert window._history == [(file_entry, None), (block, None)]


def test_visiting_somewhere_new_drops_the_forward_tail(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    first = add_block(window, file_entry, "first", RangeSource(0, 8))
    window._history_step(-1)
    second = add_block(window, file_entry, "second", RangeSource(8, 16))
    assert window._history == [(file_entry, None), (second, None)]
    assert first is not None
    assert not window._forward_action.isEnabled()


def test_a_string_under_a_block_is_a_visit_of_its_own(window, tmp_path):
    """Opening a string confines the view to its bytes, which is as much a
    place as the block, and the click is one step of the trail, not two."""
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    window._activate_entry(file_entry)
    window._show_string(block, 1)
    assert window._history[-1] == (block, 1)
    assert window._history == [
        (file_entry, None),
        (block, None),
        (file_entry, None),
        (block, 1),
    ]
    window._history_step(-1)
    assert "string 1" in window._forward_action.toolTip()  # named in the menu
    assert window._entry is file_entry
    window._history_step(1)
    assert window._entry is block and window._bounds == window._string_bounds
    assert window._history_pos == 3  # walking it did not rewrite it


def test_back_leaves_a_string_for_the_block_it_is_under(window, tmp_path):
    """The block is already on screen, so leaving its string cannot activate
    it: the view is confined to the block's source again in its place."""
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    source = window._bounds
    window._show_string(block, 1)
    assert window._bounds != source
    window._history_step(-1)
    assert window._entry is block and window._bounds == source
    assert window._history_pos == 1
    window._history_step(1)
    assert window._bounds == window._string_bounds != source


def test_walking_onto_a_string_selects_its_row_in_the_files_panel(window, tmp_path):
    """Back and Forward land on strings, and the panel's selection follows the
    view there and out again, opening the block to show the row."""
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    panel = window.files_panel
    window._show_string(block, 1)
    item = panel._items[id(block)]
    assert item.isExpanded()
    assert panel.string_of(panel.tree.selectedItems()[0]) == (block, 1)
    window._history_step(-1)
    assert panel.tree.selectedItems() == [item]
    item.setExpanded(False)
    window._history_step(1)
    assert item.isExpanded()
    assert panel.string_of(panel.tree.selectedItems()[0]) == (block, 1)


def test_changing_the_table_keeps_the_string_row_selected(window, tmp_path):
    """Re-reading the block builds its string rows afresh, and the row for the
    string on screen goes with the old ones: the panel selects it again, as the
    Strings grid keeps the string it had selected."""
    file_entry = _opened(window, tmp_path)
    other = tmp_path / "other.tbl"
    other.write_text(f"{HEADER}\n@table other\n41=a\n/00=[end]\n")
    window.open_table(str(other))
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    panel = window.files_panel
    window._show_string(block, 1)
    window._choose_table("other")
    assert block.config.table_id == "other"
    assert panel.string_of(panel.tree.selectedItems()[0]) == (block, 1)


def test_opening_a_project_starts_the_trail_on_its_current_entry(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert window.open_project(str(proj))
    current = window.workspace.current
    assert current is not None and current.name == block.name
    assert window._history == [(current, None)]
    loaded_file = window.workspace.files()[0]
    window._show_entry(loaded_file)
    window._history_step(-1)
    assert window._entry is current


def test_closing_a_block_forgets_the_visits_to_its_strings(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    window._show_string(block, 0)
    window._forget_visits(block)
    assert window._history == [(file_entry, None)]


def test_nothing_to_go_back_to_disables_the_action(window, tmp_path):
    assert not window._back_action.isEnabled()
    assert "Nothing to go back to" in window._back_action.toolTip()
    _opened(window, tmp_path)
    assert not window._back_action.isEnabled()  # one visit is not a trail


def test_closing_an_entry_forgets_its_visits(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(
        QMessageBox,
        "question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes),
    )
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    window._remove_entries([block])
    assert block not in window.workspace.entries
    assert all(e is not block for e, _ in window._history)
    assert not window._back_action.isEnabled()


def test_starting_a_project_wipes_the_trail(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    add_block(window, file_entry, "b", RangeSource(0, 8))
    assert window._history
    window._new_project()
    assert window._history == [] and window._history_pos == -1
    assert not window._back_action.isEnabled()


def test_a_reorder_keeps_the_trail(window, tmp_path):
    """A reorder fires the workspace's ``on_reset`` too, and is not a swap."""
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    window.workspace.reorder(block, 0)
    assert window._history == [(file_entry, None), (block, None)]


def test_only_entries_that_can_be_the_view_enter_the_trail(window, tmp_path):
    """A table opens in the Table Editor and a bookmark jumps somewhere else,
    so neither is a place Back could return to."""
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    table_entry = window.workspace.entry_for_table("main")
    window._activate_entry(table_entry)
    assert window._history == [(file_entry, None), (block, None)]
    assert window._entry is block  # and the view never left
    assert window._entry is block and window.workspace.current is block
    window._new_bookmark()
    bookmark = window.workspace.entries[-1]
    window._activate_entry(bookmark)
    assert window._history == [(file_entry, None), (block, None)]


def test_re_activating_the_entry_on_screen_keeps_the_position(window, tmp_path):
    """Several paths ask for it — an undo reaching its entry, a row clicked
    twice — and doing the work anyway would capture and restore the session over
    itself."""
    file_entry = _opened(window, tmp_path)
    window._go_to(0x20)
    window._activate_entry(file_entry)
    assert window._offset == 0x20
    assert window._history == [(file_entry, None)]


def test_the_back_mouse_button_steps_the_trail(window, tmp_path):
    file_entry = _opened(window, tmp_path)
    block = add_block(window, file_entry, "b", RangeSource(0, 8))
    assert block is not None
    event = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(0, 0),
        QPointF(0, 0),
        Qt.MouseButton.BackButton,
        Qt.MouseButton.BackButton,
        Qt.KeyboardModifier.NoModifier,
    )
    assert window._handle_history_mouse(event)
    assert window._entry is file_entry
