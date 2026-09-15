"""The view confined to a stretch of the file — a block's source, one string —
and the strings listed under each block in the Files panel, which is where
that confinement is asked for."""

from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from helpers import pointer_rom
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    source_span,
)
from mapchar.core.capabilities import Capability
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui import BYTES_PER_ROW
from mapchar.ui.entry_text import PREVIEW_CHARS, string_preview
from window_helpers import ASCII_TABLE, add_block, make_window, open_rom_and_table

DATA = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 0x40
"""Two strings in the first six bytes, then filler."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _shown(window):
    window.resize(900, 500)
    window.show()
    QApplication.processEvents()


# --- what a source spans, without a window ----------------------------------


def test_source_span_is_the_bytes_the_source_itself_occupies():
    assert source_span(RangeSource(0x10, 0x20)) == (0x10, 0x20)
    assert source_span(PointerTableSource(0x40, 0x48, 2, 2)) == (0x40, 0x48)
    # A pointer list spans its pointers, from the lowest to the end of the highest.
    assert source_span(PointerListSource((0x30, 0x20), size=2)) == (0x20, 0x32)
    assert source_span(PointerListSource((), size=2)) is None
    assert source_span(RangeSource(0x10, 0x10)) is None
    assert source_span(None) is None


def test_a_preview_is_one_line_cut_short_with_an_ellipsis():
    assert string_preview("Hello[line]\nworld[end]") == "Hello[line] world[end]"
    long = "x" * (PREVIEW_CHARS + 10)
    cut = string_preview(long)
    assert len(cut) == PREVIEW_CHARS and cut.endswith("…")
    assert string_preview("") == "(empty)"


# --- a block opens on its source ---------------------------------------------


def test_a_block_opens_confined_to_its_source(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    _shown(window)
    assert window._bounds is None
    add_block(window, file_entry, "b", RangeSource(0, 6))
    assert window._bounds == (0, 6)
    assert window._offset == 0
    # The Hex tab holds those bytes and no more, on a scrollbar over them.
    assert len(window.raw._model.data) == 6
    assert window.raw.verticalScrollBar().maximum() == 0
    assert "viewing 000000–000005 (6 bytes)" in window.nav_status.text()
    # The file itself is never confined.
    window._activate_entry(file_entry)
    assert window._bounds is None
    assert len(window.raw._model.data) > 6


def test_a_pointer_table_block_opens_on_its_pointers(window, tmp_path):
    rom = pointer_rom([0x10, 0x13], "41 42 00 42 41 00", at=0x10)
    file_entry = open_rom_and_table(window, tmp_path, rom)
    add_block(window, file_entry, "p", PointerTableSource(0, 4, 2, 2))
    assert window._bounds == (0, 4)
    assert len(window._doc.strings) == 2
    assert len(window.raw._model.data) == 4


def test_home_end_and_the_steps_stay_inside_the_bounds(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(range(256)) * 4)
    _shown(window)
    add_block(window, file_entry, "b", RangeSource(0x100, 0x180))
    assert window._offset == 0x100
    window._move(-1)
    assert window._offset == 0x100
    window._go_end()
    assert window._offset == max(0x100, 0x180 - window.raw.visible_bytes())
    window._move(BYTES_PER_ROW * 100)
    assert window._offset == 0x17F
    window._go_home()
    assert window._offset == 0x100
    assert window._bounds == (0x100, 0x180)
    # The scrollbar's rows are the bounds', and a drag stays inside them.
    bar = window.raw.verticalScrollBar()
    bar.setValue(bar.maximum())
    assert 0x100 <= window._offset < 0x180
    assert window._bounds == (0x100, 0x180)


def _steps_enabled(window) -> bool:
    states = {b.isEnabled() for b in window.step_buttons}
    assert len(states) == 1
    return states.pop()


def _press(window, key) -> bool:
    return window._handle_nav_key(
        QKeyEvent(QEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
    )


def test_the_steps_are_off_while_the_whole_range_fits_in_view(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(range(256)) * 4)
    _shown(window)
    assert _steps_enabled(window)
    add_block(window, file_entry, "b", RangeSource(0x100, 0x300))
    assert _steps_enabled(window)
    # One row of bytes: a step down could only hide some of them.
    add_block(window, file_entry, "c", RangeSource(0x200, 0x208))
    assert window._bounds == (0x200, 0x208)
    assert not _steps_enabled(window)
    window.raw.setFocus()  # a focused tree would spend the arrows itself
    for key in (Qt.Key.Key_Down, Qt.Key.Key_PageDown, Qt.Key.Key_Right, Qt.Key.Key_End):
        assert _press(window, key)  # swallowed, not passed to the focused widget
        assert window._offset == 0x200
    assert not _press(window, Qt.Key.Key_A)
    # The file itself brings the whole of it, and the steps, back.
    window._activate_entry(file_entry)
    window._go_to(0x200)
    assert _steps_enabled(window)
    assert _press(window, Qt.Key.Key_Down)
    assert window._offset == 0x210


def test_a_file_smaller_than_the_window_has_no_steps(window, tmp_path):
    open_rom_and_table(window, tmp_path, DATA)
    _shown(window)
    assert not _steps_enabled(window)
    assert window._can(Capability.NAVIGATION)


def test_a_position_outside_the_bounds_widens_the_view(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(range(256)) * 4)
    add_block(window, file_entry, "b", RangeSource(0x100, 0x180))
    window._go_to(0x300)
    assert window._bounds is None
    assert window._offset == 0x300
    assert len(window.raw._model.data) > 0


def test_a_block_left_outside_its_source_reopens_on_it(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes(range(256)) * 4)
    block = add_block(window, file_entry, "b", RangeSource(0x100, 0x180))
    window._go_to(0x300)
    window._activate_entry(file_entry)
    window._activate_entry(block)
    assert window._bounds == (0x100, 0x180)
    assert window._offset == 0x100
    # Left inside, the position is kept.
    window._move(BYTES_PER_ROW)
    window._activate_entry(file_entry)
    window._activate_entry(block)
    assert window._offset == 0x110


def test_the_text_tab_is_confined_too(window, tmp_path):
    prose = b"The quick brown fox jumps over the lazy dog. " * 40
    file_entry = open_rom_and_table(window, tmp_path, prose, table=ASCII_TABLE)
    _shown(window)
    add_block(window, file_entry, "b", RangeSource(45, 90))
    window.text_tab_action.trigger()
    QApplication.processEvents()
    text = window.text
    assert text.edit.toPlainText() == prose[45:90].decode()
    assert (text.bar.minimum(), text.bar.maximum()) == (45, 45)
    window._on_text_scroll(1)
    assert window._offset == 45


# --- the strings under a block -------------------------------------------------


def _string_rows(window, block):
    item = window.files_panel._items[id(block)]
    return [item.child(i) for i in range(item.childCount())]


def test_a_block_row_opens_to_its_strings(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    panel = window.files_panel
    item = panel._items[id(block)]
    # Closed, one stub keeps the expander and the strings are not built.
    assert not item.isExpanded()
    assert [panel.string_of(r) for r in _string_rows(window, block)] == [None]
    item.setExpanded(True)
    rows = _string_rows(window, block)
    assert [r.text(0) for r in rows] == ["0  AB[end]", "1  BA[end]"]
    assert panel.string_of(rows[1]) == (block, 1)
    assert rows[0].toolTip(0).startswith("0–2 (3 bytes)")
    # A refresh that changed nothing about the strings keeps the rows.
    panel.refresh_labels()
    assert _string_rows(window, block)[0] is rows[0]
    item.setExpanded(False)
    assert [panel.string_of(r) for r in _string_rows(window, block)] == [None]


def test_the_open_blocks_survive_a_rebuild(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    panel = window.files_panel
    panel._items[id(block)].setExpanded(True)
    panel.rebuild()
    item = panel._items[id(block)]
    assert item.isExpanded()
    assert len(_string_rows(window, block)) == 2


def test_opening_a_block_the_session_has_not_read_reads_it(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = Entry(
        EntryKind.BLOCK,
        "unread",
        file_entry.path,
        parent=file_entry,
        config=BlockConfig(RangeSource(0, 6), EndToken(), "main"),
    )
    window._push_add(block)
    assert block.doc is None
    window.files_panel._items[id(block)].setExpanded(True)
    assert block.doc is not None
    assert len(_string_rows(window, block)) == 2
    # Read for its rows, not shown: the view stayed on the file.
    assert window._entry is file_entry


def test_a_string_row_confines_the_view_to_that_string(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._activate_entry(file_entry)
    panel = window.files_panel
    panel._items[id(block)].setExpanded(True)
    row = _string_rows(window, block)[1]
    panel._on_clicked(row, 0)
    assert window._entry is block
    assert window._bounds == (3, 6)
    assert window._offset == 3
    # The view is that string alone, so none of its bytes are selected.
    assert window._selection is None and window.raw.selection() is None
    assert window.strings.selected_indices() == [1]
    assert len(window.raw._model.data) == 3
    # The row clicked is the one selected, not the block's.
    assert panel.tree.selectedItems() == [row]
    # The block's own row brings the whole source back, from its start.
    panel._on_clicked(panel._items[id(block)], 0)
    assert window._bounds == (0, 6)
    assert window._offset == 0
    assert window._entry is block


def test_an_arrow_key_shows_the_row_it_lands_on(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._activate_entry(file_entry)
    panel = window.files_panel
    panel._items[id(block)].setExpanded(True)
    panel.tree.setCurrentItem(panel._items[id(file_entry)])
    QTest.keyClick(panel.tree, Qt.Key.Key_Down)
    assert panel.tree.currentItem() is panel._items[id(block)]
    assert window._entry is block
    assert window._bounds == (0, 6)
    # On down into the strings: the same confinement a click on the row gives.
    QTest.keyClick(panel.tree, Qt.Key.Key_Down)
    assert window._bounds == (0, 3)
    assert window.strings.selected_indices() == [0]


def test_the_filter_reaches_the_strings(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    panel = window.files_panel
    item = panel._items[id(block)]
    item.setExpanded(True)
    panel.filter.setText("BA")
    rows = _string_rows(window, block)
    assert [r.isHidden() for r in rows] == [True, False]
    assert not item.isHidden() and not panel._items[id(file_entry)].isHidden()
    panel.filter.setText("nowhere")
    assert item.isHidden()
    panel.filter.setText("")
    assert not any(r.isHidden() for r in rows)
