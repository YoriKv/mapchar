"""The Table Editor, and the tables the window holds around it.

The grid and the entry form, tables opened, created, renamed and reloaded, and
the project overlay an in-app edit lives in until Save As File folds it in.
"""

from __future__ import annotations

from pathlib import Path

from mapchar.core.block import RangeSource
from mapchar.project.entry import Entry, EntryKind
from window_helpers import (
    TABLE,
    ab_ba_rom,
    add_block,
    grid_keys,
    grid_row,
    item_for,
    open_rom_and_table,
    type_in_grid,
)


def test_table_editor_shift_and_fill(window, tmp_path, monkeypatch):
    from mapchar.core.table import Table

    table = Table("t")
    entry = Entry(EntryKind.TABLE, "t.tbl", None, dialect="native", table=table)
    window._push_add(entry)
    editor = window.table_editor
    editor.set_entry(entry)
    from PySide6.QtWidgets import QDialog

    from mapchar.ui.table_dialogs import FillDialog, ShiftKeysDialog

    def run_fill(self):
        self.template.setCurrentIndex(self.template.findData("A-Z"))
        self.first.setText("41")
        assert self.preview.text() == "26 keys, 41 to 5A"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(FillDialog, "exec", run_fill)
    editor._fill_dialog()
    assert table.entries["01000001"].text == "A" and len(table.entries) == 26
    editor.grid.selectAll()

    def run_shift(self):
        self.offset.setText("-1")
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(ShiftKeysDialog, "exec", run_shift)
    editor._shift()
    assert table.entries["01000000"].text == "A" and "01011010" not in table.entries


def test_tables_show_their_entry_count(window, tmp_path):
    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.table_entries()[0]
    assert item_for(window, table_entry).text(0) == "main.tbl  (3)"
    assert window.format_pick.itemText(window.format_pick.findData("main")) == (
        "@main  (3)"
    )
    window._edit_table_entry(table_entry)
    window.table_editor.new_line.setText("44=D")
    window.table_editor._add()
    assert item_for(window, table_entry).text(0) == "main.tbl  (4) ●"
    assert window.format_pick.currentText() == "@main  (4)"


def test_the_table_editor_shows_a_table_file_s_notices(window, tmp_path):
    """The notices a table's read produced, on the editor's status line, and
    their detail in its tooltip.

    Carried by ``Entry.notices``, which every read of the file sets \u2014 the open, a
    reload from disk, a project load \u2014 so the line says what the table on screen
    was read as rather than what the first read of it found.
    """
    from mapchar.project.formats.table_native import HEADER

    path = tmp_path / "jp.tbl"
    path.write_bytes(f"{HEADER}\n@table jp\n41=\u30a2\n/00=[end]\n".encode("cp932"))
    entry = window.open_table(str(path))
    assert entry is not None
    assert entry.notices  # not UTF-8, so the read said so
    window._edit_table_entry(entry)
    assert "not UTF-8" in window.table_editor.status.text()
    # The detail, under the line it explains and indented under it.
    tip = window.table_editor.status.toolTip().splitlines()
    assert "not UTF-8" in tip[0]
    assert tip[1].startswith("    ") and "do not decode as UTF-8" in tip[1]

    window.reload_table(entry)
    assert entry.notices  # the re-read said it again
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window.workspace.replace([], None)
    assert window.open_project(str(proj))
    back = window.workspace.table_entries()[0]
    assert [str(n) for n in back.notices] == [str(n) for n in entry.notices]

    # A table read as UTF-8 has nothing to say, and says nothing.
    plain = tmp_path / "plain.tbl"
    plain.write_text(f"{HEADER}\n@table plain\n41=A\n")
    other = window.open_table(str(plain))
    window._edit_table_entry(other)
    assert window.table_editor.status.toolTip() == ""


# --- in-app table edits ------------------------------------------------------


def test_an_in_app_table_edit_is_carried_by_the_project(window, tmp_path):
    """A table edit changes the project, never the table file: the file other
    tools read keeps saying what it said until Save As File folds the overlay in."""
    data = ab_ba_rom(20)
    open_rom_and_table(window, tmp_path, data)
    table_entry = window.workspace.of_kind(EntryKind.TABLE)[0]
    tbl = Path(str(table_entry.path))
    on_disk = tbl.read_text()

    editor = window.table_editor
    editor.set_entry(table_entry)
    editor.new_line.setText("43=C")
    editor._add()
    assert "01000011" in table_entry.table.entries
    assert tbl.read_text() == on_disk  # the file is untouched
    assert table_entry.table_overlay == {"01000011": "43=C"}

    # The edit is project state, so the project reads unsaved until it is saved.
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert not window._project_dirty()
    editor.new_line.setText("44=D")
    editor._add()
    assert window._project_dirty()
    assert window._write_project(str(proj))

    window._new_project()
    assert window.open_project(str(proj))
    back = window.workspace.of_kind(EntryKind.TABLE)[0]
    assert {"01000011", "01000100"} <= set(back.table.entries)

    # A reload from disk picks up what changed there and keeps the edits on top.
    tbl.write_text(on_disk + "45=E\n")
    window.reload_table(back)
    keys = set(back.table.entries)
    assert "01000101" in keys and {"01000011", "01000100"} <= keys
    assert back.table_overlay  # still unspent

    # Save As File writes them out, and the overlay is spent.
    window._save_table_entry(back)
    assert back.table_overlay == {}
    assert "43=C" in tbl.read_text()
    assert not back.dirty


def test_a_table_with_no_file_is_saved_whole_in_the_project(window, tmp_path):
    from mapchar.core.table import Table

    window._add_memory_table(Table("extra"), "new.tbl")
    entry = window.workspace.entry_for_table("extra")
    editor = window.table_editor
    editor.set_entry(entry)
    editor.new_line.setText("41=A")
    editor._add()
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert window.open_project(str(proj))
    back = window.workspace.entry_for_table("extra")
    assert back is not None and back.path is None
    assert back.table.entries["01000001"].text == "A"


# --- new tables ----------------------------------------------------------------


def test_new_table_writes_an_empty_native_file_and_opens_it(
    window, tmp_path, monkeypatch
):
    from mapchar.project.formats.table_native import HEADER

    open_rom_and_table(window, tmp_path, b"AB\x00")
    (tmp_path / "sub").mkdir()
    path = tmp_path / "sub" / "main.tbl"
    monkeypatch.setattr(window, "_pick_save", lambda *a, **k: str(path))
    entry = window._new_table_dialog()
    # Named for its file, numbered up past the table already called that.
    assert entry is not None and entry.table.id == "main_2"
    assert path.read_text() == f"{HEADER}\n@table main_2\n"
    assert window.table_editor._entry is entry
    # From a menu the start table stays as it was.
    assert window.format_pick.currentData() == "main"
    errors = []
    monkeypatch.setattr(window, "_error", errors.append)
    assert window._new_table_dialog() is None and "already open" in errors[0]


def test_the_start_table_pick_ends_in_new_table(window, tmp_path, monkeypatch):
    open_rom_and_table(window, tmp_path, b"AB\x00")
    pick = window.format_pick
    assert pick.itemText(pick.count() - 1) == "New Table…"
    monkeypatch.setattr(window, "_pick_save", lambda *a, **k: "")
    pick.setCurrentIndex(pick.count() - 1)
    assert pick.currentData() == "main"  # cancelled: the choice before stays
    kana = tmp_path / "kana.tbl"
    monkeypatch.setattr(window, "_pick_save", lambda *a, **k: str(kana))
    pick.setCurrentIndex(pick.count() - 1)
    assert pick.currentData() == "kana" and kana.exists()
    assert pick.itemText(pick.count() - 1) == "New Table…"
    # Both tables are on offer, and picking one makes it the start table again.
    from mapchar.ui.widgets import select_data

    assert select_data(pick, "main")
    assert pick.currentData() == "main"


def test_new_table_is_on_the_files_menus(window, tmp_path):
    open_rom_and_table(window, tmp_path, b"AB\x00")
    table = window.workspace.table_entries()[0]
    for entry in (None, table):
        labels = [a.text() for a in window._build_files_menu(entry).actions()]
        assert "New Ta&ble…" in labels


def test_a_legacy_file_of_several_tables_opens_as_an_entry_each(window, tmp_path):
    tbl = tmp_path / "multi.tbl"
    tbl.write_text("@main\n41=A\n!F0=<[k]>,<@kana>:1\n@kana\n01=カ\n", "utf-8")
    entry = window.open_table(str(tbl), "abcde")
    assert entry.table.id == "main" and "00000001" not in entry.table.entries
    kana = window.workspace.entry_for_table("kana")
    assert kana is not None and kana.path is None
    assert kana.table.entries["00000001"].text == "カ"
    window.undo_stack.undo()  # one step takes both
    assert window.workspace.table_entries() == []


# --- the Table Editor's form -----------------------------------------------------


def test_the_entry_form_spells_every_kind_both_ways(qtbot):
    from mapchar.core.table import TokenKind
    from mapchar.project.formats.table_native import parse_entry
    from mapchar.ui.table_entry_form import TableEntryForm
    from mapchar.ui.table_entry_rows import STOP_DATA

    form = TableEntryForm()
    qtbot.addWidget(form)
    form.set_tables(["main", "upper"])
    for line in (
        "41=A",
        "%101<2>=x",
        "/FF=[end]",
        "$F0=[window],u8,u16be,3,bits:5",
        "!03=[str] @upper:u8+ @raw:$FF @bits:%101 return",
        "!F1= @upper:*",
        "!FE=return",
    ):
        form.line.setText(line)
        assert form.problem.text() == ""
        assert form.entry() == parse_entry(line)
        assert form.line.text() == line
    # The pickers write the line: a switch's stop changed to a data-read count.
    form.line.setText("!03=[str] @upper:3")
    row = form.params.rows()[0]
    row.stop.setCurrentIndex(row.stop.findData(STOP_DATA))
    row.operand.setCurrentIndex(row.operand.findData("u16"))
    row.shared.setChecked(True)
    assert form.line.text() == "!03=[str] @upper:u16+"
    form.then_return.setChecked(True)
    assert form.line.text() == "!03=[str] @upper:u16+ return"
    # A kind change shows what that kind takes.
    form.kind.setCurrentIndex(form.kind.findData(TokenKind.CODE))
    assert form.operands_box.isVisibleTo(form) and not form.params_box.isVisibleTo(form)
    assert form.text_label.text() == "Label"
    # A key that is not whole digits stays in bits.
    form.line.setText("%101=x")
    assert form.key_mode.value() == "bits" and form.key_width.text() == "3 bits"
    form.key_mode.button("hex").click()
    assert form.key_mode.value() == "bits" and "not whole" in form.problem.text()
    # Pressing the mode already down changes nothing.
    form.line.setText("%1010=x")
    for _ in range(2):
        form.key_mode.button("bits").click()
    assert form.key_mode.value() == "bits" and form.key.text() == "1010"
    form.line.setText("41=A")
    form.key_mode.button("hex").click()
    assert form.key_mode.value() == "hex" and form.key.text() == "41"
    # What the form cannot spell is said, not silently dropped.
    form.line.setText("41=[unclosed")
    assert "unclosed" in form.problem.text()


def test_the_table_editor_edits_in_the_grid_and_the_form(window, tmp_path):
    from mapchar.ui.table_editor import COMMENT, TEXT

    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    table = table_entry.table
    # A row selected loads the form, and Apply puts it back changed.
    editor.grid.selectRow(grid_row(editor, "41"))
    assert editor.add.text() == "Apply" and editor.form.key.text() == "41"
    editor.form.text.setText("a")
    editor._add()
    assert table.entries["01000001"].text == "a"
    # The key changed in the form moves the entry.
    editor.form.key.setText("4A")
    editor._add()
    assert "01000001" not in table.entries and table.entries["01001010"].text == "a"
    # Text and Comment cells are typed over in place, one undo step each.
    row = grid_row(editor, "42")
    type_in_grid(editor, row, TEXT, "bee")
    assert table.entries["01000010"].text == "bee"
    type_in_grid(editor, grid_row(editor, "42"), COMMENT, "the letter B")
    assert table.entries["01000010"].comment == "the letter B"
    assert table_entry.table_overlay["01000010"] == "# the letter B\n42=bee"
    window.undo_stack.undo()
    assert table.entries["01000010"].comment == ""
    # The filter drops what does not match key, text or comment.
    editor.filter.setText("bee")
    assert grid_keys(editor) == ["42"]


def test_the_grid_spells_a_row_when_it_is_looked_at_and_the_filter_drops_rows(
    window, tmp_path
):
    """A table including an encoding is tens of thousands of entries: the grid
    holds them as rows of a model, spelled as they are drawn, and the filter
    leaves out what does not match rather than the view hiding it."""
    from mapchar.ui.table_editor import TEXT

    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    table = table_entry.table
    editor.includes.setText("shift-jis")
    editor.includes.editingFinished.emit()
    model = editor.entry_model
    assert model.rowCount() > 7000
    # Filling it spells only the sample the column widths are measured from;
    # every other row waits for the view to draw it.
    spelled = sum(row.cells is not None for row in model._rows)
    assert spelled <= 200 < model.rowCount()
    assert model.index(model.row_of("01000001"), TEXT).data() == "A"

    # The filter leaves the rows that match, and Select All reaches those alone.
    editor.includes.setText("")
    editor.includes.editingFinished.emit()
    editor.filter.setText("A")
    assert grid_keys(editor) == ["41"]
    editor.grid.selectAll()
    editor._remove()
    assert "01000001" not in table.entries and "01000010" in table.entries


def test_the_editor_moves_on_to_the_next_key_and_removes_on_del(
    window, tmp_path, qtbot
):
    from PySide6.QtCore import Qt

    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    table = table_entry.table
    editor.form.line.setText("50=P")
    editor._add()
    assert table.entries["01010000"].text == "P"
    # The next key of the same width is ready, its text blank, focus on it.
    assert editor.form.key.text() == "51" and editor.form.text.text() == ""
    assert editor.add.text() == "Add"
    # A switch whose text has no brackets gets a word, not a refusal.
    editor.form.line.setText("!F1=item @main:1")
    assert "brackets" in editor.form.problem.text()
    editor.form.line.setText("!F1=[item] @main:1")
    assert editor.form.problem.text() == ""
    # Several rows selected: the form waits, and Del removes them all at once.
    editor.grid.selectAll()
    assert not editor.add.isEnabled() and editor.remove.isEnabled()
    qtbot.keyClick(editor.grid, Qt.Key.Key_Delete)
    assert not table.entries and "Removed" in editor.status.text()
    window.undo_stack.undo()
    assert len(table.entries) == 4


def test_the_editor_says_where_a_selection_came_from_and_adds_it_byte_by_byte(
    window, tmp_path
):
    data = b"\x41\x42\x43\x00" + b"\xff" * 8
    open_rom_and_table(window, tmp_path, data)
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    # A key typed in by hand came from nowhere, so the sample line stays out.
    editor.form.line.setText("42=B")
    assert editor.sample.text() == ""
    # Add to Table… queues the selected bytes one entry each, saying where.
    window._selection = (0, 3)
    window._add_selection_to_table()
    assert editor.form.key.text() == "41" and "2 more" in editor.status.text()
    assert editor.sample.text() == "sampled from 000000"
    editor.form.text.setText("a")
    editor._add()
    assert editor.form.key.text() == "42" and "1 more" in editor.status.text()
    assert editor.sample.text() == "sampled from 000001"
    editor.form.text.setText("b")
    editor._add()
    assert editor.form.key.text() == "43" and "more" not in editor.status.text()
    editor.form.text.setText("c")
    editor._add()
    # The queue spent, the form moves on by key as usual.
    assert editor.form.key.text() == "44"
    assert [
        table_entry.table.entries[k].text for k in ("01000001", "01000010", "01000011")
    ] == ["a", "b", "c"]


def test_rename_table_follows_every_reference(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    from mapchar.core.table import Table

    data = b"\x41\xf1\x01\x00" + b"\xff" * 8
    file_entry = open_rom_and_table(window, tmp_path, data)
    main = window.workspace.table_entries()[0]
    items = Table("items")
    window._add_memory_table(items, "items.tbl")
    items_entry = window.workspace.entry_for_table("items")
    editor = window.table_editor
    window._edit_table_entry(items_entry)
    editor.form.line.setText("01=Herb")
    editor._add()
    window._edit_table_entry(main)
    editor.form.line.setText("!F1=[item] @items:1")
    editor._add()
    block = add_block(window, file_entry, "b", RangeSource(0, 4))
    assert block.config.table_id == "main"

    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("stuff", True))
    window._edit_table_entry(items_entry)
    window._rename_table(items_entry)
    assert items.id == "stuff" and "items" not in window.workspace.loaded_tables()
    switch = main.table.entries["11110001"]
    assert switch.params[0].table_id == "stuff"
    assert (
        editor.title.text().endswith("items.tbl")
        and "@stuff" in editor.table_pick.currentText()
    )
    # The main table's rename reaches the block reading through it.
    monkeypatch.setattr(QInputDialog, "getText", lambda *a, **k: ("font", True))
    window._rename_table(main)
    assert main.table.id == "font" and block.config.table_id == "font"
    assert window._current_table_id() == "font"
    # One undo step each way, however many references followed.
    window.undo_stack.undo()
    assert main.table.id == "main" and block.config.table_id == "main"
    window.undo_stack.undo()
    assert (
        items.id == "items"
        and switch.params[0].table_id == "items"
        or (main.table.entries["11110001"].params[0].table_id == "items")
    )
    window.undo_stack.redo()
    window.undo_stack.redo()
    assert main.table.id == "font" and items.id == "stuff"
    # The project carries a renamed id in place of the file's.
    assert main.table_id == "font"
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert window.open_project(str(proj))
    back = window.workspace.table_entries()
    assert {e.table.id for e in back} == {"font", "stuff"}
    # Saved as a file, the file says the new id and the project need not.
    font = next(e for e in back if e.table.id == "font")
    window._save_table_entry(font)
    assert font.table_id is None and "@table font" in Path(str(font.path)).read_text()


def test_the_editor_sorts_and_chooses_columns(window, tmp_path):
    from PySide6.QtCore import Qt

    from mapchar.ui.table_editor import KEY, TEXT, WEIGHT

    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.table_entries()[0]
    window._edit_table_entry(table_entry)
    editor = window.table_editor
    editor.form.line.setText("0041=z")
    editor._add()

    # By key: width first, then bits — not the spelling.
    assert grid_keys(editor) == ["00", "41", "42", "0041"]
    editor.grid.sortByColumn(TEXT, Qt.SortOrder.DescendingOrder)
    assert grid_keys(editor)[0] == "0041"
    editor.grid.sortByColumn(KEY, Qt.SortOrder.AscendingOrder)
    # Weight hides itself until the table weights something.
    assert editor.grid.isColumnHidden(WEIGHT)
    editor.form.line.setText("44<2>=D")
    editor._add()
    assert not editor.grid.isColumnHidden(WEIGHT)
    # A column chosen away stays away.
    menu = editor.column_menu()
    action = next(a for a in menu.actions() if a.text() == "Weight")
    action.setChecked(False)
    assert editor.grid.isColumnHidden(WEIGHT)
    editor.set_entry(table_entry)
    assert editor.grid.isColumnHidden(WEIGHT)
    editor._columns.clear()  # back to following the table, for the tests after
    # Save writes back a native file; a converted one needs Save As File….
    assert editor.save.isEnabled() and editor.save_as.isEnabled()
    table_entry.dialect = "abcde"
    editor.set_entry(table_entry)
    assert not editor.save.isEnabled()


def test_adding_relative_search_entries_to_a_table_undoes(
    window, tmp_path, monkeypatch
):
    """Build Table ▸ add to the current table is a table change like any other:
    one step, and undoing it takes the entries back out."""
    from PySide6.QtWidgets import QInputDialog, QMessageBox

    from mapchar.engines.relsearch import UPPER, Hit

    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("A-Z", True))
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    hit = Hit(0, 1, "little", {UPPER: 0x80}, tuple(range(0x80, 0x86)))
    window._build_table_from_hit(hit)
    assert len(table_entry.table.entries) > before
    window.undo_stack.undo()
    assert len(table_entry.table.entries) == before


def test_refresh_tables_reads_every_file_and_only_refreshes_changes(
    window, tmp_path, monkeypatch
):
    rom = tmp_path / "r.bin"
    rom.write_bytes(b"AB\x00")
    quiet = tmp_path / "quiet.tbl"
    quiet.write_text(TABLE)
    edited = tmp_path / "edited.tbl"
    edited.write_text(TABLE.replace("@table main", "@table other"))
    window.open_rom(str(rom))
    window.open_table(str(quiet))
    changed = window.open_table(str(edited))
    refreshed = []
    monkeypatch.setattr(
        type(window), "_tables_changed", lambda self: refreshed.append(True)
    )

    window.refresh_tables()
    assert not refreshed
    assert "up to date" in window.statusBar().currentMessage()

    edited.write_text(TABLE.replace("@table main", "@table other") + "43=C\n")
    window.refresh_tables()
    assert refreshed == [True]
    assert "01000011" in changed.table.entries
    assert window.statusBar().currentMessage() == "Reloaded 1 table"
