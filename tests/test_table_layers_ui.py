"""The Table Editor over a table that includes another, and a table's effects
in the window."""

from __future__ import annotations

from PySide6.QtWidgets import QInputDialog

from mapchar.core.block import RangeSource
from mapchar.core.font import Effect, TextBox
from mapchar.project.formats.table_native import HEADER
from mapchar.ui.table_grid import DETAILS, KEY, ORIGIN_ROLE, TEXT
from window_helpers import (
    add_block,
    grid_cell,
    grid_row,
    open_rom_and_table,
    type_in_grid,
)

BASE = f"{HEADER}\n@table base\n41=A\n42=B\n/00=[end]\n01=[wait]\n"
TOP = f"{HEADER}\n@table top\n@include base\n42=b\n"


def _tables(window, tmp_path):
    (tmp_path / "base.tbl").write_text(BASE)
    (tmp_path / "top.tbl").write_text(TOP)
    base = window.open_table(str(tmp_path / "base.tbl"))
    top = window.open_table(str(tmp_path / "top.tbl"))
    return base, top


def _row(editor, key: str) -> int:
    return grid_row(editor, key)


def test_inherited_rows_show_dimmed_and_editing_one_overrides_it(window, tmp_path):
    base, top = _tables(window, tmp_path)
    window._edit_table_entry(top)
    editor = window.table_editor
    assert editor.includes.text() == "base"
    row = _row(editor, "41")
    assert grid_cell(editor, row, KEY, ORIGIN_ROLE) == "base"
    assert grid_cell(editor, row, DETAILS) == "from @base"
    assert grid_cell(editor, _row(editor, "42"), KEY, ORIGIN_ROLE) is None

    # Typing over an inherited row gives the table its own entry.
    type_in_grid(editor, row, TEXT, "a")
    assert top.table.entries["01000001"].text == "a"
    assert top.table_overlay == {"01000001": "41=a"}
    assert grid_cell(editor, _row(editor, "41"), KEY, ORIGIN_ROLE) is None

    # Removing an inherited row removes it with an empty entry of the table's own.
    editor.grid.selectRow(_row(editor, "00"))
    editor._remove()
    assert top.table.entries["00000000"].text == ""
    assert grid_cell(editor, _row(editor, "00"), DETAILS) == ("removes @base's entry")
    window.undo_stack.undo()
    assert "00000000" not in top.table.entries

    # An edit to the included table reaches the includer's rows.
    window._edit_table_entry(base)
    editor.new_line.setText("43=C")
    editor._add()
    window._edit_table_entry(top)
    assert grid_cell(editor, _row(editor, "43"), KEY, ORIGIN_ROLE) == "base"


def test_a_label_the_include_already_gives_is_refused(window, tmp_path):
    _, top = _tables(window, tmp_path)
    window._edit_table_entry(top)
    editor = window.table_editor
    editor.new_line.setText("02=[wait]")
    editor._add()
    assert "duplicate label [wait]" in editor.status.text()
    assert "00000010" not in top.table.entries


def test_the_includes_field_and_a_rename_carry_the_include(
    window, tmp_path, monkeypatch
):
    base, top = _tables(window, tmp_path)
    window._edit_table_entry(top)
    editor = window.table_editor
    editor.includes.setText("")
    editor.includes.editingFinished.emit()
    assert top.table.includes == () and top.table_includes == ()
    window.undo_stack.undo()
    assert top.table.includes == ("base",) and top.table_includes is None

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("font", True))
    window._rename_table(base)
    assert base.table.id == "font"
    assert top.table.includes == ("font",) and top.table_includes == ("font",)


def test_a_table_effect_sets_the_newline_code(window, tmp_path):
    table = f"{HEADER}\n@table main\n41=A\n/00=[end]\n01{{newline}}=[br]\n"
    file_entry = open_rom_and_table(window, tmp_path, b"A\x01A\x00", table)
    block = add_block(window, file_entry, "b", RangeSource(0, 4))
    block.box = TextBox(chars_per_line=4)
    box = window._layout_box(block)
    assert box.effects["br"].effect is Effect.NEWLINE
    assert window._newline_code(block) == "[br]"


def test_the_form_edits_an_effect_and_a_frame_that_falls_through(window, tmp_path):
    from mapchar.core.table import TableEntry
    from mapchar.project.formats.table_native import parse_entry
    from mapchar.ui.table_entry_form import describe

    _, top = _tables(window, tmp_path)
    window._edit_table_entry(top)
    form = window.table_editor.form
    for line in ("$04FF{pause}=[FF04],u16", "!0BFF=[saturn] @base:*+|"):
        entry = parse_entry(line)
        form.set_entry(entry)
        assert form.entry() == entry
    assert form.params.rows()[0].through.isChecked()
    form.set_entry(TableEntry("00000001", parse_entry("01=[x]").kind, "[x]"))
    form.effect.setCurrentIndex(form.effect.findData(Effect.PAGE))
    assert form.line.text() == "01{page}=[x]"
    assert describe(form.entry()) == "page"
    assert describe(parse_entry("$04FF{pause}=[FF04],u16")) == "reads u16 · pause"
