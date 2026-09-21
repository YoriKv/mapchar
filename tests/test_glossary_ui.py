"""The Glossary panel, and Find and Replace over its terms."""

from __future__ import annotations

from mapchar.core.block import RangeSource, Status
from mapchar.project.glossary import GlossaryTerm
from mapchar.ui.find_replace import BLOCK, SELECTION, Search
from mapchar.ui.strings_view import MISSES
from window_helpers import ABC_TABLE, add_block, open_rom_and_table

# AB[end] BA[end] AB[end], and room to spare.
DATA = bytes.fromhex("41 42 00 42 41 00 41 42 00") + b"\xee" * 7
TERM = GlossaryTerm("AB", "C")


def _block(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA, table=ABC_TABLE)
    block = add_block(window, file_entry, "b", RangeSource(0, 16), fill=b"\xee")
    window.apply_glossary([TERM])
    window._show_view("strings")
    return block


def test_the_glossary_is_a_dock_under_the_files_panel(window):
    from PySide6.QtCore import Qt

    assert window.dockWidgetArea(window.glossary_dock) == (
        Qt.DockWidgetArea.LeftDockWidgetArea
    )
    assert window.glossary_dock.widget() is window.glossary_panel


def test_terms_are_stepped_through_and_replaced_one_by_one(window, tmp_path):
    block = _block(window, tmp_path)
    window.strings.select_index(0)
    window._replace_glossary_terms()
    dialog = window.find_replace
    assert dialog.glossary() and not dialog.find.isEnabled()
    search = dialog.search()
    assert search.glossary and search.scope == BLOCK
    window._search_next(search)
    assert window.strings.selected_indices() == [0]
    assert dialog.hit.text() == "AB → C"
    # The pane underlines the term in the original, and the panel lists it.
    assert [(h.start, h.stop) for h in window.strings.pane._term_hits] == [(0, 2)]
    window._show_glossary()
    assert window.glossary_panel.hits.rowCount() == 1
    assert window.strings.pane.editor.textCursor().selectedText() == "AB"
    # Replace takes the hit stood on and stands on the next; Find Next skips.
    window._search_replace(search)
    assert block.doc.strings[0].current_text() == "C[end]"
    assert window.strings.selected_indices() == [2]
    window._search_next(search)
    assert window.strings.selected_indices() == [2]  # round to the only one left
    assert block.doc.strings[2].current_text() == "AB[end]"


def test_replace_all_leaves_the_done_strings_alone(window, tmp_path):
    block = _block(window, tmp_path)
    block.doc.strings[2].status = Status.DONE
    window._search_replace_all(Search(glossary=True))
    assert [r.current_text() for r in block.doc.strings] == [
        "C[end]",
        "BA[end]",
        "AB[end]",
    ]
    window.undo_stack.undo()
    assert block.doc.strings[0].current_text() == "AB[end]"
    window._search_replace_all(Search(glossary=True, skip_done=False))
    assert block.doc.strings[2].current_text() == "C[end]"


def test_the_selection_scope_is_the_rows_selected_when_it_opened(window, tmp_path):
    block = _block(window, tmp_path)
    window.strings.select_index(2)
    window._replace_terms_in_selection()
    assert window.find_replace.search().scope == SELECTION
    window.strings.select_index(0)
    window._search_replace_all(window.find_replace.search())
    assert [r.current_text() for r in block.doc.strings] == [
        "AB[end]",
        "BA[end]",
        "C[end]",
    ]


def test_a_translation_that_goes_its_own_way_is_flagged(window, tmp_path):
    block = _block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    window._on_translation_edited(2, "C[end]")
    rows = window._row_data(block, block.doc)
    assert [r.misses for r in rows] == ["AB → C", "", ""]
    window.strings.status_filter.setCurrentText(MISSES)
    assert [window.strings.table.isRowHidden(r) for r in range(3)] == [
        False,
        True,
        True,
    ]


def test_a_translation_the_table_cannot_spell_is_marked(window, tmp_path):
    _block(window, tmp_path)
    window.apply_glossary([TERM, GlossaryTerm("BA", "Z")])
    window._show_glossary()
    assert list(window._unspelled_terms()) == [GlossaryTerm("BA", "Z")]


def test_terms_are_added_from_what_is_marked_and_keep_their_options(window, tmp_path):
    _block(window, tmp_path)
    window._add_to_glossary("BA", "")
    assert window.workspace.glossary == [TERM, GlossaryTerm("BA")]
    panel = window.glossary_panel
    panel._toggle([1], "whole_word", True)
    assert window.workspace.glossary[1] == GlossaryTerm("BA", whole_word=True)
    # Typing in a row keeps the options it has.
    panel.table.item(1, 1).setText("A")
    assert window.workspace.glossary[1] == GlossaryTerm("BA", "A", whole_word=True)


def test_uses_are_counted_over_the_project(window, tmp_path):
    _block(window, tmp_path)
    window._show_glossary()
    window._count_glossary_uses()
    assert window.glossary_panel.table.item(0, 3).text() == "2"


def test_a_glossary_goes_out_and_comes_back_as_a_tsv(window, tmp_path, monkeypatch):
    _block(window, tmp_path)
    path = str(tmp_path / "terms.tsv")
    monkeypatch.setattr(window, "_pick_save", lambda *a, **k: path)
    monkeypatch.setattr(window, "_pick_open", lambda *a, **k: path)
    window._export_glossary()
    window.apply_glossary([])
    window._import_glossary()
    assert window.workspace.glossary == [TERM]


def test_one_term_is_replaced_through_the_project(window, tmp_path):
    block = _block(window, tmp_path)
    other = GlossaryTerm("BA", "A")
    window.apply_glossary([TERM, other])
    window._replace_glossary_terms(other)
    search = window.find_replace.search()
    assert search.term == other and search.scope == "project"
    window._search_replace_all(search)
    assert [r.current_text() for r in block.doc.strings] == [
        "AB[end]",
        "A[end]",
        "AB[end]",
    ]
