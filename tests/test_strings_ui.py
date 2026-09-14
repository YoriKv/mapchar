"""The editing surfaces: code-aware find and replace, the translation editor,
the font and table editors' undo, and the live draft readout."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QMessageBox

from mapchar.core.block import RangeSource
from mapchar.core.font import TextBox
from mapchar.engines import scriptfind
from mapchar.project.formats.table_native import HEADER
from mapchar.ui.main_window import MainWindow
from mapchar.ui.strings_view import (
    COL_NOTES,
    COL_TRANSLATION,
    CodeEditor,
    CodeInfo,
    StringsView,
)
from window_helpers import add_block, open_rom_and_table

TABLE = (
    f"{HEADER}\n@table main\n41=A\n42=B\n43=C\nFE=[line]\n$FD=[color],u8\n/00=[end]\n"
)
"""Three letters, a line code, a code with an operand, and an end token."""


@pytest.fixture
def window(qtbot, monkeypatch):
    """A window whose modals answer themselves: one left open is a hang.

    The three-way "unsaved edits" gate is a box with its own labels rather
    than a standard question, so it is answered by taking its destructive
    button; errors are collected for a test to read.
    """
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard),
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    errors: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _p, _t, message, *a, **k: errors.append(message)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(
            (
                b
                for b in self.buttons()
                if self.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole
            ),
            None,
        ),
    )
    w = MainWindow()
    w.errors = errors
    qtbot.addWidget(w)
    return w


def block_with(window, tmp_path, data, name="b", stop=None):
    entry = open_rom_and_table(window, tmp_path, data, table=TABLE)
    return entry, add_block(
        window, entry, name, RangeSource(0, stop if stop else len(data))
    )


# --- the matcher ----------------------------------------------------------


def test_pieces_keep_codes_whole():
    assert scriptfind.piece_spans("A[line]B") == [(0, 1), (1, 7), (7, 8)]
    assert scriptfind.piece_spans("\\[x") == [(0, 2), (2, 3)]
    # An unclosed '[' is the one piece being typed.
    assert scriptfind.piece_spans("A[li") == [(0, 1), (1, 4)]


def test_find_never_reaches_inside_a_code():
    # The plain substring replace this fixes turned [line] into [lene].
    assert scriptfind.find("A[line]B", "line") is None
    assert scriptfind.replace("A[line]B", "line", "ene") == ("A[line]B", 0)
    assert scriptfind.find("A[line]B", "[line]") == (1, 7)
    assert scriptfind.replace("A[line]B[line]", "[line]", "") == ("AB", 2)
    assert scriptfind.find("ABAB", "AB", start=1) == (2, 4)
    assert scriptfind.find("abc", "B") is None
    assert scriptfind.find("abc", "B", case=False) == (1, 2)
    assert scriptfind.replace("A[color $03]", "[color $03]", "X") == ("AX", 1)


# --- the translation editor ------------------------------------------------


def key(editor, code, modifier=Qt.KeyboardModifier.NoModifier):
    editor.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, code, modifier))


def test_shift_return_writes_the_newline_code(qtbot):
    editor = CodeEditor([CodeInfo("line")], "[line]")
    qtbot.addWidget(editor)
    editor.setPlainText("A")
    editor.moveCursor(QTextCursor.MoveOperation.End)
    key(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
    # Never a literal newline: parse_text drops those on the way back in.
    assert editor.toPlainText() == "A[line]"
    committed = []
    editor.commit.connect(lambda: committed.append(True))
    key(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    assert committed == [True] and editor.toPlainText() == "A[line]"


def test_completion_lists_operand_shapes(qtbot):
    codes = [CodeInfo("color", "u8"), CodeInfo("line")]
    editor = CodeEditor(codes, "[line]")
    qtbot.addWidget(editor)
    assert codes[0].completion == "[color u8]" and codes[0].insertion == "[color "
    editor._insert_completion("[color u8]")
    assert editor.toPlainText() == "[color "


def test_code_buttons_rank_by_use_in_the_block(qtbot):
    view = StringsView()
    qtbot.addWidget(view)
    view.set_codes(
        [CodeInfo("aaa", "", 0), CodeInfo("zzz", "", 9), CodeInfo("mmm", "", 3)]
    )
    buttons = [
        view.codes_layout.itemAt(i).widget().text()
        for i in range(view.codes_layout.count())
    ]
    assert buttons == ["[zzz]", "[mmm]", "[aaa]"]


def test_columns_hide_and_reorder(qtbot):
    view = StringsView()
    qtbot.addWidget(view)
    assert view.table.horizontalHeader().sectionsMovable()
    menu = view.column_menu()
    assert not menu.actions()[COL_TRANSLATION].isEnabled()
    menu.actions()[COL_NOTES].trigger()
    assert view.table.isColumnHidden(COL_NOTES)


# --- the window ------------------------------------------------------------


def test_replace_all_is_code_aware_over_block_or_project(window, tmp_path):
    data = b"\x41\xfe\x42\x00\x42\xfe\x41\x00" + b"\xff" * 8
    entry, first = block_with(window, tmp_path, data, stop=8)
    second = add_block(window, entry, "b2", RangeSource(0, 4))
    window._activate_entry(first)
    assert first.doc.strings[0].original_text() == "A[line]B[end]"

    # "line" is inside a code and must not be touched.
    window._fr_replace_all("line", "X", True, False)
    assert first.doc.strings[0].translation is None

    window._fr_replace_all("[line]", "[end]", True, False)
    assert first.doc.strings[0].translation == "A[end]B[end]"
    assert second.doc is None or not [
        r for r in (second.doc.strings or []) if r.translation
    ]

    window.undo_stack.undo()
    assert first.doc.strings[0].translation is None

    # Project scope reaches the block that was never opened.
    window._fr_replace_all("A", "C", True, True)
    assert first.doc.strings[0].translation == "C[line]B[end]"
    assert second.doc.strings[0].translation == "C[line]B[end]"
    # Every block it touched has unsaved edits, and the lot undoes as one step:
    # both blocks go back to the untranslated state they were in together.
    assert first.dirty and second.dirty
    window.undo_stack.undo()
    assert first.doc.strings[0].translation is None
    assert second.doc.strings[0].translation is None


def test_find_next_walks_into_the_next_block(window, tmp_path):
    data = b"\x41\x00\x42\x00" + b"\xff" * 8
    entry, first = block_with(window, tmp_path, data, stop=2)
    add_block(window, entry, "b2", RangeSource(2, 4))
    window._activate_entry(first)
    window._fr_find_next("B", True, True)
    assert window._entry.name == "b2"


def test_font_box_and_table_edits_undo_in_one_step(window, tmp_path):
    data = b"\x41\x42\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data, stop=3)
    font_entry = window.open_font(str(tmp_path / "sheet.png"))
    window._edit_font_entry(font_entry)
    before = font_entry.font

    window._on_font_changed(replace(before, cell_width=16))
    window._on_font_changed(replace(before, cell_width=12))
    assert font_entry.font.cell_width == 12
    # One field, one step: the two edits merged.
    window.undo_stack.undo()
    assert font_entry.font == before

    window._on_box_changed(TextBox(width=64))
    assert block.box.width == 64
    window.undo_stack.undo()
    assert block.box is None or block.box.width != 64

    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    was = deepcopy(table_entry.tables[0].entries)
    window.table_editor.new_line.setText("44=D")
    window.table_editor._add()
    assert "01000100" in table_entry.tables[0].entries
    window.undo_stack.undo()
    assert set(table_entry.tables[0].entries) == set(was)


def test_fill_leaves_taken_keys_alone(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    data = b"\x41\x00" + b"\xff" * 8
    block_with(window, tmp_path, data, stop=2)
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("A-Z", True))
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("41", True))
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.No
    )
    editor._fill_dialog()
    assert "left alone" in editor.status.text()
    # 41=A, 42=B and 43=C were there already and keep their own text.
    assert table_entry.tables[0].entries["01000010"].text == "B"


def test_draft_reports_bytes_used_and_the_room(window, tmp_path):
    data = b"\x41\x42\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data, stop=3)
    window.strings.select_index(0)
    window._on_draft("ABC[end]")
    assert "4 / 3 byte(s)" in window.preview_window.readout.text()
    assert "1 over" in window.preview_window.readout.text()
    window._on_draft("A[nope]")
    assert window.preview_window.readout.text()


def test_font_sheet_view_and_alphabet_tools(qtbot, tmp_path):
    from PySide6.QtGui import QImage

    from mapchar.core.font import Font
    from mapchar.ui.preview_window import PreviewWindow

    sheet = tmp_path / "sheet.png"
    image = QImage(32, 16, QImage.Format.Format_ARGB32)
    image.fill(0xFF000000)
    image.setPixelColor(2, 2, Qt.GlobalColor.white)
    assert image.save(str(sheet))

    win = PreviewWindow()
    qtbot.addWidget(win)
    fonts: list[Font] = []
    win.font_changed.connect(fonts.append)
    win.set_font(Font(str(sheet), 8, 8, 4, 0, "ABCD"))
    win.set_box(win._box, ["line"])
    win.show_string("ABq", "t")
    # The font cannot spell 'q', and the preview says so.
    assert "1 not in font: q" in win.status.text()

    # The sheet draws, and a click maps to its glyph.
    win.sheet_view.repaint()
    assert win.sheet_view.glyph_at(25, 1) == 1
    assert win.sheet_view.glyph_at(25, 40) == 5
    assert win.sheet_view.glyph_at(25, 200) is None
    win.grid.setChecked(True)
    win._paint()

    # Fill from the table, from the picked glyph.
    win.sheet_view.set_pick(4)
    win.set_table_chars("XYZ")
    win._fill_from_table()
    assert (fonts[-1].base, fonts[-1].chars) == (4, "XYZ")

    # Shift moves the alphabet one row of glyphs.
    win._shift(1)
    assert fonts[-1].base == 4
    win._shift(-1)
    assert fonts[-1].base == 0

    # The measured gap is the user's, not a constant.
    win.gap.setValue(2)
    win._measure()
    assert fonts[-1].widths[0] == 5

    win._copy_alphabet()
    win._paste_alphabet()
    assert (fonts[-1].base, fonts[-1].chars) == (0, "ABCD")

    # The sheet's own zoom and its tile/row switch.
    win.sheet_zoom.setValue(4)
    assert win.sheet_view.scale == 4
    win.sheet_mode.setCurrentIndex(1)
    assert win.sheet_view.rows


def test_fill_with_a_template(qtbot, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from mapchar.core.font import Font
    from mapchar.ui.preview_window import PreviewWindow

    win = PreviewWindow()
    qtbot.addWidget(win)
    fonts: list[Font] = []
    win.font_changed.connect(fonts.append)
    win.set_font(Font(None, 8, 8, 4))
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("0-9", True))
    win._fill_with()
    assert fonts[-1].chars == "0123456789"


def test_paste_alphabet_keeps_a_multi_character_override(qtbot):
    """Copy then paste is lossless: a ``TH`` override sorts before the run and
    must not be sliced off, nor a run character duplicated as an override."""
    from mapchar.core.font import Font
    from mapchar.ui.preview_window import PreviewWindow

    win = PreviewWindow()
    qtbot.addWidget(win)
    fonts: list = []
    win.font_changed.connect(fonts.append)
    win.set_font(Font(None, 8, 8, 16, 0x20, "AB", glyphs={"TH": 0x10}))
    win._copy_alphabet()
    win._paste_alphabet()
    f = fonts[-1]
    assert (f.base, f.chars, f.glyphs) == (0x20, "AB", {"TH": 0x10})


def test_build_table_offers_the_kana_a_hit_pinned_down(window, monkeypatch):
    """A kana hit seeds a kana table: the Build table dialog offers the same
    alphabets the Table Editor's Fill does."""
    from PySide6.QtWidgets import QInputDialog

    from mapchar.engines.relsearch import HIRAGANA, RUNS, relative_search

    data = bytes(range(0x40, 0x40 + len(RUNS[HIRAGANA])))
    hit = relative_search(data, "あいう", widths=(1,))[0]
    assert hit.bases == {HIRAGANA: 0x40}
    picked: list = []

    def getitem(_p, _t, _l, options, *a, **k):
        picked.append(list(options))
        return options[0], True

    monkeypatch.setattr(QInputDialog, "getItem", getitem)
    entries = window._hit_entries(hit)
    assert picked and "あ-ん" in picked[0]
    assert [e.text for e in entries] == list(RUNS[HIRAGANA])


def test_wrap_is_one_undo_step(window, tmp_path):
    from mapchar.core.font import CodeEffect, Effect
    from mapchar.core.font import TextBox as Box

    data = b"\x41\x42\x41\x42\x41\x42\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data, stop=7)
    font_entry = window.open_font(str(tmp_path / "sheet.png"))
    font_entry.font = replace(
        font_entry.font, chars="AB", base=1, widths=(0,) + (8,) * 8
    )
    fonts = window.workspace.fonts()
    block.box = Box(
        width=16,
        height=32,
        line_height=8,
        font_index=fonts.index(font_entry),
        effects={"line": CodeEffect(Effect.NEWLINE)},
    )
    window.strings.select_index(0)
    depth = window.undo_stack.index()
    window._wrap_selected()
    assert window.undo_stack.index() == depth + 1
    assert "[line]" in (block.doc.strings[0].translation or "")
    window.undo_stack.undo()
    assert block.doc.strings[0].translation is None


def test_a_table_keeps_its_identity_through_undo(window, tmp_path):
    """The Table Editor and every view hold the same ``Table`` object, so an
    undo restores its contents rather than swapping the object out."""
    data = b"\x41\x00" + b"\xff" * 8
    block_with(window, tmp_path, data, stop=2)
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    table = table_entry.tables[0]
    window.table_editor.new_line.setText("44=D")
    window.table_editor._add()
    assert "01000100" in table.entries
    window.undo_stack.undo()
    assert table_entry.tables[0] is table
    assert "01000100" not in table.entries
    window.undo_stack.redo()
    assert table_entry.tables[0] is table and "01000100" in table.entries


# --- the raw view's text column --------------------------------------------


def _raw_widget(qtbot, tokens, data):
    from mapchar.ui.raw_widget import RawWidget, RowModel

    widget = RawWidget()
    qtbot.addWidget(widget)
    widget.set_model(RowModel(0, data, tokens, set(), len(data)))
    widget.resize(1400, 200)
    return widget


def _text_entry(key: str, text: str, kind=None):
    from mapchar.core.table import Entry as TableEntry
    from mapchar.core.table import TokenKind

    return TableEntry(key, kind or TokenKind.TEXT, text)


def test_a_byte_is_the_same_byte_under_either_column(qtbot):
    """Every hex cell, group gaps included, and every text cell names its own
    byte, whatever the font's fractional width."""
    from mapchar.ui import BYTES_PER_ROW
    from mapchar.ui.widgets import MONO_FAMILIES

    widget = _raw_widget(qtbot, [], bytes(BYTES_PER_ROW * 2))
    # The fallback list is what keeps a Japanese decode from being boxes; the
    # face that answers depends on the machine, so only the list is asserted.
    assert "Noto Sans CJK JP" in MONO_FAMILIES
    assert widget._font.families() == list(MONO_FAMILIES)
    for rel in range(BYTES_PER_ROW * 2):
        for cell in (widget._hex_cell(rel), widget._text_cell(rel)):
            assert widget._byte_at(cell.center()) == rel
            assert widget._byte_at(cell.topLeft()) == rel


def test_every_hex_pair_is_drawn_in_its_own_cell(qtbot):
    """The font's true advance is fractional, so a row drawn as one string
    drifts off the cells its tints fill; each pair is placed in its cell."""
    from PySide6.QtGui import QPainter

    from mapchar.ui import BYTES_PER_ROW

    widget = _raw_widget(qtbot, [], bytes(range(BYTES_PER_ROW)))
    placed = []
    original = QPainter.drawText

    def spy(painter, *args):
        placed.append(args)
        return original(painter, *args)

    QPainter.drawText = spy
    try:
        widget.viewport().grab()
    finally:
        QPainter.drawText = original
    cells = {args[2]: args[0] for args in placed if len(args) == 3}
    for rel in range(BYTES_PER_ROW):
        assert cells[f"{rel:02X}"] == widget._hex_cell(rel)


def test_bit_packed_tokens_sit_in_the_byte_holding_most_of_their_bits(qtbot):
    """Four 6-bit codes fill three bytes: each sits inside the cell of the byte
    holding most of its bits, so the second code shares the second byte with
    the third rather than straddling the first two."""
    from PySide6.QtCore import QRectF

    from mapchar.core.tokens import Token

    tokens = [
        Token(f"{i:06b}", 6 * i, 6 * i + 6, _text_entry(f"{i:06b}", "かきくけ"[i]))
        for i in range(4)
    ]
    widget = _raw_widget(qtbot, tokens, bytes(3))
    places = [widget._text_segments(t, 3) for t in tokens]
    assert all(len(p) == 1 for p in places)
    rects = [p[0] for p in places]
    cells = [QRectF(widget._text_cell(rel)) for rel in range(3)]
    half = cells[1].width() / 2
    assert rects[0] == cells[0]
    assert rects[1] == cells[1].adjusted(0, 0, -half, 0)
    assert rects[2] == cells[1].adjusted(half, 0, 0, 0)
    assert rects[3] == cells[2]


def test_a_token_that_shows_nothing_takes_no_share_of_its_byte(qtbot):
    """A table switch between two codes is a zero-width place at the boundary,
    and the codes split the byte between themselves."""
    from PySide6.QtCore import QRectF

    from mapchar.core.table import TokenKind
    from mapchar.core.tokens import Token

    tokens = [
        Token("0000", 0, 4, _text_entry("0000", "か")),
        Token("", 4, 4, _text_entry("", "", TokenKind.SWITCH)),
        Token("0001", 4, 8, _text_entry("0001", "き")),
    ]
    widget = _raw_widget(qtbot, tokens, bytes(1))
    cell = QRectF(widget._text_cell(0))
    first, switch, second = (widget._text_segments(t, 1) for t in tokens)
    assert first == [cell.adjusted(0, 0, -cell.width() / 2, 0)]
    assert switch == [QRectF(cell.center().x(), cell.top(), 0, cell.height())]
    assert second == [cell.adjusted(cell.width() / 2, 0, 0, 0)]


def test_a_token_over_a_row_end_is_placed_on_both_rows(qtbot):
    from mapchar.core.tokens import Token
    from mapchar.ui import BYTES_PER_ROW

    last = BYTES_PER_ROW - 1
    token = Token("0" * 16, last * 8, (last + 2) * 8, _text_entry("0" * 16, "漢"))
    widget = _raw_widget(qtbot, [token], bytes(BYTES_PER_ROW * 2))
    first, second = widget._text_segments(token, BYTES_PER_ROW * 2)
    assert first == widget._text_cell(last)
    assert second == widget._text_cell(BYTES_PER_ROW)


def test_the_text_column_shows_names_as_labels_and_marks_inside_text():
    from mapchar.core.table import TokenKind
    from mapchar.core.tokens import Token
    from mapchar.ui.raw_widget import display_text

    def shown(text, kind=None):
        return display_text(Token("0" * 8, 0, 8, _text_entry("0" * 8, text, kind)))

    assert shown("[end]\\n", TokenKind.END) == ("end", True)
    assert shown("[tile60]") == ("tile60", True)
    assert shown("s[line]\\n") == ("s↵", False)
    assert shown("[F6]の") == ("▪の", False)
    assert shown("A") == ("A", False)
    assert display_text(Token("11111111", 0, 16)) == ("·", False)


def test_text_wider_than_its_cells_never_leaves_them(qtbot):
    """A dictionary word on one byte is squeezed and cut to its own cell, and
    says it was cut; a letter that fits is drawn whole."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    widget = _raw_widget(qtbot, [], bytes(1))
    cell = QRectF(widget._text_cell(0))
    image = QImage(400, 100, QImage.Format.Format_ARGB32)
    painter = QPainter(image)
    painter.setFont(widget._font)
    try:
        assert not widget._fit(painter, cell, "A")
        assert widget._fit(painter, cell, "ちからのたね")
        assert painter.clipBoundingRect() == QRectF()  # the clip was restored
    finally:
        painter.end()


def test_one_character_too_wide_is_condensed_rather_than_sliced(qtbot):
    """A single glyph wider than its cell even at MIN_SQUEEZE has nothing left
    to drop, so it is condensed the rest of the way and says it was cut —
    rather than drawn over the cell's edge and sliced there by the clip."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter

    from mapchar.ui.raw_widget import MIN_SQUEEZE

    widget = _raw_widget(qtbot, [], bytes(1))
    image = QImage(400, 100, QImage.Format.Format_ARGB32)
    image.fill(0)
    painter = QPainter(image)
    painter.setFont(widget._font)
    try:
        # A cell far too narrow for one character, wherever the faces come from.
        wide = painter.fontMetrics().horizontalAdvance("W")
        cell = QRectF(40, 0, wide * MIN_SQUEEZE / 2, widget.row_height)
        assert widget._fit(painter, cell, "W")
    finally:
        painter.end()
    painted = [
        x
        for x in range(image.width())
        for y in range(image.height())
        if image.pixelColor(x, y).alpha() > 8
    ]
    # Ink inside the cell, and none of it touching either edge: a glyph that
    # was condensed to fit, not one the clip cut off at the boundary.
    assert painted, "the character was drawn"
    assert cell.left() < min(painted) and max(painted) < cell.right()
