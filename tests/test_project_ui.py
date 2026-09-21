"""The project file: what a save carries, what a load reads back, and what
becomes of a file that has moved or is not there any more.

The autosave copy and the session recovered from one belong here too: they are
the project written and read under another name.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from helpers import texts
from mapchar.core.block import RangeSource
from mapchar.project.entry import Entry, EntryKind
from mapchar.ui.main_window import MainWindow
from window_helpers import (
    TABLE,
    ab_ba_rom,
    add_block,
    item_for,
    open_rom_and_table,
)


def test_table_reload_and_container_info(window, tmp_path, monkeypatch):
    rom = tmp_path / "r.bin"
    rom.write_bytes(b"AB\x00")
    tbl = tmp_path / "t.tbl"
    tbl.write_text(TABLE)
    entry = window.open_rom(str(rom))
    table_entry = window.open_table(str(tbl))
    assert str(tbl) in window.table_watcher.files()
    tbl.write_text(TABLE + "43=C\n")
    window.reload_table(table_entry)
    assert "01000011" in table_entry.table.entries
    shown = []
    from mapchar.ui.dialogs import TextDialog

    original = TextDialog.__init__
    monkeypatch.setattr(
        TextDialog,
        "__init__",
        lambda self, title, text, parent=None: (
            shown.append(text),
            original(self, title, text, parent),
        )[1],
    )
    window._container_info(entry)
    assert shown and "Flat file" in shown[0]
    # A described field's detail is what the container did with the value, which
    # is the whole reason to open this rather than look at the bytes.
    assert "Nothing is stripped" in shown[0]


def test_project_reads_clean_until_the_session_moves(window, tmp_path):
    data = ab_ba_rom(20)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    # A session never saved as a project has no file to differ from.
    assert not window._project_dirty()
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert not window._project_dirty()
    # The view position is part of what a save stores, so moving it shows at once
    # rather than at the next entry switch.
    window._go_to(4)
    assert window._project_dirty()
    assert window._write_project(str(proj))
    assert not window._project_dirty()
    # And a reopened project is clean, though showing the restored entry ran its
    # session through the live widgets.
    window._new_project()
    assert window.open_project(str(proj))
    assert not window._project_dirty()
    assert window.workspace.current is not None


def test_opening_a_project_reads_its_blocks(window, tmp_path):
    data = ab_ba_rom(20)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    window._activate_entry(file_entry)
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert window.open_project(str(proj))
    file_entry, block = window.workspace.entries[:2]
    # The file is the one on screen, yet the block is read and counted.
    assert window._entry is file_entry
    assert texts(block.doc.strings) == ["AB[end]", "BA[end]"]
    assert item_for(window, block).text(0) == "b  (2)"
    assert not window._project_dirty()


@pytest.mark.parametrize("folders", [0, 2])
def test_the_files_panel_dresses_each_row_a_bounded_number_of_times(
    window, tmp_path, monkeypatch, folders
):
    """Opening a project, and reading every block for Project Strings, costs the
    Files panel a few passes over its rows, not one per block: a project of
    hundreds of blocks would otherwise take the square of that — with the
    blocks in folders, one inside the other, as much as without."""
    data = ab_ba_rom(0) * 40
    file_entry = open_rom_and_table(window, tmp_path, data)
    blocks = [
        add_block(window, file_entry, f"b{n}", RangeSource(n * 6, n * 6 + 6))
        for n in range(40)
    ]
    holder = file_entry
    for _ in range(folders):
        holder = window._new_folder(holder, [holder])
    if folders:
        window._place_entries(blocks, holder, None)
    window._activate_entry(file_entry)
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    panel = type(window.files_panel)
    dressed: list[Entry] = []
    dress = panel._dress

    def counted(self, entry, *rest):
        dressed.append(entry)
        dress(self, entry, *rest)

    monkeypatch.setattr(panel, "_dress", counted)
    assert window.open_project(str(proj))
    rows = len(window.workspace.entries)
    assert rows == 42 + folders
    assert len(dressed) <= 3 * rows
    blocks = window.workspace.of_kind(EntryKind.BLOCK)
    assert all((b.folder is not None) == bool(folders) for b in blocks)
    assert all(item_for(window, b).text(0) == f"{b.name}  (2)" for b in blocks)
    dressed.clear()
    window.workspace.invalidate_extractions()
    window.project_strings.show()
    window._refresh_project_strings()
    assert window.project_strings.results.rowCount() == 80
    assert len(dressed) <= 3 * rows


def test_opening_a_project_leaves_a_missing_files_blocks_unread(window, tmp_path):
    data = ab_ba_rom(20)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    window._activate_entry(file_entry)
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    Path(str(file_entry.path)).unlink()
    window._new_project()
    assert window.open_project(str(proj))
    assert window.workspace.entries[1].doc is None


def test_saving_a_project_offers_to_write_the_unsaved_edits(window, tmp_path):
    data = ab_ba_rom(20)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    window._on_translation_edited(0, "BA[end]")
    assert file_entry.dirty
    # The fixture answers the gate with "Continue Without": the project saves and
    # the edit stays in memory, unwritten.
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert file_entry.dirty
    assert Path(str(file_entry.path)).read_bytes() == data


def test_locate_repoints_a_moved_file_and_reloads_it(window, tmp_path, monkeypatch):
    data = ab_ba_rom(20)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    moved = tmp_path / "moved.bin"
    Path(str(file_entry.path)).rename(moved)

    window._new_project()
    assert window.open_project(str(proj))  # the offer is declined by the fixture
    assert window.locate_action.isEnabled()
    monkeypatch.setattr(MainWindow, "_pick_open", lambda self, *a, **k: str(moved))
    window._relocate_missing()
    assert not window.locate_action.isEnabled()
    names = [e.name for e in window.workspace.entries]
    assert names == ["moved.bin", "b", "main.tbl"]  # named after the file, so renamed
    file_entry, block = window.workspace.entries[:2]
    assert block.path == str(moved) and file_entry.path == str(moved)
    window._activate_entry(block)
    assert texts(block.doc.strings) == ["AB[end]", "BA[end]"]


# --- Open Recent -------------------------------------------------------------


def test_open_recent_normalises_prunes_and_clears(window, tmp_path):
    one, two = tmp_path / "one.mapchar", tmp_path / "two.mapchar"
    for p in (one, two):
        p.write_text('{"version": 1, "entries": []}')
    window._add_recent(str(one))
    # The same project spelled another way is the same row, not a second one.
    window._add_recent(str(tmp_path / "sub" / ".." / "one.mapchar"))
    assert window._recent() == [str(one)]
    window._add_recent(str(two))
    assert window._recent() == [str(two), str(one)]  # newest first
    two.unlink()
    window._rebuild_recent()  # what opening the File menu does
    assert window._recent() == [str(one)]
    labels = [a.text() for a in window.recent_menu.actions()]
    assert labels == ["one.mapchar", "", "Clear List"]
    window._clear_recent()
    assert window._recent() == [] and not window.recent_menu.isEnabled()


def test_a_plugin_refresh_keeps_a_clean_block_s_originals(window, tmp_path):
    """F5 drops every cached document it can; a block's originals live in one,
    so they have to be stashed on the entry on the way out."""
    data = ab_ba_rom(20)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._on_translation_edited(0, "BB[end]")
    assert window._write_blocks([block])
    assert not file_entry.dirty
    window._reload_plugins = lambda project_dir: (window.registry, [])
    window._refresh_plugins()
    assert block.doc is not None
    assert block.doc.strings[0].current_text() == "BB[end]"
    assert block.doc.strings[0].original == "AB[end]"
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert '"o": "AB[end]"' in Path(proj).read_text()


def test_a_project_holding_translations_puts_them_in_the_bytes(window, tmp_path):
    """A project written before originals were kept carried translations
    that were not in the ROM. Opening it lands them: the file reads unsaved,
    and Write All writes them."""
    import json

    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6), fill=b"\xee")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    doc = json.loads(proj.read_text())
    block_dict = next(e for e in doc["entries"] if e["kind"] == "block")
    block_dict["strings"] = [{"i": 0, "t": "A[end]", "s": "edited"}]
    proj.write_text(json.dumps(doc))
    window._new_project()
    assert window.open_project(str(proj))
    back = window.workspace.of_kind(EntryKind.BLOCK)[0]
    rom = window.workspace.files()[0]
    assert rom.dirty and back.doc.strings[0].current_text() == "A[end]"
    assert back.doc.strings[0].original == "AB[end]"
    assert window._write_all()
    assert (
        Path(file_entry.path).read_bytes()
        == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    )
    assert not rom.dirty


def test_a_project_holding_translations_keeps_them_while_its_table_is_gone(
    window, tmp_path
):
    """The table a block reads through is not there, so nothing can be laid
    out: the translations an older project was holding stay in the project,
    the load says so in its notices, and they land when the table is back."""
    import json

    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6), fill=b"\xee")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    doc = json.loads(proj.read_text())
    block_dict = next(e for e in doc["entries"] if e["kind"] == "block")
    block_dict["strings"] = [{"i": 0, "t": "A[end]", "s": "edited"}]
    proj.write_text(json.dumps(doc))
    table_file = tmp_path / "main.tbl"
    table_text = table_file.read_text()
    table_file.unlink()
    window._new_project()
    assert window.open_project(str(proj))

    back = window.workspace.of_kind(EntryKind.BLOCK)[0]
    rom = window.workspace.files()[0]
    # Nothing was read, so nothing landed — and nothing was dropped either.
    assert not rom.dirty and not back.doc.strings
    assert back.pending_strings[0].translation == "A[end]"
    assert any("translation(s)" in n for n in window._load_notices)
    # A save writes them out again, so they outlive the session that could not
    # place them.
    again = tmp_path / "q.mapchar"
    assert window._write_project(str(again))
    saved = next(
        e for e in json.loads(again.read_text())["entries"] if e["kind"] == "block"
    )
    assert saved["strings"] == [{"i": 0, "t": "A[end]", "s": "edited"}]
    # With the table back they land as they always would have.
    table_file.write_text(table_text)
    window.reload_table(window.workspace.of_kind(EntryKind.TABLE)[0])
    window._extract_current(back, back.doc, window._table_set_of(back))
    assert back.doc.strings[0].current_text() == "A[end]"
    assert rom.dirty


def test_an_older_project_s_translation_that_would_re_cut_the_block_is_refused(
    window, tmp_path
):
    """The bytes are the translation, so they must say what the translator
    said: a translation that would make the block read as other strings is
    kept in the notes and reported, not written."""
    import json

    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    # The spare room a shorter string leaves is filled with the end token,
    # which the table maps and the block therefore reads as text: it would
    # read as three strings rather than two.
    add_block(window, file_entry, "b", RangeSource(0, 6), fill=b"\x00")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    doc = json.loads(proj.read_text())
    block_dict = next(e for e in doc["entries"] if e["kind"] == "block")
    block_dict["strings"] = [{"i": 0, "t": "A[end]", "s": "edited"}]
    proj.write_text(json.dumps(doc))
    window._new_project()
    assert window.open_project(str(proj))

    back = window.workspace.of_kind(EntryKind.BLOCK)[0]
    rom = window.workspace.files()[0]
    assert [r.current_text() for r in back.doc.strings] == ["AB[end]", "BA[end]"]
    assert not rom.dirty
    assert back.doc.strings[0].unwritten == "A[end]"
    assert any("3 strings instead of 2" in n for n in window._load_notices)


def test_autosave_writes_a_copy_and_offers_it_back(window, tmp_path, monkeypatch):
    """The copy beside the project is written on the timer and after a write,
    goes with a save, and is offered when the project is next opened."""
    import time

    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    copy = tmp_path / "p.mapchar.autosave"
    window._autosave()
    assert not copy.exists()  # nothing changed since the save
    window._on_translation_edited(0, "BB[end]")
    assert window._write_blocks([block])  # a write saves a copy at once
    assert copy.exists() and '"o": "AB[end]"' in copy.read_text()
    assert window._write_project(str(proj))
    assert not copy.exists()  # the save made it redundant
    # A copy newer than the project is offered back on open.
    block.doc.strings[0].notes = "from the copy"
    window._autosave()
    assert copy.exists()
    time.sleep(0.05)
    os.utime(str(proj), (time.time() - 10, time.time() - 10))
    window._new_project()
    monkeypatch.setattr(window, "_ask", lambda *a, **k: True)
    assert window.open_project(str(proj))
    assert window.project_path == str(proj)
    back = window.workspace.of_kind(EntryKind.BLOCK)[0]
    assert back.doc.strings[0].notes == "from the copy"
    assert window._project_dirty()  # the copy is not what the file holds
    # And the copy is still there: until the project is saved it is the only
    # place the recovered work exists, so a crash now finds it rather than the
    # older file.
    assert copy.exists() and "from the copy" in copy.read_text()
    assert window._write_project(str(proj))
    assert not copy.exists()  # saved: the file says it now


def test_recovering_a_session_keeps_its_copy_and_never_names_it(
    window, tmp_path, monkeypatch
):
    """A session that was never saved as a project is recovered from the copy
    in the data folder: the copy stays until the session is saved, and it is
    not a project the user has, so nothing lists it."""
    window.plugin_dir = str(tmp_path / "data" / "plugins")
    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    block.doc.strings[0].notes = "from the session"
    window._autosave()
    copy = tmp_path / "data" / "autosave" / "unsaved.mapchar"
    assert copy.exists()

    window._new_project()
    assert not window.project_path and not window.workspace.entries
    monkeypatch.setattr(window, "_ask", lambda *a, **k: True)
    window.offer_session_recovery()
    back = window.workspace.of_kind(EntryKind.BLOCK)[0]
    assert back.doc.strings[0].notes == "from the session"
    assert window.project_path is None
    assert copy.exists()  # the session still has nowhere else to be
    # The copy is not a project: Open Recent and the last folder used say
    # nothing about it.
    assert str(copy) not in window._recent()
    assert str(copy.parent) != window.settings.value("last_dir", "")
