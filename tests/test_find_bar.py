"""The Find bar along the window's bottom: the current search, and the keys
and buttons that walk its matches."""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from window_helpers import make_window, open_rom_and_table

DATA = bytes(range(64)) + b"ABCABC" + bytes(16)


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _open(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, DATA)
    window._activate_entry(entry)
    window.show()
    return entry


def test_the_bar_sits_along_the_bottom_and_is_gated_with_search(window, tmp_path):
    assert window.toolBarArea(window.find_bar) == Qt.ToolBarArea.BottomToolBarArea
    assert not window.find_bar.isMovable() and not window.find_bar.isFloatable()
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
    assert window._selection == (67, 70)  # the last match, wrapped to
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
