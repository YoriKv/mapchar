"""Blocks on screen: reading one, editing its strings and writing them back,
the preview box the text is wrapped to, and the room a shortened string leaves
the block."""

from __future__ import annotations

from pathlib import Path

import pytest

from helpers import pointer_rom, texts
from mapchar.core.block import RangeSource, Status
from mapchar.project.entry import Entry, EntryKind
from mapchar.ui.token_text import POINTER_TOKENS
from window_helpers import (
    ABCDE_TABLE,
    ASCII_TABLE,
    TABLE,
    ab_ba_rom,
    add_block,
    open_rom_and_table,
)


def test_edit_and_write(window, tmp_path):
    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6), fill=b"\xee")
    window._on_translation_edited(0, "A[end]")
    rec = block.doc.strings[0]
    # The translation is the bytes: the string's slot holds it, fill after.
    assert rec.current_text() == "A[end]" and rec.original == "AB[end]"
    assert rec.status is Status.EDITED
    assert file_entry.dirty and not block.dirty
    assert file_entry.doc.data == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    rows = window._row_data(block, block.doc)
    assert (rows[0].used, rows[0].room, rows[0].status) == (2, 3, "edited")
    # An edit that does not fit never lands: the bytes cannot hold it.
    steps = window.undo_stack.count()
    window._on_translation_edited(1, "BBB[end]")
    assert window.undo_stack.count() == steps
    assert block.doc.strings[1].current_text() == "BA[end]"
    assert window._write_blocks([block])
    assert (
        Path(file_entry.path).read_bytes()
        == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    )
    assert not file_entry.dirty
    # A write moves no string state.
    assert rec.current_text() == "A[end]" and rec.original == "AB[end]"
    window.undo_stack.undo()  # the write itself: the file goes back, the buffer stays
    assert Path(file_entry.path).read_bytes() == data
    assert block.doc.strings[0].current_text() == "A[end]" and file_entry.dirty
    window.undo_stack.redo()
    assert Path(file_entry.path).read_bytes() != data and not file_entry.dirty
    window.undo_stack.undo()
    window.undo_stack.undo()  # the edit too: back to the bytes on disk, and clean
    assert block.doc.strings[0].current_text() == "AB[end]"
    assert not file_entry.dirty and file_entry.doc.data == data
    window.overtype_bytes(1, b"\x43")
    assert file_entry.dirty and file_entry.doc.data[1] == 0x43
    # A hex edit inside the block reads as an edit of its string.
    assert block.doc.strings[0].current_text() == "A[$43][end]"
    assert block.doc.strings[0].status is Status.EDITED
    window.undo_stack.undo()
    assert file_entry.doc.data[1] == 0x42


def test_pointer_block_in_window(window, tmp_path, monkeypatch):
    from mapchar.core.block import PointerTableSource

    data = pointer_rom((0x10, 0x13), "41 42 00 42 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "P", RangeSource(0x10, 0x15), bound=0x18)
    assert texts(block.doc.strings) == ["AB[end]", "B[end]"]
    monkeypatch.setattr("mapchar.ui.dialogs.PointerSearchDialog.exec", lambda self: 1)
    monkeypatch.setattr("mapchar.ui.dialogs.DiscoveryDialog.exec", lambda self: 1)
    window._find_pointers()
    assert block.config.source == PointerTableSource(0, 4, 2, 2, "little", "linear", 0)
    rows = window._row_data(block, block.doc)
    assert rows[0].pointers == "0" and rows[1].pointers == "2"
    window._go_to(0)
    # A pointer block reads as pointers: its table is Pointer, and each of its
    # pointers is one token saying where it points.
    assert window.mode_toggle.value() is True
    tokens = window.raw._model.tokens
    assert [(t.bit_start, t.bit_end, t.text()) for t in tokens[:2]] == [
        (0, 16, "→10"),
        (16, 32, "→13"),
    ]
    assert all(t.table_id == POINTER_TOKENS for t in tokens[:2])
    window._on_translation_edited(0, "ABB[end]")
    assert window._write_blocks([block])
    written = Path(file_entry.path).read_bytes()
    assert written[0x10:0x18] == bytes.fromhex("41 42 42 00 42 00 FF FF")
    assert written[:4] == bytes.fromhex("10 00 14 00")


def test_preview_and_wrap(window, tmp_path):
    from mapchar.core.font import CodeEffect, Effect, TextBox

    file_entry = open_rom_and_table(
        window, tmp_path, b"AB CD EF GH IJ\x00", table=ASCII_TABLE + "FE=[line]\\n\n"
    )
    block = add_block(window, file_entry, "F", RangeSource(0, 15))
    window._show_preview()
    assert block.box is not None
    # Counted, not measured: the wrap must not depend on which families the
    # machine running the tests happens to have.
    window._on_box_changed(
        TextBox(
            width=24,
            height=16,
            line_height=8,
            chars_per_line=5,
            effects={"line": CodeEffect(Effect.NEWLINE)},
        )
    )
    rows = window._row_data(block, block.doc)
    assert rows[0].status == "overflows box"
    window.strings.select_index(0)
    window._wrap_selected()
    text = block.doc.strings[0].current_text()
    assert "[line]" in text
    assert window.preview_window.status.text().startswith("page 1/")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert window.open_project(str(proj))
    blocks = [e for e in window.workspace.entries if e.kind is EntryKind.BLOCK]
    assert (
        blocks[0].box.width == 24
        and blocks[0].box.chars_per_line == 5
        and blocks[0].box.effects["line"].effect is Effect.NEWLINE
    )


def test_blocks_and_bookmarks_never_share_a_name(window, tmp_path):
    data = ab_ba_rom(0)
    file_entry = open_rom_and_table(window, tmp_path, data)
    first = add_block(window, file_entry, "b", RangeSource(0, 3))
    second = add_block(window, file_entry, "b", RangeSource(3, 6))
    assert (first.name, second.name) == ("b", "b (2)")
    window._commit_rename(second, "b")
    assert second.name == "b (2)"  # nothing to rename to
    window._commit_rename(second, "c")
    assert second.name == "c"
    window._commit_rename(first, "c")
    assert first.name == "c (2)"
    bookmark = Entry(EntryKind.BOOKMARK, "c", file_entry.path, parent=file_entry)
    window._push_add(bookmark)
    assert bookmark.name == "c (3)"


def test_a_command_file_s_repeated_block_names_are_numbered(window, tmp_path):
    rom = tmp_path / "c.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 00") + b"\xff" * 8)
    (tmp_path / "main.tbl").write_text(ABCDE_TABLE)
    block = (
        "#BLOCK NAME: Script\n#TYPE: NORMAL\n#METHOD: RAW\n#SCRIPT START: 0\n"
        "#SCRIPT STOP: $5\n#TABLE: main.tbl\n#COMMENTS: No\n#END BLOCK\n"
    )
    (tmp_path / "cmd.txt").write_text(block * 2)
    window.open_rom(str(rom))
    created = window.import_cartographer(str(tmp_path / "cmd.txt"))
    assert [e.name for e in created] == ["Script", "Script (2)"]


def test_fill_pick_leaves_blocked_signals_blocked(qtbot):
    from PySide6.QtWidgets import QComboBox

    from mapchar.ui.widgets import fill_pick

    combo = QComboBox()
    qtbot.addWidget(combo)
    combo.blockSignals(True)
    fill_pick(combo, [("a", 1)])
    assert combo.signalsBlocked()
    combo.blockSignals(False)
    fill_pick(combo, [("a", 1)])
    assert not combo.signalsBlocked()


def test_switching_entries_keeps_the_block_s_document(window, tmp_path):
    """Restoring a session picks the entry's table with signals blocked; the
    pick must not fire and drop the block's document on every switch."""
    data = ab_ba_rom(4)
    file_entry = open_rom_and_table(window, tmp_path, data)
    other = tmp_path / "other.tbl"
    other.write_text(TABLE.replace("@table main", "@table other"))
    window.open_table(str(other))
    file_entry.session.table_id = "other"
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    doc = block.doc
    window._activate_entry(file_entry)
    assert window.format_pick.currentData() == "other"
    window._activate_entry(block)
    assert window.format_pick.currentData() == "main"
    assert block.doc is doc


def test_a_block_with_no_configuration_still_refreshes(window, tmp_path):
    """A project file writes a block's configuration only when it has one, so a
    config-less block round-trips — and the refresh has to survive it, rather
    than reading ``entry.config`` as though a block always had one."""
    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00CD\x00")
    block = Entry(
        EntryKind.BLOCK, "No reading", file_entry.path, parent=file_entry, config=None
    )
    window._push_add(block)
    window._activate_entry(block)
    assert window._entry is block
    assert window.block_label.text().startswith("No reading")
    window._refresh_view()
    window._refresh_view(moved=True)


# --- the room a shortened block remembers, and the question it arms ---------


def _edited_pointer_block(window, tmp_path):
    """A pointer block whose second string has been typed one byte shorter,
    so the block remembers the room it gave up."""
    from mapchar.core.block import PointerTableSource

    data = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", PointerTableSource(0, 4, 2, 2))
    assert texts(block.doc.strings) == ["AB[end]", "BA[end]"]
    window._on_translation_edited(1, "B[end]")
    return file_entry, block


def test_a_shortened_string_leaves_the_block_the_room_it_gave_up(window, tmp_path):
    """A pointer block with no Bound ends where its text ends, so the room a
    shorter translation frees would be gone at the next edit: the block
    remembers the extent it had instead, and the original goes back in."""
    file_entry, block = _edited_pointer_block(window, tmp_path)
    assert block.room == 0x16
    assert file_entry.doc.data[0x10:0x16] == bytes.fromhex("41 42 00 42 00 FF")
    window._on_translation_edited(1, "BA[end]")
    assert block.doc.strings[1].current_text() == "BA[end]"
    assert file_entry.doc.data[0x10:0x16] == ab_ba_rom(0)
    # A write that fills the room again leaves the room remembered.
    assert block.room == 0x16


def test_a_reading_change_on_an_edited_block_asks_once(window, tmp_path, monkeypatch):
    """Changing how an edited block is read cuts its strings out of the bytes
    afresh and forgets its room, so it asks first — once for the run of
    changes that follows, not once per keystroke."""
    _file_entry, block = _edited_pointer_block(window, tmp_path)
    asked: list[str] = []
    monkeypatch.setattr(
        type(window), "_ask", lambda _s, _t, message: asked.append(message) or True
    )
    window.reading_bar.spp.setValue(2)
    assert len(asked) == 1 and "edited strings" in asked[0]
    assert block.config.strings_per_pointer == 2 and block.room is None
    window.reading_bar.spp.setValue(3)
    assert len(asked) == 1 and block.config.strings_per_pointer == 3


def test_a_refused_reading_change_leaves_the_block_and_the_bar_as_they_were(
    window, tmp_path, monkeypatch
):
    _file_entry, block = _edited_pointer_block(window, tmp_path)
    steps = window.undo_stack.count()
    monkeypatch.setattr(type(window), "_ask", lambda *a: False)
    window.reading_bar.spp.setValue(2)
    assert block.config.strings_per_pointer == 1 and block.room == 0x16
    assert window.undo_stack.count() == steps
    # The bar shows the block's own reading again, not what was typed into it.
    assert window.reading_bar.spp.value() == 1


def test_a_string_edit_arms_the_question_again(window, tmp_path, monkeypatch):
    """The consent covers the strings the user agreed to have re-cut; one
    edited after it is not among them, so the next change asks again."""
    _file_entry, block = _edited_pointer_block(window, tmp_path)
    asked: list[str] = []
    monkeypatch.setattr(
        type(window), "_ask", lambda _s, _t, message: asked.append(message) or True
    )
    window.reading_bar.realign_m.setValue(2)
    assert len(asked) == 1 and block.room is None
    window._on_translation_edited(0, "A[end]")
    assert block.doc.strings[0].current_text() == "A[end]" and block.room == 0x16
    window.reading_bar.realign_m.setValue(4)
    assert len(asked) == 2


def test_what_one_adjusts_while_editing_never_asks(window, tmp_path, monkeypatch):
    """A bound, a fill, the table the translation is written in and the block's
    name leave every string where it is, so none of them asks."""
    from dataclasses import replace

    _file_entry, block = _edited_pointer_block(window, tmp_path)
    monkeypatch.setattr(type(window), "_ask", lambda *a: pytest.fail("asked"))
    window.reading_bar.writing.bound.setText("20")
    window.reading_bar.writing.bound.editingFinished.emit()
    assert block.config.bound == 0x20
    window._push_block_edit(block, config=replace(block.config, fill=b"\x00"))
    window._push_block_edit(block, config=replace(block.config, table_id="other"))
    window._push_block_edit(block, name="renamed")
    assert block.name == "renamed" and block.room == 0x16


def test_an_unedited_block_is_re_read_without_a_question(window, tmp_path, monkeypatch):
    from mapchar.core.block import PointerTableSource

    data = pointer_rom((0x10, 0x13), "41 42 00 42 41 00", tail=8)
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", PointerTableSource(0, 4, 2, 2))
    monkeypatch.setattr(type(window), "_ask", lambda *a: pytest.fail("asked"))
    window.reading_bar.spp.setValue(2)
    assert block.config.strings_per_pointer == 2
    # The first run stops at the second pointer's string; the second reads on
    # into a row of its own, which no pointer reaches.
    assert [bool(rec.pointers) for rec in block.doc.strings] == [True, True, False]
    bar = window.reading_bar
    bar.run_to_next.setChecked(True)
    assert block.config.run_to_next and len(block.doc.strings) == 3
    # A fill that is the end token is no padding where ends are counted.
    assert not bar._groups["end_is_fill"].isEnabled()
    bar.run_to_next.setChecked(False)
    bar.spp.setValue(1)
    assert bar._groups["end_is_fill"].isEnabled()
    bar.end_is_fill.setChecked(True)
    assert block.config.end_is_fill
