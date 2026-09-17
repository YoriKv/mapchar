"""The editing surfaces: code-aware find and replace, the translation editor,
the font and table editors' undo, and the live draft readout."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

import pytest
from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor
from PySide6.QtWidgets import QMessageBox

from mapchar.core.block import RangeSource, Status
from mapchar.core.font import TextBox
from mapchar.engines import scriptfind
from mapchar.project.formats.table_native import HEADER
from mapchar.ui.main_window import MainWindow
from mapchar.ui.strings_view import (
    COL_NOTES,
    COL_TRANSLATION,
    CodeEditor,
    CodeInfo,
    RowData,
    StringsView,
)
from mapchar.ui.token_text import hide_codes
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
    editor.commit.connect(lambda advance: committed.append(advance))
    # Ctrl+Return commits and stays; Return commits and moves on.
    key(editor, Qt.Key.Key_Return, Qt.KeyboardModifier.ControlModifier)
    key(editor, Qt.Key.Key_Return)
    assert committed == [False, True] and editor.toPlainText() == "A[line]"


def test_completion_and_buttons_carry_the_tables_comment(qtbot):
    code = CodeInfo("color", "u8", 2, "text colour")
    assert code.completion == "[color u8]  text colour"
    editor = CodeEditor([code], "[line]")
    qtbot.addWidget(editor)
    editor._insert_completion(code.completion)
    assert editor.toPlainText() == "[color "
    view = StringsView()
    qtbot.addWidget(view)
    view.set_codes([code, CodeInfo("line", "", 1)])
    button = view.codes_layout.itemAt(0).widget()
    assert button.text() == "[color" and button.toolTip().startswith("text colour")


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


def test_a_replace_all_that_matched_nothing_leaves_no_undo_step(window, tmp_path):
    """An empty macro would leave a step that undoes nothing, so the macro is
    only opened by the first command actually pushed inside it."""
    data = b"\x41\xfe\x42\x00" + b"\xff" * 4
    _, block = block_with(window, tmp_path, data, stop=4)
    before = window.undo_stack.count()
    window._fr_replace_all("nothing here", "X", True, False)
    assert window.undo_stack.count() == before
    # And a run that does match still lands one step.
    window._fr_replace_all("A", "C", True, False)
    assert window.undo_stack.count() == before + 1
    assert block.doc.strings[0].current_text() == "C[line]\nB[end]"


def test_replace_all_is_code_aware_over_block_or_project(window, tmp_path):
    data = b"\x41\xfe\x42\x00\x42\xfe\x41\x00" + b"\xff" * 8
    entry, first = block_with(window, tmp_path, data, stop=8)
    second = add_block(window, entry, "b2", RangeSource(0, 4))
    window._activate_entry(first)
    assert first.doc.strings[0].original_text() == "A[line]\nB[end]"

    # "line" is inside a code and must not be touched.
    window._fr_replace_all("line", "X", True, False)
    assert not first.doc.strings[0].edited

    # A [line] turned into an [end] cuts the string in two: the block would
    # read differently, so the edit is refused and nothing lands.
    steps = window.undo_stack.count()
    window._fr_replace_all("[line]", "[end]", True, False)
    assert not first.doc.strings[0].edited and window.undo_stack.count() == steps

    # Project scope reaches the block that was never opened.
    window._fr_replace_all("A", "C", True, True)
    assert first.doc.strings[0].current_text() == "C[line]\nB[end]"
    assert second.doc.strings[0].current_text() == "C[line]\nB[end]"
    # Both blocks read the same bytes, so the file is what has unsaved edits,
    # and the lot undoes as one step: both go back to the original together.
    assert entry.dirty
    window.undo_stack.undo()
    assert not first.doc.strings[0].edited
    assert not second.doc.strings[0].edited


def test_find_next_walks_into_the_next_block(window, tmp_path):
    data = b"\x41\x00\x42\x00" + b"\xff" * 8
    entry, first = block_with(window, tmp_path, data, stop=2)
    add_block(window, entry, "b2", RangeSource(2, 4))
    window._activate_entry(first)
    window._fr_find_next("B", True, True)
    assert window._entry.name == "b2"


def test_box_and_table_edits_undo_in_one_step(window, tmp_path):
    data = b"\x41\x42\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data, stop=3)

    window._on_box_changed(TextBox(width=64))
    assert block.box.width == 64
    window.undo_stack.undo()
    assert block.box is None or block.box.width != 64

    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    was = deepcopy(table_entry.table.entries)
    window.table_editor.new_line.setText("44=D")
    window.table_editor._add()
    assert "01000100" in table_entry.table.entries
    window.undo_stack.undo()
    assert set(table_entry.table.entries) == set(was)


def test_fill_leaves_taken_keys_alone(window, tmp_path):
    data = b"\x41\x00" + b"\xff" * 8
    block_with(window, tmp_path, data, stop=2)
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    # The dialog says which keys are taken; declined, they are left alone.
    from mapchar.ui.table_dialogs import FillDialog

    dialog = FillDialog(table_entry.table)
    dialog.template.setCurrentIndex(dialog.template.findData("A-Z"))
    dialog.first.setText("41")
    assert "already have entries" in dialog.preview.text()
    editor.fill_run(dialog.chars(), dialog.start(), dialog.width(), overwrite=False)
    assert "left alone" in editor.status.text()
    # 41=A, 42=B and 43=C were there already and keep their own text.
    assert table_entry.table.entries["01000010"].text == "B"


def rows():
    return [
        RowData(0, 0, "A[line]\nB[end]", "A[line]\nB[end]", 4, 4, "untouched", ""),
        RowData(1, 4, "B[end]", "A[end]", 2, 2, "edited", "seen"),
    ]


def test_the_pane_shows_the_selected_string_whole(qtbot):
    view = StringsView()
    qtbot.addWidget(view)
    view.set_rows(rows())
    view.select_index(0)
    pane = view.pane
    assert pane.index == 0 and pane.editor.toPlainText() == "A[line]\nB[end]"
    assert pane.heading.text() == "#0 · 0 · untouched"
    assert pane.readout.text() == "4 / 4 byte(s)"
    # The original keeps its line breaks; Show codes off leaves the words.
    pane.show_codes.setChecked(True)
    assert pane.original.toPlainText() == "A[line]\nB[end]"
    pane.show_codes.setChecked(False)
    assert pane.original.toPlainText() == "A\nB"
    assert hide_codes("x\\[not a code] [code]") == "x\\[not a code] "
    view.select_index(1)
    assert pane.notes.text() == "seen" and pane.editor.toPlainText() == "A[end]"
    view.set_rows([])
    assert pane.index is None and not pane.isEnabled()


def test_the_pane_commits_on_return_and_moves_on(qtbot):
    view = StringsView()
    qtbot.addWidget(view)
    landed: list[tuple[int, str]] = []
    problems: list[str] = []
    view.commit_handler = lambda i, t: landed.append((i, t)) or None
    view.problem_shown.connect(problems.append)
    view.set_rows(rows())
    view.select_index(0)
    pane = view.pane
    drafts: list[str] = []
    view.draft_changed.connect(drafts.append)
    pane.editor.setPlainText("B[end]")
    assert drafts == ["B[end]"] and pane.dirty()
    key(pane.editor, Qt.Key.Key_Return)
    assert landed == [(0, "B[end]")] and problems == []
    # Moved on to the next row, on the pane.
    assert view.selected_indices() == [1] and pane.index == 1
    # A refused commit keeps the draft and the row, with the reason shown.
    view.commit_handler = lambda i, t: "too long"
    pane.editor.setPlainText("AAA[end]")
    key(pane.editor, Qt.Key.Key_Return)
    assert problems == ["too long"] and pane.readout.text() == "too long"
    assert view.selected_indices() == [1] and pane.editor.toPlainText() == "AAA[end]"
    # Selecting another row lands the draft first; refused, the row stays.
    view.table.selectRow(0)
    assert view.selected_indices() == [1] and pane.editor.toPlainText() == "AAA[end]"
    # Esc puts the bytes' text back.
    key(pane.editor, Qt.Key.Key_Escape)
    assert not pane.dirty() and pane.editor.toPlainText() == "A[end]"
    view.table.selectRow(0)
    assert view.selected_indices() == [0]
    # The notes field lands on the string it was edited on.
    notes: list[tuple[int, str]] = []
    view.notes_edited.connect(lambda i, t: notes.append((i, t)))
    pane.notes.setText("check")
    pane.notes.editingFinished.emit()
    assert notes == [(0, "check")]
    # A code button with no cell open types into the pane.
    view.set_codes([CodeInfo("line", "", 1)])
    view.codes_layout.itemAt(0).widget().click()
    assert pane.editor.toPlainText() == "A[line]\nB[end][line]"


def test_the_pane_edits_the_bytes(window, tmp_path):
    data = b"\x41\x42\x00\x41\x00" + b"\xff" * 4
    entry, block = block_with(window, tmp_path, data)
    window._show_view("strings")
    window.strings.select_index(0)
    pane = window.strings.pane
    pane.editor.setPlainText("B[end]")
    assert "2 / 3 byte(s)" in pane.readout.text()
    key(pane.editor, Qt.Key.Key_Return)
    assert block.doc.strings[0].current_text() == "B[end]"
    assert window.strings.selected_indices() == [1] and pane.index == 1
    # Seven bytes into a slot of six: refused, the draft and the row stay.
    pane.editor.setPlainText("AAAAAA[end]")
    assert "1 over" in pane.readout.text()
    key(pane.editor, Qt.Key.Key_Return)
    assert pane.editor.toPlainText() == "AAAAAA[end]" and pane.index == 1
    assert pane.readout.text().startswith("#1")
    assert block.doc.strings[1].current_text() == "A[end]"


def test_draft_reports_bytes_used_and_the_room(window, tmp_path):
    data = b"\x41\x42\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data, stop=3)
    window.strings.select_index(0)
    window._on_draft("ABC[end]")
    assert "4 / 3 byte(s)" in window.preview_window.readout.text()
    assert "1 over" in window.preview_window.readout.text()
    window._on_draft("A[nope]")
    assert window.preview_window.readout.text()


def test_the_preview_draws_in_the_app_font_and_names_what_it_cannot(qtbot):
    """The Preview needs no font of its own: it measures the app's and says
    which characters that family has no glyph for."""
    from mapchar.ui.preview_window import PreviewWindow

    win = PreviewWindow()
    qtbot.addWidget(win)
    win.set_box(win._box, ["line"])
    win.show_string("AB", "t")
    assert win.canvas.pixmap() is not None and not win.canvas.pixmap().isNull()
    assert "page 1/1" in win.status.text()
    win.grid.setChecked(True)
    win.zoom.setValue(4)
    assert not win.canvas.pixmap().isNull()

    # A character no family on this machine is guaranteed to draw: whatever
    # the answer, the status and the tooltip agree on it.
    win.show_string("A\ue000", "t")
    if "not in font" in win.status.text():
        assert "\ue000" in win.status.toolTip()


def test_the_font_tab_picks_the_app_font(qtbot):
    """Its pick is the app's, kept for the next run and announced once."""
    from PySide6.QtGui import QFontDatabase

    from mapchar.ui.preview_font import forget_preview_font, preview_font
    from mapchar.ui.preview_window import PreviewWindow

    win = PreviewWindow()
    qtbot.addWidget(win)
    changes: list = []
    win.font_changed.connect(lambda: changes.append(True))
    families = QFontDatabase.families()
    other = next(f for f in families if f != preview_font().family)

    win.font_tab.family.setCurrentFont(win.font_tab.family.currentFont())
    win.font_tab.size.setValue(20)
    assert preview_font().size == 20 and changes

    win.font_tab.family.setCurrentText(other)
    assert preview_font().family == other
    # Stored, so the next run starts where this one left off.
    forget_preview_font()
    assert preview_font().family == other and preview_font().size == 20


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

    # Room after the string, for the line codes wrapping adds.
    data = b"\x41\x42\x41\x42\x41\x42\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data, stop=15)
    # Counted rather than measured, so the wrap does not depend on which
    # families the machine running the tests happens to have.
    block.box = Box(
        width=16,
        height=32,
        line_height=8,
        chars_per_line=2,
        effects={"line": CodeEffect(Effect.NEWLINE)},
    )
    window.strings.select_index(0)
    depth = window.undo_stack.index()
    window._wrap_selected()
    assert window.undo_stack.index() == depth + 1
    assert "[line]" in block.doc.strings[0].current_text()
    window.undo_stack.undo()
    assert not block.doc.strings[0].edited


def test_a_table_keeps_its_identity_through_undo(window, tmp_path):
    """The Table Editor and every view hold the same ``Table`` object, so an
    undo restores its contents rather than swapping the object out."""
    data = b"\x41\x00" + b"\xff" * 8
    block_with(window, tmp_path, data, stop=2)
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    table = table_entry.table
    window.table_editor.new_line.setText("44=D")
    window.table_editor._add()
    assert "01000100" in table.entries
    window.undo_stack.undo()
    assert table_entry.table is table
    assert "01000100" not in table.entries
    window.undo_stack.redo()
    assert table_entry.table is table and "01000100" in table.entries


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
    original = QPainter.drawStaticText

    def spy(painter, point, laid):
        placed.append((point, laid))
        return original(painter, point, laid)

    QPainter.drawStaticText = spy
    try:
        widget.viewport().grab()
    finally:
        QPainter.drawStaticText = original
    pairs = {laid.text(): (point, laid.size()) for point, laid in placed}
    for rel in range(BYTES_PER_ROW):
        cell = widget._hex_cell(rel)
        point, size = pairs[f"{rel:02X}"]
        assert point.x() == cell.left() + (cell.width() - size.width()) / 2
        assert cell.top() <= point.y()
        assert point.y() + size.height() <= cell.bottom() + 1


def test_bit_packed_tokens_each_get_a_place_of_their_own(qtbot):
    """Four 6-bit codes fill three bytes: each is placed by its bits, three
    quarters of a byte cell, and none shares another's place."""
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
    for rect in rects:
        assert rect.width() == widget._text_width * 6 / 8
    for left, right in zip(rects, rects[1:], strict=False):
        assert left.right() == right.left()
    assert rects[0].left() == QRectF(widget._text_cell(0)).left()
    assert rects[-1].right() == QRectF(widget._text_cell(2)).right()


def test_a_token_over_a_row_end_is_placed_on_both_rows(qtbot):
    from mapchar.core.tokens import Token
    from mapchar.ui import BYTES_PER_ROW

    last = BYTES_PER_ROW - 1
    token = Token("0" * 16, last * 8, (last + 2) * 8, _text_entry("0" * 16, "漢"))
    widget = _raw_widget(qtbot, [token], bytes(BYTES_PER_ROW * 2))
    first, second = widget._text_segments(token, BYTES_PER_ROW * 2)
    assert first == widget._text_cell(last)
    assert second == widget._text_cell(BYTES_PER_ROW)


def _six_bit_codes(qtbot, count=4):
    """``count`` 6-bit codes over a widget, and the widget."""
    from mapchar.core.tokens import Token

    tokens = [
        Token(f"{i:06b}", 6 * i, 6 * i + 6, _text_entry(f"{i:06b}", "かきくけ"[i % 4]))
        for i in range(count)
    ]
    return tokens, _raw_widget(qtbot, tokens, bytes(-(-6 * count // 8)))


def _click(widget, point):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    QTest.mouseClick(widget.viewport(), Qt.MouseButton.LeftButton, pos=point.toPoint())


def test_a_click_on_a_character_selects_its_bits_in_both_columns(qtbot):
    """The second 6-bit code straddles bytes 0 and 1: clicked, it is selected by
    its bits, the window is told the two bytes it touches, and the hex column
    covers the last two bits of the first pair and exactly the first digit of
    the second."""
    from PySide6.QtCore import QRectF

    tokens, widget = _six_bit_codes(qtbot)
    told = []
    widget.selection_changed.connect(lambda s, e: told.append((s, e)))
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    assert widget.selection_bits() == (6, 12)
    assert told == [(0, 2)]
    (text,) = widget._text_span(6, 12)
    assert text == widget._text_segments(tokens[1], 3)[0]
    (hex_span,) = widget._hex_span(6, 12)
    first, second = QRectF(widget._hex_cell(0)), QRectF(widget._hex_cell(1))
    assert first.center().x() < hex_span.left() < first.right()
    assert hex_span.right() == second.center().x()


def test_a_click_on_a_hex_pair_selects_the_whole_byte(qtbot):
    from PySide6.QtCore import QRectF

    tokens, widget = _six_bit_codes(qtbot)
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    _click(widget, QRectF(widget._hex_cell(1)).center())
    assert widget.selection() == (1, 2)
    assert widget.selection_bits() is None


def test_dragging_over_characters_selects_every_code_it_crosses(qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    tokens, widget = _six_bit_codes(qtbot)
    start = widget._text_segments(tokens[1], 3)[0].center().toPoint()
    end = widget._text_segments(tokens[2], 3)[0].center().toPoint()
    viewport = widget.viewport()
    QTest.mousePress(viewport, Qt.MouseButton.LeftButton, pos=start)
    # QTest's move carries no held button, so the drag is sent as Qt sends it.
    widget.mouseMoveEvent(_move_event(viewport, end))
    assert widget.selection_bits() == (6, 18)
    assert widget.selection() == (0, 3)


def test_a_byte_aligned_bit_span_covers_its_cells_exactly(qtbot):
    from PySide6.QtCore import QRectF

    _, widget = _six_bit_codes(qtbot)
    assert widget._hex_span(8, 24) == [QRectF(widget._hex_cell(1, 2))]
    assert widget._text_span(8, 24) == [QRectF(widget._text_cell(1, 2))]


def test_a_selection_set_from_outside_is_whole_bytes(qtbot):
    tokens, widget = _six_bit_codes(qtbot)
    _click(widget, widget._text_segments(tokens[1], 3)[0].center())
    widget.set_selection(0, 2)
    assert widget.selection_bits() is None


def _move_event(viewport, point):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    local = QPointF(point)
    return QMouseEvent(
        QEvent.Type.MouseMove,
        local,
        QPointF(viewport.mapToGlobal(point)),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_the_text_column_shows_names_as_labels_and_marks_inside_text():
    from mapchar.core.table import TokenKind
    from mapchar.core.tokens import Token
    from mapchar.ui.token_text import display_text

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


# --- done, project strings and the glossary ----------------------------------


def test_done_is_held_and_counted(window, tmp_path):
    from mapchar.core.block import Status

    data = b"\x41\x42\x00\x41\x00" + b"\xff" * 4
    entry, block = block_with(window, tmp_path, data)
    window._show_view("strings")
    window.strings.select_index(0)
    window._toggle_done_selected()
    # A byte edit reads the strings again, so the record is looked up afresh.
    status = lambda: block.doc.strings[0].status  # noqa: E731
    assert status() is Status.DONE
    # Done sticks whatever the bytes do, and is counted by the block bar.
    window._on_translation_edited(0, "B[end]")
    assert status() is Status.DONE
    assert "done 1" in window.block_label.text()
    window._on_translation_edited(0, "")
    assert status() is Status.DONE
    window._toggle_done_selected()
    assert status() is Status.UNTOUCHED
    window.undo_stack.undo()
    assert status() is Status.DONE
    window.strings.status_filter.setCurrentText("done")
    assert window.strings._visible_rows() == [0]


def test_chars_per_line_flags_overflow_and_wraps_without_a_font(window, tmp_path):

    from mapchar.core.font import CodeEffect, Effect

    # Room after the string: a line code makes the text a byte longer.
    data = b"\x41\x42\x41\x42\x41\x00" + b"\xff" * 8
    entry, block = block_with(window, tmp_path, data)
    window._show_view("strings")
    box = TextBox(chars_per_line=3, effects={"line": CodeEffect(Effect.NEWLINE)})
    window._on_box_changed(box)
    assert window.strings._row_data(0).status == "overflows box"
    window.strings.select_index(0)
    window._on_draft("ABAB")
    assert "4 / 3 chars" in window.strings.pane.readout.text()
    window._wrap_selected()
    assert block.doc.strings[0].current_text() == "ABA[line]\nBA[end]"
    assert window.strings._row_data(0).status == "edited"
    window._on_box_changed(replace(box, lines_per_page=1))
    assert window.strings._row_data(0).status == "overflows box"


def test_next_untranslated_finds_a_string_its_box_overflows(window, tmp_path):
    """A row shows "overflows box" in place of the string's own status, and an
    untouched string is still untranslated whatever its box says about it."""
    data = b"\x41\x42\x00\x42\x41\x00" + b"\xff" * 8
    _entry, block = block_with(window, tmp_path, data, stop=6)
    window._show_view("strings")
    window._on_box_changed(TextBox(chars_per_line=1))
    window._set_translation(block, 1, "BB[end]")
    assert window.strings._row_data(0).status == "overflows box"
    assert block.doc.strings[0].status is Status.UNTOUCHED
    window.strings.select_index(1)
    window._step_strings("untranslated")
    assert window.strings.selected_indices() == [0]


def test_project_strings_lists_every_block_and_jumps(window, tmp_path):
    from mapchar.core.block import RangeSource

    data = b"\x41\x00\x42\x00\x41\x42\x00"
    entry, first = block_with(window, tmp_path, data, name="one", stop=4)
    second = add_block(window, entry, "two", RangeSource(4, 7))
    window._activate_entry(first)
    window._show_project_strings()
    listing = window.project_strings
    assert listing.isVisible() and listing.results.rowCount() == 3
    listing.filter.setText("two")
    assert listing.results.rowCount() == 1
    listing.filter.setText("")
    listing.status_filter.setCurrentText("untouched")
    assert listing.results.rowCount() == 3
    # Enter or a double-click on a row opens its block on that string.
    listing.results.selectRow(2)
    listing._jump()
    assert window._entry is second and window.strings.selected_indices() == [0]
    assert window._current_view() == "strings"
    # The list follows an edit while it is open.
    listing.status_filter.setCurrentText("all")
    window._on_translation_edited(0, "A[end]")
    assert listing.results.item(2, 3).text() == "A[end]"
    assert listing.results.item(2, 4).text() == "edited"


def test_glossary_terms_are_kept_undone_and_typed_in(window, tmp_path):
    from PySide6.QtWidgets import QTableWidgetItem

    from mapchar.project.glossary import GlossaryTerm

    data = b"\x41\x42\x00\x41\x00" + b"\xff" * 4
    entry, block = block_with(window, tmp_path, data)
    window._show_view("strings")
    window.strings.select_index(0)
    window._show_glossary()
    glossary = window.glossary_window
    glossary._add()
    glossary.table.closePersistentEditor(glossary.table.item(0, 0))
    glossary.table.setItem(0, 0, QTableWidgetItem("AB"))
    glossary.table.setItem(0, 1, QTableWidgetItem("BA"))
    assert window.workspace.glossary == [GlossaryTerm("AB", "BA")]
    assert "glossary" in window._snapshot()  # what the project file will hold
    # The string on screen holds the term, so it is listed and can be typed in.
    assert glossary.hits.rowCount() == 1
    glossary.hits.selectRow(0)
    glossary._insert()
    assert window.strings.pane.editor.toPlainText() == "AB[end]BA"
    window.strings.pane.cancel()
    window.strings.select_index(1)
    window._on_string_row(1)  # what a click on the row does
    assert glossary.hits.rowCount() == 0
    window.undo_stack.undo()
    assert window.workspace.glossary == [] and glossary.table.rowCount() == 0
    window.undo_stack.redo()
    assert window.workspace.glossary == [GlossaryTerm("AB", "BA")]
    glossary.table.selectRow(0)
    glossary._remove()
    assert window.workspace.glossary == []
