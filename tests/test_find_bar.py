"""The Find bar under the navigation row: the current search, and the keys
and buttons that walk its matches."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from mapchar.core.block import RangeSource
from window_helpers import add_block, make_window, open_rom_and_table

DATA = bytes(range(64)) + b"ABCABC" + bytes(8) + b"ABC" + bytes(5)
"""Two matches of ``ABC`` at 64 and 67, and a third at 78."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _open(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, DATA)
    window._activate_entry(entry)
    window.show()
    return entry


def test_the_bar_is_the_last_row_of_the_editing_column_and_gated_with_search(
    window, tmp_path
):
    column = window.centralWidget().layout()
    rows = [column.itemAt(i).widget() for i in range(column.count())]
    assert rows[-1] is window.find_bar and rows[-2] is window.nav_bar
    assert not window.find_bar.isEnabled()  # nothing open, nothing to search
    _open(window, tmp_path)
    assert window.find_bar.isEnabled()


def test_find_next_and_previous_walk_what_the_bar_says(window, tmp_path):
    _open(window, tmp_path)
    window.find_row.set_text("41 42 43")
    window._find_bytes(again=True)  # F3
    assert window._selection == (64, 67)
    window._find_bytes(again=True)
    assert window._selection == (67, 70)
    window._find_bytes(again=True, backwards=True)  # Shift+F3
    assert window._selection == (64, 67)
    window.find_row.next.click()
    assert window._selection == (67, 70)
    window.find_row.previous.click()
    assert window._selection == (64, 67)


def test_enter_and_shift_enter_in_the_bar_search_either_way(window, tmp_path, qtbot):
    _open(window, tmp_path)
    window._go_to(0)
    window.find_row.set_text("41 42 43")
    qtbot.keyClick(
        window.find_row.field, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )
    assert window._selection == (78, 81)  # the last match, wrapped to
    qtbot.keyClick(window.find_row.field, Qt.Key.Key_Return)
    assert window._selection == (64, 67)  # forward from there, wrapped again


def test_ctrl_f_puts_the_keyboard_in_the_bar_with_the_search_selected(window, tmp_path):
    _open(window, tmp_path)
    window.find_row.set_text("41 42 43")
    window._find_bytes()
    assert window.focusWidget() is window.find_row.field
    assert window.find_row.field.selectedText() == "41 42 43"


def test_find_next_with_an_empty_bar_asks_for_a_search(window, tmp_path):
    _open(window, tmp_path)
    window._find_bytes(again=True)
    assert window._selection is None
    assert window.focusWidget() is window.find_row.field


def test_search_for_selection_spells_the_bytes_into_the_bar(window, tmp_path):
    _open(window, tmp_path)
    window._select_bytes(64, 3)
    window._search_selection()
    assert window.find_row.text() == "41 42 43"
    assert window._selection == (67, 70)


def test_a_search_that_matches_nothing_says_so(window, tmp_path):
    _open(window, tmp_path)
    window.find_row.set_text("FE FE FE")
    window._find_bytes(again=True)
    assert window._selection is None
    assert window.statusBar().currentMessage() == "Not found"


def test_inside_a_block_the_search_stays_in_its_bounds(window, tmp_path):
    """A hit outside a block's source would widen the view to the file, so the
    search never looks there: it wraps within the block."""
    file_entry = _open(window, tmp_path)
    add_block(window, file_entry, "b", RangeSource(64, 70))
    assert window._bounds == (64, 70)
    window.find_row.set_text("41 42 43")
    window._find_bytes(again=True)
    assert window._selection == (64, 67)
    window._find_bytes(again=True)
    assert window._selection == (67, 70)
    window._find_bytes(again=True)
    assert window._selection == (64, 67)  # wrapped inside, not to 78
    window._find_bytes(again=True, backwards=True)
    assert window._selection == (67, 70)
    assert window._bounds == (64, 70)
    # A match the block's bytes hold only part of is not the block's.
    window.find_row.set_text("43 00")
    window._find_bytes(again=True)
    assert window._selection == (67, 70)
    assert window.statusBar().currentMessage() == "Not found"
    # The file, never confined, is searched whole.
    window._activate_entry(file_entry)
    window._go_to(0)
    window._find_bytes(again=True)
    assert window._selection == (69, 71) and window._bounds is None
