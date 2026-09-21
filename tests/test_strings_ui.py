"""The editing surfaces: code-aware find and replace, the translation editor,
the font and table editors' undo, and the live draft readout."""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QKeyEvent, QTextCursor, QTextDocument

from mapchar.core.block import RangeSource, Status
from mapchar.core.font import TextBox
from mapchar.core.tokens import piece_spans
from mapchar.engines import scriptfind
from mapchar.ui.code_editor import CodeEditor, CodeInfo
from mapchar.ui.string_pane import CodeHighlighter
from mapchar.ui.strings_view import (
    COL_NOTES,
    COL_TRANSLATION,
    RowData,
    StringsView,
)
from mapchar.ui.token_text import code_spans, hide_codes
from window_helpers import CODES_TABLE, add_block, open_rom_and_table


def block_with(window, tmp_path, data, name="b", stop=None):
    entry = open_rom_and_table(window, tmp_path, data, table=CODES_TABLE)
    return entry, add_block(
        window, entry, name, RangeSource(0, stop if stop else len(data))
    )


# --- the matcher ----------------------------------------------------------


def test_pieces_keep_codes_whole():
    # The third element says which piece is a code.
    assert piece_spans("A[line]B") == [(0, 1, False), (1, 7, True), (7, 8, False)]
    assert piece_spans("\\[x") == [(0, 2, False), (2, 3, False)]
    # An unclosed '[' is the one piece being typed, and is no code yet.
    assert piece_spans("A[li") == [(0, 1, False), (1, 4, False)]


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
    # read differently, so the edit is refused and nothing lands: the text is
    # kept unwritten, and undoing the replace lets go of it.
    window._fr_replace_all("[line]", "[end]", True, False)
    assert not first.doc.strings[0].edited
    assert first.doc.strings[0].unwritten == "A[end]\nB[end]"
    window.undo_stack.undo()
    assert first.doc.strings[0].unwritten is None

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
    editor.fill_cells(dialog.chars(), dialog.start(), dialog.width(), overwrite=False)
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


def dimmed(doc, block):
    """The ``(start, length)`` runs :class:`CodeHighlighter` formatted."""
    layout = doc.findBlockByNumber(block).layout()
    return [(run.start, run.length) for run in layout.formats()]


def test_a_code_is_found_past_an_escaped_backslash(qtbot):
    # One character of lookbehind cannot tell a '[' escaped by a backslash
    # from one after a backslash that is itself escaped, so the grammar's own
    # walk decides: '\\' is a piece, and '[line]' after it is a code.
    assert code_spans("a\\\\[line]b") == [(3, 9)]
    assert hide_codes("a\\\\[line]b") == "a\\\\b"
    # An unclosed '[' swallows the escape after it, so the pair that closes is
    # the code — the lenient walk's answer, which every other surface takes.
    assert code_spans("[\\[line]") == [(2, 8)]
    assert hide_codes("[\\[line]") == "[\\"
    doc = QTextDocument("a\\\\[line]b\n[\\[line]")
    CodeHighlighter(doc).rehighlight()
    assert dimmed(doc, 0) == [(3, 6)]
    assert dimmed(doc, 1) == [(2, 6)]


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
    # A refused commit stays on the row, with the reason shown; the window
    # keeps the text unwritten, so it is a draft no longer.
    view.commit_handler = lambda i, t: "too long"
    pane.editor.setPlainText("AAA[end]")
    key(pane.editor, Qt.Key.Key_Return)
    assert problems == ["too long"] and pane.readout.text() == "too long"
    assert view.selected_indices() == [1] and pane.editor.toPlainText() == "AAA[end]"
    assert not pane.dirty()
    # Esc puts back what a draft started from.
    pane.editor.setPlainText("AAAB[end]")
    key(pane.editor, Qt.Key.Key_Escape)
    assert not pane.dirty() and pane.editor.toPlainText() == "AAA[end]"
    # Selecting another row lands a draft first, and moves on even when the
    # bytes refuse it.
    pane.editor.setPlainText("AAAA[end]")
    view.table.selectRow(0)
    assert problems == ["too long", "too long"]
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
    glossary = window.glossary_panel
    glossary.add_term()
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
