"""A translation the bytes refuse is kept by the project until they take it."""

from __future__ import annotations

from copy import deepcopy

from mapchar.core.block import RangeSource
from mapchar.core.table import TableEntry, TokenKind
from mapchar.project.projectfile import load_project
from mapchar.ui.strings_view import UNWRITTEN
from window_helpers import ab_ba_rom, add_block, open_rom_and_table

DATA = ab_ba_rom(4)


def _block(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    return file_entry, add_block(
        window, file_entry, "b", RangeSource(0, 6), fill=b"\xee"
    )


def test_a_refused_text_is_shown_flagged_in_place_of_the_bytes(window, tmp_path):
    _, block = _block(window, tmp_path)
    window._on_translation_edited(0, "AC[end]")  # nothing encodes a C
    row = window._row_data(block, block.doc)[0]
    assert (row.translation, row.unwritten, row.status) == (
        "AB[end]",
        "AC[end]",
        UNWRITTEN,
    )
    assert row.shown == "AC[end]" and row.problem
    assert "AB[end]" in row.unwritten_note
    # Next Flagged stops on it, and the filter finds it.
    window._show_view("strings")
    window.strings.select_index(1)
    window._step_strings("flagged")
    assert window.strings.selected_indices() == [0]
    # The pane opens on the text kept, not on the bytes'.
    assert window.strings.pane.editor.toPlainText() == "AC[end]"
    assert not window.strings.pane.dirty()


def test_an_unwritten_translation_travels_with_the_project(window, tmp_path):
    _, block = _block(window, tmp_path)
    window._on_translation_edited(0, "AC[end]")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    saved = load_project(str(proj))
    held = next(e for e in saved.entries if e.name == "b").pending_strings
    assert held[0].unwritten == "AC[end]" and held[1].unwritten is None
    assert window.open_project(str(proj))
    back = window.workspace.block_named("b")
    window._activate_entry(back)
    assert back.doc.strings[0].unwritten == "AC[end]"
    assert back.doc.strings[0].current_text() == "AB[end]"


def test_a_table_that_learns_the_character_lets_it_be_written(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._on_translation_edited(0, "AC[end]")
    assert block.doc.strings[0].unwritten == "AC[end]"
    table_entry = window.workspace.entry_for_table("main")
    before = deepcopy(table_entry.table)
    table_entry.table.add(TableEntry("01000011", TokenKind.TEXT, "C"))
    window._on_table_edited(table_entry, before)
    rec = block.doc.strings[0]
    assert rec.current_text() == "AC[end]" and rec.unwritten is None
    # A step of its own after the table's: undone, the text is kept again.
    window.undo_stack.undo()
    rec = block.doc.strings[0]
    assert rec.current_text() == "AB[end]" and rec.unwritten == "AC[end]"
    assert file_entry.doc.data == DATA


def test_what_still_does_not_fit_stays_kept(window, tmp_path):
    _, block = _block(window, tmp_path)
    window._on_translation_edited(0, "ABBB[end]")  # too long for its room
    window.strings.select_index(0)
    window._write_unwritten_selected()
    rec = block.doc.strings[0]
    assert rec.unwritten == "ABBB[end]" and rec.current_text() == "AB[end]"
    # Shortened, it lands, and the string keeps nothing back.
    window._on_translation_edited(0, "A[end]")
    rec = block.doc.strings[0]
    assert rec.unwritten is None and rec.current_text() == "A[end]"
    window.undo_stack.undo()
    rec = block.doc.strings[0]
    assert rec.unwritten == "ABBB[end]" and rec.current_text() == "AB[end]"


def test_revert_lets_go_of_what_is_kept(window, tmp_path):
    _, block = _block(window, tmp_path)
    window._on_translation_edited(0, "ABBB[end]")
    window._show_view("strings")
    window.strings.select_index(0)
    window._revert_selected()
    assert block.doc.strings[0].unwritten is None


def test_replace_all_works_on_the_text_kept(window, tmp_path):
    _, block = _block(window, tmp_path)
    window._on_translation_edited(0, "ABBB[end]")
    window._fr_replace_all("BBB", "", True, False)
    rec = block.doc.strings[0]
    assert rec.unwritten is None and rec.current_text() == "A[end]"
