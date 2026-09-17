"""One session stack: what a command reaches, what it merges with, and the
unsaved state that follows it back to clean.

The three invariants the commands exist to keep (``mapchar.ui.undo_commands``):
an entry undone back to what is on disk reads clean again, a step is reverted
where it was made, and an apply never pushes a command of its own.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from mapchar.core.block import RangeSource, Status
from mapchar.core.font import Font
from mapchar.core.table import Entry as TableEntry
from mapchar.core.table import TokenKind
from mapchar.ui.undo_commands import (
    BlockEditCommand,
    BoxCommand,
    ContainerCommand,
    FontCommand,
)
from window_helpers import add_block, make_window, open_rom_and_table

DATA = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


def _block(window, tmp_path, name="b", rom_name="rom.bin"):
    file_entry = open_rom_and_table(window, tmp_path, DATA, rom_name=rom_name)
    return file_entry, add_block(
        window, file_entry, name, RangeSource(0, 6), fill=b"\xee"
    )


# --- unsaved state follows undo -------------------------------------------


def test_an_undone_translation_reads_clean_again(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    assert not file_entry.dirty
    window._on_translation_edited(0, "B[end]")
    # The translation is in the file's bytes, so the file is what is unsaved.
    assert file_entry.dirty and not block.dirty
    assert file_entry.doc.data[:3] == bytes.fromhex("42 00 EE")
    window.undo_stack.undo()
    # The command restored the revision token the entry had before the edit
    # rather than minting a fresh one, so the entry is back to what is on disk.
    assert not file_entry.dirty
    assert block.doc.strings[0].current_text() == "AB[end]"
    window.undo_stack.redo()
    assert file_entry.dirty


def test_an_undone_overtype_reads_clean_again(window, tmp_path):
    file_entry, _ = _block(window, tmp_path)
    assert not file_entry.dirty
    window.overtype_bytes(1, b"\x43")
    assert file_entry.dirty and file_entry.doc.data[1] == 0x43
    window.undo_stack.undo()
    assert not file_entry.dirty and file_entry.doc.data[1] == 0x42


def test_an_undone_font_edit_reads_clean_again(window, tmp_path):
    from PySide6.QtGui import QImage

    sheet = tmp_path / "font.png"
    QImage(16, 16, QImage.Format.Format_ARGB32).save(str(sheet))
    entry = window.open_font(str(sheet))
    window.apply_font(entry, Font(str(sheet), 8, 8), window.workspace.next_revision())
    window.workspace.mark_saved(entry)
    assert not entry.dirty
    window._push_command(FontCommand(window, entry, entry.font, Font(str(sheet), 4, 4)))
    assert entry.dirty
    window.undo_stack.undo()
    assert not entry.dirty and entry.font.cell_width == 8


def test_an_undone_table_edit_reads_clean_again(window, tmp_path):
    _, block = _block(window, tmp_path)
    table_entry = window.workspace.entry_for_table("main")
    window.workspace.mark_saved(table_entry)
    before = deepcopy(table_entry.table)
    table_entry.table.add(TableEntry("01000011", TokenKind.TEXT, "C"))
    window._on_table_edited(table_entry, before)
    assert table_entry.dirty
    window.undo_stack.undo()
    assert not table_entry.dirty


def test_an_undone_box_edit_reads_clean_again(window, tmp_path):
    from mapchar.core.font import TextBox

    _, block = _block(window, tmp_path)
    window.workspace.mark_saved(block)
    window._push_command(BoxCommand(window, block, block.box, TextBox(width=64)))
    assert block.dirty and block.box.width == 64
    window.undo_stack.undo()
    assert not block.dirty and block.box is None


def test_an_undone_block_edit_leaves_the_project_clean(window, tmp_path):
    """A block's configuration is *project* state rather than entry bytes, so
    what an undo has to bring back to clean is the project's own unsaved
    marker."""
    from dataclasses import replace

    _, block = _block(window, tmp_path)
    assert window._write_project(str(tmp_path / "p.mapchar"))
    assert not window._project_dirty()
    before = (block.name, block.config, block.compression_id, block.spare_room)
    after = (
        "renamed",
        replace(block.config, source=RangeSource(0, 3)),
        block.compression_id,
        block.spare_room,
    )
    window._push_command(BlockEditCommand(window, block, before, after))
    assert window._project_dirty() and window.isWindowModified()
    window.undo_stack.undo()
    assert block.name == "b" and not window._project_dirty()
    assert not window.isWindowModified()


def test_an_undone_container_edit_leaves_the_project_clean(window, tmp_path):
    file_entry, _ = _block(window, tmp_path)
    other = tmp_path / "second.bin"
    other.write_bytes(DATA)
    assert window._write_project(str(tmp_path / "p.mapchar"))
    assert not window._project_dirty()
    before = (file_entry.container_id, file_entry.paths)
    after = ("raw", (file_entry.path, str(other)))
    window._push_command(ContainerCommand(window, file_entry, before, after))
    assert file_entry.extra_paths == (str(other),)
    assert window._project_dirty()
    window.undo_stack.undo()
    assert file_entry.extra_paths == ()
    assert not window._project_dirty()


def test_undoing_a_write_and_its_edit_reads_clean_again(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    assert window._write_blocks([block])
    assert not file_entry.dirty
    window.undo_stack.undo()  # the write
    assert file_entry.dirty
    window.undo_stack.undo()  # the edit
    # Both steps undone, the file is back to what is on disk, and reads so:
    # the write's undo put back the saved token it had before the write.
    assert not file_entry.dirty
    assert Path(file_entry.path).read_bytes() == DATA


# --- writes ----------------------------------------------------------------


def test_an_undone_write_puts_the_file_back_and_keeps_the_buffer(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    assert window._write_blocks([block])
    written = Path(file_entry.path).read_bytes()
    assert written != DATA and not file_entry.dirty
    # A write moves no string state: the original is the project's to keep.
    assert block.doc.strings[0].current_text() == "B[end]"
    assert block.doc.strings[0].original == "AB[end]"
    assert block.doc.strings[0].status is Status.EDITED
    assert window.undo_stack.undoText() == "Write rom.bin"

    window.undo_stack.undo()
    # The disk is as it was; the buffer still carries the edit, unsaved again.
    assert Path(file_entry.path).read_bytes() == DATA
    assert file_entry.doc.data == written and block.doc.data == written
    assert block.doc.strings[0].current_text() == "B[end]"
    assert file_entry.dirty and not block.dirty

    window.undo_stack.redo()
    assert Path(file_entry.path).read_bytes() == written
    assert not file_entry.dirty
    window._refresh_view()
    assert block.doc.strings[0].original == "AB[end]"


def test_an_undone_write_of_a_hex_edit_re_marks_the_file(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._activate_entry(file_entry)
    window.overtype_bytes(1, b"\x43")
    assert file_entry.dirty
    window._write_entry(file_entry)
    assert not file_entry.dirty and Path(file_entry.path).read_bytes()[1] == 0x43
    window.undo_stack.undo()
    # The disk is as it was; the buffer still carries the edit, unsaved again.
    assert Path(file_entry.path).read_bytes() == DATA
    assert file_entry.doc.data[1] == 0x43 and file_entry.dirty
    window.undo_stack.undo()  # the overtype itself
    assert file_entry.doc.data == DATA and not file_entry.dirty


def test_write_all_over_two_files_is_one_step(window, tmp_path):
    first_file, first = _block(window, tmp_path, "first", "one.bin")
    window._on_translation_edited(0, "B[end]")
    second_file, second = _block(window, tmp_path, "second", "two.bin")
    window._on_translation_edited(0, "A[end]")
    assert window._write_all()
    assert window.undo_stack.undoText() == "Write All"
    assert not first_file.dirty and not second_file.dirty
    window.undo_stack.undo()
    assert Path(first_file.path).read_bytes() == DATA
    assert Path(second_file.path).read_bytes() == DATA
    assert first_file.dirty and second_file.dirty
    assert first.doc.strings[0].current_text() == "B[end]"
    assert second.doc.strings[0].current_text() == "A[end]"


def test_a_file_changed_since_the_write_is_left_alone(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    assert window._write_blocks([block])
    Path(file_entry.path).write_bytes(b"\x00" * len(DATA))
    window.undo_stack.undo()
    assert Path(file_entry.path).read_bytes() == b"\x00" * len(DATA)
    assert window.errors and "changed on disk" in window.errors[-1]
    # Nothing in memory moved either: the file still reads as written.
    assert not file_entry.dirty and block.doc.strings[0].current_text() == "B[end]"


def test_a_refused_edit_leaves_no_step_and_no_bytes(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    steps = window.undo_stack.count()
    window._on_translation_edited(0, "BBB[end]")  # too long for its room
    assert window.undo_stack.count() == steps
    assert not file_entry.dirty and file_entry.doc.data == DATA
    assert "too long" in window.statusBar().currentMessage()
    window._on_translation_edited(0, "Z[end]")  # nothing encodes a Z
    assert window.undo_stack.count() == steps and file_entry.doc.data == DATA


# --- reach -----------------------------------------------------------------


def test_undoing_a_change_made_elsewhere_switches_back_to_it(window, tmp_path):
    file_entry, first = _block(window, tmp_path, "first")
    window._on_translation_edited(0, "B[end]")
    second = add_block(window, file_entry, "second", RangeSource(3, 6))
    assert window._entry is second
    window.undo_stack.undo()  # removing the second block
    window.undo_stack.undo()  # the translation, made in the first
    assert window._entry is first
    assert window.workspace.current is first
    # And in the view the edit was made in, not whichever tab happened to be up.
    assert window.tabs.currentWidget() is window.strings
    assert first.doc.strings[0].current_text() == "AB[end]"


def test_undoing_an_overtype_returns_to_the_hex_tab(window, tmp_path):
    file_entry, _ = _block(window, tmp_path)
    window._activate_entry(file_entry)
    window.overtype_bytes(1, b"\x43")
    window._show_view("raw")
    window._go_to(9)
    window._show_view("strings")
    window.undo_stack.undo()  # the view move
    window.undo_stack.undo()  # the overtype
    assert window.tabs.currentWidget() is window.raw
    assert file_entry.doc.data[1] == 0x42


def test_a_rename_is_undone_without_moving_the_view(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._commit_rename(file_entry, "renamed")
    assert window._entry is block
    window.undo_stack.undo()
    assert file_entry.name == "rom.bin"
    assert window._entry is block  # an in-place command never yanks the view


# --- re-entrancy -----------------------------------------------------------


def test_an_apply_never_pushes_a_second_command(window, tmp_path):
    _, block = _block(window, tmp_path)
    window._on_translation_edited(0, "B[end]")
    count = window.undo_stack.count()
    window.undo_stack.undo()
    window.undo_stack.redo()
    assert window.undo_stack.count() == count


def test_a_push_inside_an_apply_is_refused(window, tmp_path):
    _, block = _block(window, tmp_path)
    count = window.undo_stack.count()
    with window._undo_apply():
        window._on_translation_edited(0, "B[end]")
        window._go_to(4)
    assert window.undo_stack.count() == count


# --- merging ---------------------------------------------------------------


def test_a_typing_run_on_one_cell_is_one_step(window, tmp_path):
    _, block = _block(window, tmp_path)
    start = window.undo_stack.count()
    window._on_translation_edited(0, "B[end]")
    window._on_translation_edited(0, "BB[end]")
    assert window.undo_stack.count() == start + 1
    assert block.doc.strings[0].current_text() == "BB[end]"
    window.undo_stack.undo()
    assert block.doc.strings[0].current_text() == "AB[end]"


def test_a_run_cleared_back_to_the_original_leaves_no_step(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    start = window.undo_stack.count()
    window._on_translation_edited(0, "B[end]")
    window._on_translation_edited(0, "")  # blank: the original, back where it began
    assert window.undo_stack.count() == start
    assert block.doc.strings[0].current_text() == "AB[end]"
    assert file_entry.doc.data == DATA
    assert not file_entry.dirty  # and the revision came back with it


def test_moving_to_another_row_starts_a_new_step(window, tmp_path):
    _, block = _block(window, tmp_path)
    start = window.undo_stack.count()
    window._on_translation_edited(0, "B[end]")
    window._on_string_row(1)  # the run on row 0 ends here
    window._on_string_row(0)
    window._on_translation_edited(0, "BB[end]")
    assert window.undo_stack.count() == start + 2


def test_view_moves_in_two_entries_stay_separate_steps(window, tmp_path):
    file_entry, block = _block(window, tmp_path)
    window._activate_entry(file_entry)
    window._go_to(4)
    window._activate_entry(block)
    window._go_to(2)
    assert window._offset == 2
    window.undo_stack.undo()
    assert window._entry is block and window._offset == 0
    window.undo_stack.undo()
    assert window._entry is file_entry and window._offset == 0


def test_undo_and_redo_of_a_table_edit_take_the_overlay_with_them(window, tmp_path):
    """The overlay is re-measured from the tables on every apply, so the project
    holds exactly what the tables say in either direction."""
    _block(window, tmp_path)
    table_entry = window.workspace.entry_for_table("main")
    before = deepcopy(table_entry.table)
    table_entry.table.add(TableEntry("01000011", TokenKind.TEXT, "C"))
    window._on_table_edited(table_entry, before)
    assert table_entry.table_overlay == {"01000011": "43=C"}
    window.undo_stack.undo()
    assert table_entry.table_overlay == {}
    window.undo_stack.redo()
    assert table_entry.table_overlay == {"01000011": "43=C"}


def test_binding_a_font_from_the_files_panel_is_an_undo_step(window, tmp_path):
    """A click on a font row binds the current block to it through a box edit,
    so it undoes and the block reads clean again."""
    from mapchar.core.font import Font
    from mapchar.project.workspace import Entry, EntryKind

    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    block = add_block(window, file_entry, "b", RangeSource(0, 3))
    window._activate_entry(block)
    font_entry = Entry(
        EntryKind.FONT, "f", path=str(tmp_path / "f.png"), font=Font(path="")
    )
    window.workspace.add(font_entry)
    assert block.box is None and not block.dirty

    window._edit_font_entry(font_entry)
    assert block.box is not None and block.box.font_index == 0
    assert block.dirty

    window.undo_stack.undo()
    assert block.box is None and not block.dirty
