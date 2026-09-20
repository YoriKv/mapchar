"""The Hex dock: its dump, its overtype line, its find field, and what it
remembers between runs.
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from mapchar.core.bits import format_hex_bytes, parse_hex_bytes
from mapchar.ui import BYTES_PER_ROW, settings
from mapchar.ui.hex_panel import FOLLOW_SELECTION_KEY, HexPanel
from window_helpers import make_window, open_rom_and_table

DATA = bytes(range(64)) + b"ABCABC" + bytes(16)


def test_a_byte_run_is_spelled_as_upper_case_pairs_and_reads_back():
    assert format_hex_bytes(b"\x00\x0a\xff") == "00 0A FF"
    assert format_hex_bytes(b"") == ""
    assert format_hex_bytes(b"\x01\x02", sep="") == "0102"
    assert parse_hex_bytes(format_hex_bytes(DATA)) == DATA


def _dock(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, DATA)
    window._activate_entry(entry)
    window.show()
    window.hex_dock.show()
    window._sync_hex_panel()
    return entry


# --- the dump --------------------------------------------------------------


def test_the_dump_starts_at_the_view_offset(window, tmp_path):
    _dock(window, tmp_path)
    assert window.hex_panel.view.toPlainText().startswith("000000  00 01 02")


def test_the_dump_spells_the_chosen_address_format(window, tmp_path):
    _dock(window, tmp_path)
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    window._sync_hex_panel()
    text = window.hex_panel.view.toPlainText()
    assert text.startswith("$00:8000  00 01 02")
    # The wider column moves the hex cells with it, so the caret arithmetic that
    # finds a byte under the cursor stays right.
    assert window.hex_panel._hex_start == len("$00:8000") + 2


def test_both_byte_views_size_and_spell_one_address_column(window, tmp_path):
    """The dump and the raw view read the same file, so their address columns
    are the same width and the same spelling — they were written twice and
    disagreed, four digits in one and six in the other."""
    _dock(window, tmp_path)
    assert window.hex_panel._addr_width == window.raw._addr_width == 6
    assert window.raw._addr_of(0x20) == window.hex_panel._addr_of(0x20) == "000020"
    window.address_pick.setCurrentIndex(window.address_pick.findData("snes-lorom"))
    window._sync_hex_panel()
    assert window.hex_panel._addr_width == window.raw._addr_width == len("$00:8000")
    assert window.raw._addr_of(0x20) == window.hex_panel._addr_of(0x20) == "$00:8020"


def test_the_dump_follows_the_selection(window, tmp_path):
    _dock(window, tmp_path)
    assert window.hex_panel.follow.isChecked()
    window._on_selection(0x20, 0x24)
    assert window.hex_panel.view.toPlainText().startswith("000020  20 21 22")


def test_the_selection_is_tinted_in_both_columns(window, tmp_path):
    """Two rows' worth from mid-row: each row's hex cells and ASCII cells, and
    the tint stays when the caret moves."""
    _dock(window, tmp_path)
    window._on_selection(0x1E, 0x22)
    panel = window.hex_panel
    spans = [
        (h.cursor.selectionStart(), h.cursor.selectionEnd())
        for h in panel.view.extraSelections()
    ]
    line, hex_at, ascii_at = panel._line_len, panel._hex_start, panel._ascii_start
    row = (0x10 - panel._offset) // BYTES_PER_ROW
    assert spans == [
        (row * line + hex_at + 14 * 3, row * line + hex_at + 16 * 3 - 1),
        (row * line + ascii_at + 14, row * line + ascii_at + 16),
        ((row + 1) * line + hex_at, (row + 1) * line + hex_at + 2 * 3 - 1),
        ((row + 1) * line + ascii_at, (row + 1) * line + ascii_at + 2),
    ]
    _caret_to(panel, 0)
    assert len(panel.view.extraSelections()) == 4
    window._on_selection(-1, -1)
    assert panel.view.extraSelections() == []


def test_follow_off_leaves_the_dump_where_it_was(window, tmp_path):
    _dock(window, tmp_path)
    window.hex_panel.follow.setChecked(False)
    window._on_selection(0x20, 0x24)
    assert window.hex_panel.view.toPlainText().startswith("000000  00 01 02")


# --- overtyping ------------------------------------------------------------


def _caret_to(panel, byte_offset, nibble=0):
    cursor = panel.view.textCursor()
    row, col = divmod(byte_offset - panel._offset, BYTES_PER_ROW)
    cursor.setPosition(row * panel._line_len + panel._hex_start + col * 3 + nibble)
    panel.view.setTextCursor(cursor)


def test_an_overtype_run_keeps_the_caret_moving_with_follow_on(window, tmp_path, qtbot):
    """The bug: every edit re-renders the dock, and the render used to re-seat
    the caret on the raw view's selection — so the second digit of a byte landed
    back on the first one, and a run of overtypes was impossible."""
    entry = _dock(window, tmp_path)
    window._on_selection(0, 1)  # a live selection, which Follow is watching
    assert window.hex_panel.follow.isChecked()
    panel = window.hex_panel
    _caret_to(panel, 2)
    qtbot.keyClick(panel.view, Qt.Key.Key_F)
    assert entry.doc.data[2] == 0xF2
    qtbot.keyClick(panel.view, Qt.Key.Key_A)
    assert entry.doc.data[2] == 0xFA
    # And on to the next byte rather than back onto the one just typed.
    qtbot.keyClick(panel.view, Qt.Key.Key_1)
    assert entry.doc.data[3] == 0x13


def test_each_overtyped_nibble_is_its_own_undo_step(window, tmp_path, qtbot):
    entry = _dock(window, tmp_path)
    panel = window.hex_panel
    _caret_to(panel, 2)
    qtbot.keyClick(panel.view, Qt.Key.Key_F)
    qtbot.keyClick(panel.view, Qt.Key.Key_A)
    assert entry.doc.data[2] == 0xFA and entry.dirty
    window.undo_stack.undo()
    assert entry.doc.data[2] == 0xF2
    window.undo_stack.undo()
    assert entry.doc.data[2] == 0x02 and not entry.dirty


def test_the_bytes_line_writes_a_run(window, tmp_path):
    entry = _dock(window, tmp_path)
    window.hex_panel.at.setText("4")
    window.hex_panel.bytes.setText("AA BB")
    window.hex_panel._on_apply()
    assert entry.doc.data[4:6] == b"\xaa\xbb"


def test_a_non_hex_run_writes_nothing(window, tmp_path):
    entry = _dock(window, tmp_path)
    before = entry.doc.data
    window.hex_panel.at.setText("4")
    window.hex_panel.bytes.setText("zz")
    window.hex_panel._on_apply()
    assert entry.doc.data == before


def test_hex_panel_overtypes_in_place(window, tmp_path, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor

    rom = tmp_path / "h.bin"
    rom.write_bytes(bytes(range(32)))
    file_entry = window.open_rom(str(rom))
    window.show()
    window.hex_dock.show()
    window.hex_panel.follow.setChecked(False)
    window._sync_hex_panel()
    view = window.hex_panel.view
    cursor = view.textCursor()
    cursor.setPosition(8 + 3 * 2)  # first digit of byte 2
    view.setTextCursor(cursor)
    qtbot.keyClick(view, Qt.Key.Key_F)
    assert file_entry.doc.data[2] == 0xF2
    qtbot.keyClick(view, Qt.Key.Key_A)
    assert file_entry.doc.data[2] == 0xFA and file_entry.dirty
    assert view.textCursor().position() == 8 + 3 * 3
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert file_entry.doc.data[2] == 2
    assert isinstance(cursor, QTextCursor)


# --- finding ---------------------------------------------------------------


def test_find_next_and_previous_walk_the_matches(window, tmp_path):
    _dock(window, tmp_path)
    window.hex_panel.find.setText("41 42 43")
    window.hex_panel.find_row.search(backwards=False)
    assert window._offset <= 64 and window._selection == (64, 67)
    window.hex_panel.find_row.search(backwards=False)
    assert window._selection == (67, 70)
    window.hex_panel.find_row.search(backwards=True)
    assert window._selection == (64, 67)
    # The Find bar took the search over, so F3 walks on from the panel's find.
    assert window.find_row.text() == "41 42 43"
    window._find_bytes(again=True)
    assert window._selection == (67, 70)


def test_find_previous_wraps_to_the_last_match(window, tmp_path):
    _dock(window, tmp_path)
    window._go_to(0)
    window.hex_panel.find.setText("41 42 43")
    window.hex_panel.find_row.search(backwards=True)
    assert window._selection == (67, 70)


def test_shift_return_in_the_find_field_searches_backwards(window, tmp_path, qtbot):
    """What the ◀ button's tooltip promises. ``returnPressed`` carries no
    modifiers, so the field's key presses are filtered instead."""
    _dock(window, tmp_path)
    window._go_to(0)
    window.hex_panel.find.setText("41 42 43")
    qtbot.keyClick(
        window.hex_panel.find, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier
    )
    assert window._selection == (67, 70)  # the last match, wrapped to
    qtbot.keyClick(window.hex_panel.find, Qt.Key.Key_Return)
    assert window._selection == (64, 67)  # forward from there, wrapped again


def test_an_empty_needle_finds_nothing(window, tmp_path):
    _dock(window, tmp_path)
    window.hex_panel.find.setText("   ")
    window.hex_panel.find_row.search(backwards=False)
    assert window._selection is None


# --- what it remembers -----------------------------------------------------


def test_follow_selection_is_remembered(window, tmp_path, qtbot, monkeypatch):
    _dock(window, tmp_path)
    window.hex_panel.follow.setChecked(False)
    assert settings().value(FOLLOW_SELECTION_KEY) in (False, "false")
    assert not HexPanel().follow.isChecked()
    other = make_window(qtbot, monkeypatch)
    assert not other.hex_panel.follow.isChecked()
