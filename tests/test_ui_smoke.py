from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from helpers import pointer_rom, texts
from mapchar.core.block import RangeSource, Status
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.main_window import MainWindow
from mapchar.ui.token_text import POINTER_TOKENS
from window_helpers import (
    ASCII_TABLE,
    TABLE,
    add_block,
    arm_scheme,
    grid_keys,
    grid_row,
    open_rom_and_table,
    type_in_grid,
)


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: (
            __import__("PySide6.QtWidgets").QtWidgets.QMessageBox.StandardButton.Discard
        ),
    )
    monkeypatch.setattr("mapchar.ui.main_window.window.TextDialog.exec", lambda self: 0)
    # The three-way "unsaved edits" gate is a box with its own labels, not a
    # standard question: answer it the same way, by taking the destructive
    # ("Continue Without" / "Discard") button.
    qmessagebox = __import__("PySide6.QtWidgets").QtWidgets.QMessageBox
    monkeypatch.setattr(
        qmessagebox,
        "clickedButton",
        lambda self: next(
            (
                b
                for b in self.buttons()
                if self.buttonRole(b) == qmessagebox.ButtonRole.DestructiveRole
            ),
            None,
        ),
    )
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_open_rom_table_and_block(window, tmp_path, monkeypatch):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
    entry = open_rom_and_table(window, tmp_path, data, rom_name="game.bin")
    assert entry is not None and window._doc is not None and window._doc.size == 26
    assert window.format_pick.currentData() == "main"
    window._refresh_view()
    model = window.raw._model
    assert model is not None and [t.text() for t in model.tokens][:3] == [
        "A",
        "B",
        "[end]",
    ]
    assert 0 in model.string_starts and 3 in model.string_starts

    # A block over the first six bytes, created without the dialog.
    add_block(window, entry, "b", RangeSource(0, 6))
    assert texts(window._doc.strings) == ["AB[end]", "BA[end]"]
    assert window.strings.table.rowCount() == 2

    # Project round trip.
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert not window.workspace.entries
    assert window.open_project(str(proj))
    names = [e.name for e in window.workspace.entries]
    assert names == ["game.bin", "b", "main.tbl"]

    # Undo removes the last added entry.
    window.undo_stack.clear()
    window._push_add(
        Entry(EntryKind.BOOKMARK, "bm", entry.path, parent=window.workspace.entries[0])
    )
    assert len(window.workspace.entries) == 4
    window.undo_stack.undo()
    assert len(window.workspace.entries) == 3


def test_navigation_and_selection(window, tmp_path):
    rom = tmp_path / "big.bin"
    rom.write_bytes(bytes(range(256)) * 8)
    window.open_rom(str(rom))
    window._go_to(0x100)
    assert window._offset == 0x100
    window.undo_stack.undo()
    assert window._offset == 0
    window._select_bytes(0x210, 4)
    assert window.raw.selection() == (0x210, 0x214)
    assert "selected" in window.nav_status.text()


def test_text_tab(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
    open_rom_and_table(window, tmp_path, data)
    assert window.tabs.currentWidget() is window.raw
    assert [window.tabs.tabText(i) for i in range(3)] == ["Hex", "Text", "Strings"]
    window.text_tab_action.trigger()
    assert window.tabs.currentWidget() is window.text
    assert window.text.edit.toPlainText() == "AB[end]\nBA[end]\n[$FF][$FF][$FF][$FF]"
    window.raw.set_selection(3, 5)
    window._on_selection(3, 5)
    cursor = window.text.edit.textCursor()
    assert (cursor.selectionStart(), cursor.selectionEnd()) == (8, 10)
    cursor.setPosition(0)
    cursor.setPosition(8, cursor.MoveMode.KeepAnchor)
    window.text.edit.setTextCursor(cursor)
    assert window.raw.selection() == (0, 3)
    window.raw_tab_action.trigger()
    assert window.tabs.currentWidget() is window.raw


def test_text_tab_drag_upward(window, tmp_path):
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent
    from PySide6.QtWidgets import QApplication

    open_rom_and_table(window, tmp_path, bytes.fromhex("41 42 42 42 42 00") * 20)
    window.text_tab_action.trigger()
    edit = window.text.edit
    viewport = edit.viewport()
    left = Qt.MouseButton.LeftButton

    def send(kind, char, button):
        cursor = edit.textCursor()
        cursor.setPosition(char)
        at = QPointF(edit.cursorRect(cursor).center())
        global_at = QPointF(viewport.mapToGlobal(at.toPoint()))
        event = QMouseEvent(
            kind, at, global_at, button, left, Qt.KeyboardModifier.NoModifier
        )
        QApplication.sendEvent(viewport, event)

    # Each "ABBBB[end]" and its line break is eleven characters over six bytes.
    send(QEvent.Type.MouseButtonPress, 66, left)
    for char in (63, 55, 44, 33):
        send(QEvent.Type.MouseMove, char, Qt.MouseButton.NoButton)
    assert edit.textCursor().anchor() == 66
    assert window._selection == (18, 36)


def test_the_text_tab_is_a_session_view(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, bytes.fromhex("41 42 00"))
    window.text_tab_action.trigger()
    window._capture_session()
    assert file_entry.session.view == "text"
    window.raw_tab_action.trigger()
    window._show_view(file_entry.session.view)
    assert window.tabs.currentWidget() is window.text
    assert window.text.edit.toPlainText().startswith("AB[end]")


def test_edit_and_write(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6), fill=b"\xee")
    window._on_translation_edited(0, "A[end]")
    rec = block.doc.strings[0]
    # The translation is the bytes: the string's slot holds it, fill after.
    assert rec.current_text() == "A[end]" and rec.original == "AB[end]"
    assert rec.status is Status.EDITED
    assert file_entry.dirty and not block.dirty
    assert file_entry.doc.data == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    rows = window._row_data(block, block.doc, window._table_set())
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


def test_import_export_and_find_replace(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    window._on_translation_edited(0, "B[end]")
    tsv = tmp_path / "d.tsv"
    window.export_file(str(tsv), "tsv")
    assert "D/0\t$0\tAB[end]\tB[end]\tedited" in tsv.read_text()
    po = tmp_path / "d.po"
    window.export_file(str(po), "po")
    window._on_translation_edited(0, "")  # blank: the original again
    assert not block.doc.strings[0].edited
    window.import_file(str(po), "po", confirm=False)
    assert block.doc.strings[0].current_text() == "B[end]"
    window.undo_stack.undo()
    assert not block.doc.strings[0].edited
    # Script import through the native writer.
    from helpers import translated
    from mapchar.project.formats.script import DumpMode, write_script

    strings, _ = translated(data, block.config, window._table_set(), {1: "A[end]"})
    script = tmp_path / "s.txt"
    script.write_text(
        write_script([("D", block.config, strings)], DumpMode.TRANSLATIONS)
    )
    window.import_file(str(script), "script", confirm=False)
    assert block.doc.strings[1].current_text() == "A[end]"
    assert block.doc.strings[1].status is Status.EDITED
    # Replace all over the strings' text.
    window._fr_replace_all("A", "B", True)
    assert block.doc.strings[1].current_text() == "B[end]"
    assert block.doc.strings[0].current_text() == "BB[end]"
    # Hex panel overtype path.
    window.show()
    window.hex_dock.show()
    window._sync_hex_panel()
    assert "000000  42 42 00" in window.hex_panel.view.toPlainText()
    window.hex_panel.at.setText("2")
    window.hex_panel.bytes.setText("42 00")
    window.hex_panel._on_apply()
    assert file_entry.doc.data[:4] == bytes.fromhex("42 42 42 00")


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
    rows = window._row_data(block, block.doc, window._table_set())
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


def test_cartographer_and_atlas_import(window, tmp_path):
    rom = tmp_path / "c.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 00") + b"\xff" * 8)
    (tmp_path / "main.tbl").write_text("@main\n41=A\n42=B\n/00=[end]\n")
    (tmp_path / "cmd.txt").write_text(
        "#BLOCK NAME: Intro\n#TYPE: NORMAL\n#METHOD: RAW\n#SCRIPT START: 0\n"
        "#SCRIPT STOP: $5\n#TABLE: main.tbl\n#COMMENTS: No\n#END BLOCK\n"
    )
    window.open_rom(str(rom))
    created = window.import_cartographer(str(tmp_path / "cmd.txt"))
    assert [e.name for e in created] == ["Intro"]
    block = created[0]
    assert block.config.table_id == "main"
    assert texts(block.doc.strings) == ["AB[end]", "B[end]"]
    (tmp_path / "atlas.txt").write_text(
        '#VAR(T, TABLE)\n#ADDTBL("main.tbl", T)\n#ACTIVETBL(T)\n#JMP($0, $4)\nBA[end]\n'
        "#JMP($3, $4)\nA[end]\n"
    )
    assert window.import_atlas(str(tmp_path / "atlas.txt")) == 2
    assert block.doc.strings[0].current_text() == "BA[end]"
    assert block.doc.strings[1].current_text() == "A[end]"
    assert window._string_at(3) is block.doc.strings[1]


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


def test_compressed_block_roundtrip(window, tmp_path, monkeypatch):
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(payload, PipelineContext())
    slot = len(packed) + 9  # the compressed slot has spare room at its end
    data = b"\xff" * 16 + packed + b"\xff" * 9 + b"\xff" * 24
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    arm_scheme(window, "gba_lz77")
    window._go_to(16)
    assert "compressed bytes at 10 → 90 bytes" in window.decompress_window.status.text()
    block = add_block(
        window,
        file_entry,
        "Z",
        RangeSource(0, len(payload)),
        # The fill byte is one the ASCII table maps nothing to, so what a
        # shorter string leaves behind is padding and not text.
        fill=b"\xff",
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=slot,
    )
    assert block.doc.data == payload and len(block.doc.strings) == 6
    window._on_translation_edited(0, "HI HI HI[end]")
    assert window._write_blocks([block])
    written = Path(file_entry.path).read_bytes()
    assert written[:16] == b"\xff" * 16 and written[16 + slot :] == b"\xff" * 24
    out = GbaLz77().decompress(written[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00" + b"\xff" * 9 + b"WORLD WORLD\x00")
    assert block.doc.strings[0].current_text() == "HI HI HI[end]"
    assert block.doc.strings[0].original == "HELLO HELLO HELLO[end]"


# -- the Compression picker and the Decompressed View ------------------------

RNC2_PAYLOAD = b"HELLO HELLO HELLO\x00WORLD WORLD WORLD\x00" * 4
"""Text enough to read in the preview's Text tab, and repetitive enough to pack."""


def _rnc2_rom(payload: bytes = RNC2_PAYLOAD, *, gap: int = 16) -> tuple[bytes, bytes]:
    """A ROM holding one RNC 2 stream at ``gap``, and the stream itself."""
    from mapchar.plugins.builtins.compression import rnc

    stream = rnc.compress(payload, method=2)
    return b"\xff" * gap + stream + b"\xff" * 32, stream


def test_the_compression_picker_arms_the_preview_and_turns_it_off(window, tmp_path):
    """A scheme picked on the Format bar is what the Decompressed View reads
    through, in both of its readings of the payload; ``none`` reads nothing."""
    data, stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    window._go_to(16)
    arm_scheme(window, "rnc2")
    assert window._preview_scheme == "rnc2" and view.isVisible()
    assert f"{len(stream):,} compressed bytes at 10 →" in view.status.text()
    # One decode, shown twice: the bytes and the text they read as.
    assert view.raw._model.data.startswith(b"HELLO")
    assert "HELLO HELLO HELLO" in view.text.edit.toPlainText()
    arm_scheme(window, "")
    assert window._preview_scheme is None and not view.isVisible()


def test_a_signature_arms_the_preview_by_itself_and_leaving_hides_it(window, tmp_path):
    data, stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    # The picker starts on automatic, and nothing announces itself at 0.
    assert window.compression_pick.currentData() is None
    assert not view.isVisible() and window._preview_scheme is None
    window._go_to(16)
    assert window._preview_scheme == "rnc2" and view.isVisible()
    # The picker only says it was left to the bytes, so the view names what
    # recognised them.
    assert view.status.text().startswith("RNC 2")
    # The file's own view washes the structure's compressed bytes.
    assert window.raw.structure() == (16, 16 + len(stream))
    window._go_to(17)
    assert window._preview_scheme is None and not view.isVisible()
    assert window.raw.structure() is None
    # A click on the stream's first byte arms it again: a selection is the
    # nearest thing the byte views have to a cursor.
    window._select_bytes(16, 1)
    assert window._preview_scheme == "rnc2" and view.isVisible()


def test_a_block_states_its_own_scheme_and_cannot_be_repicked(window, tmp_path):
    data, stream = _rnc2_rom()
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    plain = add_block(window, file_entry, "plain", RangeSource(0, 6))
    assert window.compression_pick.currentData() == ""
    assert not window.compression_pick.isEnabled()
    packed = add_block(
        window,
        file_entry,
        "packed",
        RangeSource(0, len(RNC2_PAYLOAD)),
        compression_id="rnc2",
        slice_offset=16,
        slice_length=len(stream),
    )
    assert packed.doc.data == RNC2_PAYLOAD
    assert window.compression_pick.currentData() == "rnc2"
    assert not window.compression_pick.isEnabled()
    # Back on the file, the pick is the file's own again.
    window._activate_entry(file_entry)
    assert window.compression_pick.isEnabled()
    assert window.compression_pick.currentData() is None
    assert plain.name and packed.name  # both rows are still there


def test_the_compression_pick_survives_a_project_save_and_load(window, tmp_path):
    data, _stream = _rnc2_rom()
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    arm_scheme(window, "rnc2")
    assert file_entry.session.preview_scheme == "rnc2"
    project = tmp_path / "p.mapchar"
    window.project_path = str(project)
    assert window._save_project()
    assert '"preview_scheme": "rnc2"' in project.read_text(encoding="utf-8")
    assert window.open_project(str(project))
    assert window.workspace.files()[0].session.preview_scheme == "rnc2"
    assert window.compression_pick.currentData() == "rnc2"


def test_find_all_lists_the_structures_and_selecting_one_moves_the_view(
    window, tmp_path
):
    from mapchar.plugins.builtins.compression import rnc

    first = rnc.compress(RNC2_PAYLOAD, method=2)
    second = rnc.compress(b"SECOND SECOND SECOND\x00" * 3, method=2)
    data = b"\xff" * 16 + first + b"\xff" * 8 + second + b"\xff" * 16
    at_second = 16 + len(first) + 8
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    view = window.decompress_window
    # On automatic, Find All covers every scheme that announces itself.
    view.find_button.click()
    assert "2 structure(s)" in view.found.text()
    assert view.results.rowCount() == 2
    assert [view.results.item(r, 0).text() for r in range(2)] == [
        "10",
        f"{at_second:X}",
    ]
    assert view.results.item(0, 1).text() == f"{len(first):,}"
    assert view.results.item(0, 2).text() == f"{len(RNC2_PAYLOAD):,}"
    # The payload reads as text, which is what the Text column scores.
    assert float(view.results.item(0, 3).text()) > 0.5
    view.results.selectRow(1)
    assert window._offset == at_second and window._preview_scheme == "rnc2"
    # The list is one file's: another file on screen drops it.
    open_rom_and_table(window, tmp_path, b"\xff" * 64, rom_name="other.bin")
    assert view.results.rowCount() == 0


def test_to_block_under_automatic_arming_records_the_scheme_that_decoded(
    window, tmp_path
):
    data, stream = _rnc2_rom()
    open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    window._go_to(16)
    window.decompress_window.block.click()
    block = window.workspace.of_kind(EntryKind.BLOCK)[0]
    assert block.compression_id == "rnc2"
    assert (block.slice_offset, block.slice_length) == (16, len(stream))
    assert block.doc.data == RNC2_PAYLOAD


def test_find_all_lists_every_structure_in_the_mk2_rom(window):
    """The 29 RNC 2 streams Mortal Kombat II (GB) carries, when the ROM is here.

    The ROM never enters the repository; without it this skips. What it pins is
    Find All against a real packer's output: the walk over a whole ROM finds
    every stream and nothing else.
    """
    rom = Path(__file__).resolve().parent.parent / (
        "sample-projects/MK2/Mortal Kombat II (USA, Europe).gb"
    )
    if not rom.exists():
        pytest.skip("the Mortal Kombat II ROM is not present")
    window.open_rom(str(rom))
    view = window.decompress_window
    view.find_button.click()
    assert "29 structure(s)" in view.found.text()
    assert view.results.item(0, 0).text() == "AC54"
    assert view.results.item(0, 1).text() == "1,951"
    assert view.results.item(0, 2).text() == "3,056"


def test_two_blocks_over_one_slot_write_together(window, tmp_path):
    """One compressed slot holds one stream, so both blocks over it fold into it.

    Compressing each block's own buffer separately would have the second splice
    at the slot's offset replace the first, and one of the two edits would vanish
    without anything being reported.
    """
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(payload, PipelineContext())
    # Room for a re-compression that packs worse than the original: breaking a
    # long repetition up is what editing text does.
    slot = len(packed) + 16
    data = b"\xff" * 16 + packed + b"\xff" * 16 + b"\xff" * 24
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    first = add_block(
        window,
        file_entry,
        "front",
        RangeSource(0, 30),
        fill=b"\xff",
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=slot,
    )
    second = add_block(
        window,
        file_entry,
        "back",
        RangeSource(30, 60),
        fill=b"\xff",
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=slot,
    )
    window._activate_entry(first)
    window._on_translation_edited(0, "HI HI HI[end]")
    window._activate_entry(second)
    window._on_translation_edited(0, "BYE[end]")
    assert window._write_blocks([first, second])
    written = Path(file_entry.path).read_bytes()
    out = GbaLz77().decompress(written[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00" + b"\xff" * 9)
    assert out[30:].startswith(b"BYE\x00" + b"\xff" * 14)
    assert not first.dirty and not second.dirty


def test_siblings_over_a_slot_share_its_payload_and_write_together(window, tmp_path):
    """Blocks over one compressed slot read one stream, so an edit in any of
    them is in the payload the others read, and a write of one writes the slot
    with every edit in it — and marks every block over it written."""
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(payload, PipelineContext())
    slot = len(packed) + 16
    data = b"\xff" * 16 + packed + b"\xff" * 16 + b"\xff" * 24
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    slice_fields = dict(
        fill=b"\xff", compression_id="gba_lz77", slice_offset=16, slice_length=slot
    )
    written = add_block(
        window, file_entry, "written", RangeSource(0, 30), **slice_fields
    )
    clean = add_block(window, file_entry, "clean", RangeSource(30, 60), **slice_fields)
    edited = add_block(
        window, file_entry, "edited", RangeSource(60, 90), **slice_fields
    )
    window._on_translation_edited(0, "KEPT[end]")
    assert edited.dirty and edited.doc is not None and clean.doc is not None
    assert written.doc.data == edited.doc.data  # one stream, shared

    window._activate_entry(written)
    window._on_translation_edited(0, "HI HI HI[end]")
    assert window._write_blocks([written])

    assert not written.dirty and not edited.dirty and not clean.dirty
    assert edited.doc.strings[0].current_text() == "KEPT[end]"
    out = GbaLz77().decompress(
        Path(file_entry.path).read_bytes()[16:], PipelineContext()
    )
    assert out.startswith(b"HI HI HI") and out[60:].startswith(b"KEPT\x00")


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
    rows = window._row_data(block, block.doc, window._table_set())
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


def test_hex_panel_overtypes_in_place(window, tmp_path, qtbot):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QTextCursor

    rom = tmp_path / "h.bin"
    rom.write_bytes(bytes(range(32)))
    file_entry = window.open_rom(str(rom))
    window.show()
    window.hex_dock.show()
    window.hex_panel.follow.setChecked(False)
    window._sync_hex_panel()
    view = window.hex_panel.view
    cursor = view.textCursor()
    cursor.setPosition(8 + 3 * 2)  # first digit of byte 2
    view.setTextCursor(cursor)
    qtbot.keyClick(view, Qt.Key.Key_F)
    assert file_entry.doc.data[2] == 0xF2
    qtbot.keyClick(view, Qt.Key.Key_A)
    assert file_entry.doc.data[2] == 0xFA and file_entry.dirty
    assert view.textCursor().position() == 8 + 3 * 3
    window.undo_stack.undo()
    window.undo_stack.undo()
    assert file_entry.doc.data[2] == 2
    assert isinstance(cursor, QTextCursor)


def test_cartographer_import_strips_the_header(window, tmp_path):
    from mapchar.plugins.builtins.containers import NES_MAGIC

    header = NES_MAGIC + bytes([1, 0, 0, 0]) + b"\x00" * 8
    rom = tmp_path / "h.nes"
    rom.write_bytes(header + bytes.fromhex("41 42 00 42 00") + b"\xff" * 8)
    (tmp_path / "main.tbl").write_text("@main\n41=A\n42=B\n/00=[end]\n")
    (tmp_path / "cmd.txt").write_text(
        "#BLOCK NAME: Intro\n#TYPE: NORMAL\n#METHOD: RAW\n#SCRIPT START: $10\n"
        "#SCRIPT STOP: $15\n#TABLE: main.tbl\n#COMMENTS: No\n#END BLOCK\n"
    )
    window.open_rom(str(rom))
    block = window.import_cartographer(str(tmp_path / "cmd.txt"))[0]
    assert block.config.source == RangeSource(0, 5)
    assert texts(block.doc.strings) == ["AB[end]", "B[end]"]


def test_project_reads_clean_until_the_session_moves(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
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
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
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
    assert window.files_panel._items[id(block)].text(0) == "b  (2)"
    assert not window._project_dirty()


@pytest.mark.parametrize("folders", [0, 2])
def test_the_files_panel_dresses_each_row_a_bounded_number_of_times(
    window, tmp_path, monkeypatch, folders
):
    """Opening a project, and reading every block for Project Strings, costs the
    Files panel a few passes over its rows, not one per block: a project of
    hundreds of blocks would otherwise take the square of that — with the
    blocks in folders, one inside the other, as much as without."""
    data = bytes.fromhex("41 42 00 42 41 00") * 40
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
    assert all(
        window.files_panel._items[id(b)].text(0) == f"{b.name}  (2)" for b in blocks
    )
    dressed.clear()
    window.workspace.invalidate_extractions()
    window.project_strings.show()
    window._refresh_project_strings()
    assert window.project_strings.results.rowCount() == 80
    assert len(dressed) <= 3 * rows


def test_opening_a_project_leaves_a_missing_files_blocks_unread(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    window._activate_entry(file_entry)
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    Path(str(file_entry.path)).unlink()
    window._new_project()
    assert window.open_project(str(proj))
    assert window.workspace.entries[1].doc is None


def test_tables_show_their_entry_count(window, tmp_path):
    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.table_entries()[0]
    assert window.files_panel._items[id(table_entry)].text(0) == "main.tbl  (3)"
    assert window.format_pick.itemText(window.format_pick.findData("main")) == (
        "@main  (3)"
    )
    window._edit_table_entry(table_entry)
    window.table_editor.new_line.setText("44=D")
    window.table_editor._add()
    assert window.files_panel._items[id(table_entry)].text(0) == "main.tbl  (4) ●"
    assert window.format_pick.currentText() == "@main  (4)"


def test_saving_a_project_offers_to_write_the_unsaved_edits(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
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
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
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


# --- capability gating -----------------------------------------------------


def test_the_capability_table_covers_every_kind():
    from mapchar.core.capabilities import CAPABILITIES, Capability, EntryKind, supports

    assert set(CAPABILITIES) == set(EntryKind)
    assert not supports(None, Capability.NAVIGATION)  # nothing open supports nothing
    assert supports(EntryKind.FILE, Capability.CONTAINER)
    assert not supports(EntryKind.BLOCK, Capability.CONTAINER)
    assert supports(EntryKind.BLOCK, Capability.STRINGS)
    assert not supports(EntryKind.FILE, Capability.STRINGS)
    assert CAPABILITIES[EntryKind.BOOKMARK] == frozenset()


def test_nothing_open_leaves_the_controls_gated(window):
    assert not window.format_bar.isEnabled()
    assert not window.offset_box.isEnabled()
    assert not window.write_action.isEnabled()
    # The Block bar keeps its row, greyed and with nothing to say.
    assert not window.block_bar.isEnabled() and window.block_label.text() == ""


def test_a_file_gates_the_string_surfaces_off(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    window._activate_entry(entry)
    assert window.format_bar.isEnabled()
    assert window.offset_box.isEnabled() and window.goto_action.isEnabled()
    assert window.container_action.isEnabled()
    assert not window.strings_tab_action.isEnabled()
    assert not window.find_replace_action.isEnabled()
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(window.strings))
    # Greyed, not gone — opening a block moves nothing — and it names the file.
    assert window.block_bar.isVisibleTo(window) and not window.block_bar.isEnabled()
    assert window.block_label.text() == "rom.bin · 3 bytes"


def test_a_block_gates_the_string_surfaces_on(window, tmp_path):
    data = bytes.fromhex("41 42 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window._entry is block
    assert window.strings_tab_action.isEnabled()
    assert window.find_replace_action.isEnabled()
    assert window.preview_action.isEnabled()
    assert window.tabs.isTabEnabled(window.tabs.indexOf(window.strings))
    assert window.block_bar.isEnabled()
    assert window.block_export.isEnabled()
    assert window.reading_bar.isEnabled()
    # A block reads its container through its parent, so the row is not its own.
    assert not window.container_action.isEnabled()


# --- long operations can be stopped -----------------------------------------


def test_modal_progress_asks_the_engine_to_stop_once_cancelled(window):
    from mapchar.ui.widgets import ModalProgress

    with ModalProgress(window, "Working", "Working…") as run:
        assert run.progress(1, 4) is True
        assert not run.cancelled
        run.cancel()
        assert run.progress(2, 4) is False
        assert run.cancelled


def test_pointer_discovery_runs_under_a_stop_button(
    window, tmp_path, monkeypatch, unattended_dialogs
):
    """The promise of architecture §1: every long operation pumps the event loop
    through a progress callback and can be cancelled. The search here is every
    mapping crossed with every width and endianness over the whole file."""
    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00" + b"\xff" * 8)
    add_block(window, file_entry, "b", RangeSource(0, 3))
    seen = {}

    def stub(data, starts, mappings, *, progress=None, **kw):
        seen["hooked"] = progress is not None
        window._pointer_progress.cancel()  # the Stop button, pressed
        seen["told_to_stop"] = progress(1, 2) is False
        return []

    monkeypatch.setattr("mapchar.ui.dialogs.PointerSearchDialog.exec", lambda self: 1)
    monkeypatch.setattr("mapchar.ui.main_window.pointers.discover", stub)
    window._find_pointers()
    assert seen["hooked"] and seen["told_to_stop"]
    assert any("stopped" in message for message in unattended_dialogs)


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


# --- the menu bar as a whole ------------------------------------------------
# Three walks over the built window, so a new action, a new gate or a new
# shortcut has to be classified rather than quietly joining the crowd.

# The rows that apply whatever is on screen, so no capability gates them: the
# project and plugin rows, the panels, the themes, the help, the visit trail
# (armed by the trail), the undo pair (armed by the stack) and the entry
# clipboard (scoped to the Files panel, which has a selection of its own).
ALWAYS_ON = frozenset(
    {
        "Open ROM…",
        "Open Table…",
        "New Table…",
        "Refresh Tables",
        "Open Font…",
        "Write All",
        "New Project",
        "Open Project…",
        "Locate Missing Files…",
        "Script…",
        "TSV / CSV…",
        "PO…",
        "Cartographer Command File…",
        "Atlas Script…",
        "TSV…",
        "CSV…",
        "Save Project",
        "Save Project As…",
        "Open Plugins Folder…",
        "Refresh Plugins",
        "Quit",
        "Undo",
        "Redo",
        "Cut Entry",
        "Copy Entry",
        "Paste Entry",
        "Duplicate Entry",
        "Table Editor…",
        "Glossary…",
        "Project Strings…",
        "Light Theme",
        "Dark Theme",
        "Back",
        "Forward",
        "Files",
        "Tables",
        "Fonts",
        "Hex",
        "Reset Panel Layout",
        "Shortcuts…",
        "Legend…",
        "About",
    }
)


def _menu_actions(window, menu=None):
    """Every ``(label, action)`` in the menu bar, submenus walked in place.

    Open Recent is skipped: its rows are project names that come and go.
    """
    from mapchar.ui.help_dialogs import submenus

    menu = window.menuBar() if menu is None else menu
    for action in menu.actions():
        if action.isSeparator():
            continue
        yield action.text().replace("&", "").strip(), action
        submenu = submenus(window.menuBar()).get(action)
        if submenu is not None and submenu is not window.recent_menu:
            yield from _menu_actions(window, submenu)


def _gated_controls(window):
    from mapchar.ui.main_window.capability_sync import _GATES

    found = set()
    for names in _GATES.values():
        for name in names:
            control = getattr(window, name)
            found.update(
                id(c) for c in (control if isinstance(control, tuple) else (control,))
            )
    return found


def test_every_menu_action_is_gated_or_always_on(window):
    """The capability table's coverage read off the menu bar rather than
    promised: a row that is neither gated nor deliberately always-on stays live
    on an entry it cannot act on."""
    gated = _gated_controls(window)
    ungated = [
        label
        for label, action in _menu_actions(window)
        # A row that only opens a submenu is not itself a row that acts, unless
        # the table gates it (Import and Export are gated on their whole menu).
        if action.menu() is None and id(action) not in gated and label not in ALWAYS_ON
    ]
    assert ungated == []


def test_every_gate_names_a_control_the_window_has(window):
    """A gate naming a control that is not there used to be a silent skip, so
    the control was never gated and nothing said so."""
    from mapchar.ui.main_window.capability_sync import _GATES

    for names in _GATES.values():
        for name in names:
            assert getattr(window, name) is not None


def test_no_two_window_actions_share_a_shortcut(window):
    """Qt calls one sequence bound twice on a window ambiguous and fires
    neither, so a duplicate is two dead keys rather than one."""
    seen: dict[str, str] = {}
    clashes = []
    for label, action in _menu_actions(window):
        keys = action.shortcut().toString()
        if not keys:
            continue
        if keys in seen:
            clashes.append(f"{keys}: {seen[keys]} and {label}")
        seen[keys] = label
    assert clashes == []


# --- the window's own chrome ------------------------------------------------


def test_the_dock_layout_is_remembered_and_resettable(window):
    from mapchar.ui import settings

    window.show()
    window.hex_dock.show()
    window.files_dock.hide()
    window._window_layout.save()
    assert settings().value("window/state") is not None
    window._reset_layout()
    # The factory arrangement is whatever the docks built for themselves, which
    # is the Files dock up and the Hex dock down.
    assert window.files_dock.isVisibleTo(window)
    assert not window.hex_dock.isVisibleTo(window)


def test_every_tool_window_remembers_its_geometry(window):
    for tool in (
        window.search_window,
        window.scan_window,
        window.table_editor,
        window.decompress_window,
        window.preview_window,
        window.find_replace,
    ):
        assert tool._layout is not None
        tool._layout.save()


def test_the_architecture_doc_lists_every_main_window_module():
    """§7.1's table is the map of the split, so a module added or renamed without
    a line there leaves the design describing a window that is not this one."""
    root = Path(__file__).resolve().parent.parent
    doc = (root / "docs/plan/architecture.md").read_text(encoding="utf-8")
    section = doc.split("### 7.1 Composition", 1)[1].split("### 7.2", 1)[0]
    listed = set(re.findall(r"`(\w+\.py)`", section))
    on_disk = {
        path.name
        for path in (root / "src/mapchar/ui/main_window").glob("*.py")
        if path.name != "__init__.py"
    }
    assert on_disk - listed == set()


def test_a_layout_version_bump_drops_a_stored_arrangement(window):
    """What the version is for: an arrangement this build cannot make sense of is
    dropped for the defaults rather than half-restored."""
    from mapchar.ui.window_layout import LAYOUT_VERSION

    window.show()
    state = window.saveState(LAYOUT_VERSION)
    assert window.restoreState(state, LAYOUT_VERSION)
    assert not window.restoreState(state, LAYOUT_VERSION + 1)


def test_the_navigation_filter_comes_off_the_application_on_close(
    window, tmp_path, monkeypatch
):
    """It was installed on a singleton, so a closed window that left its filter
    behind would keep answering for a window that is gone."""
    from PySide6.QtWidgets import QApplication

    entry = open_rom_and_table(window, tmp_path, b"AB\x00" + b"\xff" * 32)
    window._activate_entry(entry)
    removed = []
    monkeypatch.setattr(
        QApplication.instance(),
        "removeEventFilter",
        lambda obj: removed.append(obj),
        raising=False,
    )
    window.close()
    assert removed == [window]


def test_a_push_re_serialises_the_project_once(window, tmp_path, monkeypatch):
    """The project's unsaved marker costs the whole project re-serialised, and one
    push passes several choke points that would each ask for it."""
    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00" + b"\xff" * 32)
    add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window._write_project(str(tmp_path / "p.mapchar"))
    calls = []
    real = window._snapshot
    monkeypatch.setattr(window, "_snapshot", lambda: (calls.append(1), real())[1])
    window._on_translation_edited(0, "B[end]")
    assert len(calls) == 1


def test_no_widget_wears_a_stylesheet():
    """The theme is one palette on Fusion; a stylesheet anywhere would paint one
    widget out of step with both themes (``ui/theme.py``)."""
    root = Path(__file__).resolve().parent.parent / "src/mapchar/ui"
    wearing = [
        path.relative_to(root).as_posix()
        for path in root.rglob("*.py")
        if "setStyleSheet" in path.read_text(encoding="utf-8")
    ]
    assert wearing == []


def test_the_shortcut_guide_is_built_from_the_window(window):
    from mapchar.ui.help_dialogs import (
        ShortcutGuide,
        balanced_columns,
        shortcut_sections,
    )

    sections = dict(shortcut_sections(window))
    assert "File" in sections and "Navigate" in sections
    assert ("Write", "Ctrl+W") in sections["File"]
    assert ("Go to Address…", "Ctrl+G") in sections["Navigate"]
    assert ("Back", "Alt+Left") in sections["Navigate"]
    # Undo's label names the command it would undo; the guide pins the verb.
    assert ("Undo", "Ctrl+Z") in sections["Edit"]
    # A submenu's rows are flattened into its parent's section.
    assert ("Script…", "") not in sections["File"]
    # And the keys no menu can carry are declared beside them.
    assert ("Page up / down", "PgUp / PgDn") in sections["Hex and Text Views"]
    columns = balanced_columns(shortcut_sections(window))
    assert sum(len(c) for c in columns) == len(sections) and all(columns)
    guide = ShortcutGuide(shortcut_sections(window), window)
    assert guide.width() > 0
    guide.close()


def test_the_legend_shows_every_colour_the_views_draw(window):
    """A tint added to the theme is a tint the legend has to explain."""
    from PySide6.QtGui import QColor

    from mapchar.ui import theme
    from mapchar.ui.help_dialogs import LEGEND, LegendDialog, SwatchWidget

    shown = {
        colour.name(QColor.NameFormat.HexArgb)
        for _title, entries in LEGEND
        for swatch, _meaning in entries
        for colour in (swatch.tint, swatch.ink)
        if colour is not None
    }
    drawn = {
        getattr(theme, name).name(QColor.NameFormat.HexArgb)
        for name in dir(theme)
        # The Preview's own paper, ink and grid are the colours of a stand-in
        # screen rather than marks a view puts on the text: nothing in the
        # Legend would explain them.
        if not name.startswith("PREVIEW_")
        and (name.startswith("TINT_") or name.endswith("_INK"))
    }
    assert drawn - shown == {theme.TINT_STRING_RULE.name(QColor.NameFormat.HexArgb)}
    assert any(
        swatch.mark == "rule" for _t, entries in LEGEND for swatch, _m in entries
    )
    assert all(meaning for _t, entries in LEGEND for _s, meaning in entries)
    legend = LegendDialog(window)
    assert legend.width() > 0
    swatches = legend.findChildren(SwatchWidget)
    assert len(swatches) == sum(len(entries) for _t, entries in LEGEND)
    for swatch in swatches:
        swatch.grab()  # paints every kind of mark without a screen
    legend.close()


def test_walking_the_menus_for_the_guide_leaves_every_submenu_alive(window):
    """PySide's ``QAction.menu()`` hands its wrapper ownership of the menu, so a
    walk through it deleted Open Recent the first time Help ▸ Shortcuts opened."""
    import gc

    from mapchar.ui.help_dialogs import shortcut_sections

    shortcut_sections(window)
    gc.collect()
    window._rebuild_recent()  # raised RuntimeError on a deleted QMenu
    assert window.recent_menu.title() == "Open &Recent"


_KEY = re.compile(
    r"\b(?:Ctrl|Alt|Shift|Meta)(?:\+(?:Ctrl|Alt|Shift|Meta))*"
    r"\+(?:F\d|[A-Za-z0-9]|Return|Enter|Left|Right|Up|Down|Home|End|PgUp|PgDn|Del)\b"
    r"|\bF\d\b"
)
"""A key sequence as either the docs or Qt spells one.

Bare keys (Home, PgUp, the arrows) are deliberately not matched: they are the
navigation filter's, not any action's, and the reference writes them as prose
("Up/Down row") that no pattern should try to read as a binding.
"""


def _documented_keys() -> set[str]:
    """Every sequence the Keyboard reference table in features.md advertises."""
    doc = (Path(__file__).resolve().parent.parent / "docs/plan/features.md").read_text(
        encoding="utf-8"
    )
    table = doc.split("## Keyboard reference", 1)[1]
    return {m.group(0) for line in table.splitlines() for m in _KEY.finditer(line)}


def test_every_documented_shortcut_is_really_bound(window):
    """The Keyboard reference walked against the window it describes.

    A key that moved, or one the docs promise and nothing carries, is a row the
    reader tries and finds dead. Both halves count: an action's own shortcut, and
    the keys ``help_dialogs.DISPLAY_ONLY`` declares because they are handled
    somewhere a ``QKeySequence`` cannot reach.
    """
    from mapchar.ui.help_dialogs import DISPLAY_ONLY

    bound = {
        action.shortcut().toString()
        for _, action in _menu_actions(window)
        if action.shortcut().toString()
    }
    for _, rows in DISPLAY_ONLY:
        for _, keys in rows:
            bound.update(m.group(0) for m in _KEY.finditer(keys))
    assert _documented_keys() - bound == set()


def test_a_shift_jis_script_is_read_and_says_so(window, tmp_path):
    path = tmp_path / "commands.txt"
    path.write_bytes("#BLOCK ソ\n".encode("cp932"))
    text, notices = window._read_text(str(path))
    assert text is not None and "ソ" in text
    assert notices == ["commands.txt is not UTF-8; read as cp932"]


def test_a_utf8_script_is_read_without_a_notice(window, tmp_path):
    path = tmp_path / "commands.txt"
    path.write_text("#BLOCK ソ\n", encoding="utf-8")
    assert window._read_text(str(path)) == ("#BLOCK ソ\n", [])


def test_an_unreadable_script_reports_nothing_read(window, tmp_path):
    assert window._read_text(str(tmp_path / "missing.txt")) == (None, [])


def test_importing_is_live_on_a_file_because_it_creates_blocks(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    window._activate_entry(entry)
    assert window.import_action.isEnabled()
    assert window.export_action.isEnabled()


# --- what the harness isolates ---------------------------------------------
# Two tests in order: the first writes, the second proves the fixture emptied
# the store between them. Without ``conftest.settings_root`` these would be
# reading and writing the developer's own profile, because Qt resolves the
# settings location once per process and no environment variable set afterwards
# moves it.

_PROBE = "probe/settings-are-isolated"


def test_the_settings_store_is_a_folder_of_this_runs_own(tmp_path):
    from mapchar.ui import settings

    store = settings()
    assert "pytest" in store.fileName()
    store.setValue(_PROBE, "written")
    store.sync()
    assert settings().value(_PROBE) == "written"


def test_the_settings_store_is_empty_again_for_the_next_test():
    from mapchar.ui import settings

    assert settings().value(_PROBE) is None


def test_a_dialog_nobody_arranged_for_answers_itself(window):
    """The autouse fixture answers ``QDialog`` as well as ``QMessageBox``: a
    modal reached by a path a test did not expect would otherwise run a loop
    the offscreen platform can never close."""
    from PySide6.QtWidgets import QDialog

    from mapchar.ui.dialogs import TextDialog

    assert TextDialog("Report", "body", window).exec() == QDialog.DialogCode.Rejected


# --- in-app table edits ------------------------------------------------------


def test_an_in_app_table_edit_is_carried_by_the_project(window, tmp_path):
    """A table edit changes the project, never the table file: the file other
    tools read keeps saying what it said until Save As File folds the overlay in."""
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
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
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
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


def test_a_files_row_name_takes_the_panel_width(window, tmp_path):
    """The status mark is a second column; the name still fills what it leaves."""
    open_rom_and_table(window, tmp_path, b"AB\x00" * 10, rom_name="a" * 60 + ".sfc")
    tree = window.files_panel.tree
    tree.resize(500, 300)
    window.files_panel.rebuild()
    assert tree.columnWidth(0) > 400


def test_the_bar_pickers_are_narrow_and_open_to_their_longest_item(window):
    from mapchar.ui.widgets import PICKER_WIDTH

    pick = window.address_pick
    assert pick.sizeHint().width() == PICKER_WIDTH
    longest = max(
        pick.fontMetrics().horizontalAdvance(pick.itemText(i))
        for i in range(pick.count())
    )
    assert longest > PICKER_WIDTH
    pick.showPopup()
    assert pick.view().width() >= longest
    pick.hidePopup()


def test_a_project_holding_translations_puts_them_in_the_bytes(window, tmp_path):
    """A project written before originals were kept carried translations
    that were not in the ROM. Opening it lands them: the file reads unsaved,
    and Write All writes them."""
    import json

    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
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

    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
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

    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
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
    assert "unplaced: A[end]" in back.doc.strings[0].notes
    assert any("3 strings instead of 2" in n for n in window._load_notices)


def test_blocks_and_bookmarks_never_share_a_name(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00")
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
    (tmp_path / "main.tbl").write_text("@main\n41=A\n42=B\n/00=[end]\n")
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
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
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


# --- the Table Editor's form -----------------------------------------------------


def test_the_entry_form_spells_every_kind_both_ways(qtbot):
    from mapchar.core.table import TokenKind
    from mapchar.project.formats.table_native import parse_entry
    from mapchar.ui.entry_rows import STOP_DATA
    from mapchar.ui.table_entry_form import TableEntryForm

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


def test_adding_relative_search_entries_to_a_table_undoes(
    window, tmp_path, monkeypatch
):
    """Build Table ▸ add to the current table is a table change like any other:
    one step, and undoing it takes the entries back out."""
    from PySide6.QtWidgets import QInputDialog

    from mapchar.engines.relsearch import UPPER, Hit

    open_rom_and_table(window, tmp_path, b"AB\x00")
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    monkeypatch.setattr(QInputDialog, "getItem", lambda *a, **k: ("A-Z", True))
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: (
            __import__("PySide6.QtWidgets").QtWidgets.QMessageBox.StandardButton.Yes
        ),
    )
    hit = Hit(0, 1, "little", {UPPER: 0x80}, tuple(range(0x80, 0x86)))
    window._build_table_from_hit(hit)
    assert len(table_entry.table.entries) > before
    window.undo_stack.undo()
    assert len(table_entry.table.entries) == before


def test_a_block_from_a_scanned_region_undoes_with_its_end_token(window, tmp_path):
    """The guessed terminator and the block are one gesture, so one undo takes
    both back out — otherwise undoing the block leaves the entry behind."""
    from mapchar.engines.scan import Region
    from mapchar.project.formats.table_native import HEADER

    # No end token of its own: the scan's guessed terminator becomes the block's.
    open_rom_and_table(
        window,
        tmp_path,
        b"AB\x0fCD\x0f",
        table=f"{HEADER}\n@table main\n41=A\n42=B\n",
    )
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    blocks = len(window.workspace.of_kind(EntryKind.BLOCK))
    window._block_from_region(Region(0, 6, 1.0, terminator=0x0F))
    assert len(table_entry.table.entries) == before + 1
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == blocks + 1
    window.undo_stack.undo()
    assert len(table_entry.table.entries) == before
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == blocks


def test_a_block_from_a_scanned_region_keeps_a_table_that_already_has_an_end(
    window, tmp_path
):
    """A table that already labels [end] on other bits keeps it: the guessed
    terminator is not added, and the block still comes out."""
    from mapchar.engines.scan import Region
    from mapchar.project.formats.table_native import HEADER

    open_rom_and_table(
        window,
        tmp_path,
        b"AB\x0fCD\x0f",
        table=f"{HEADER}\n@table main\n41=A\n42=B\n/00=[end]\n",
    )
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    blocks = len(window.workspace.of_kind(EntryKind.BLOCK))
    window._block_from_region(Region(0, 6, 1.0, terminator=0x0F))
    assert len(table_entry.table.entries) == before
    assert len(window.workspace.of_kind(EntryKind.BLOCK)) == blocks + 1


def test_a_block_from_a_scanned_record_chain_reads_it_behind_its_header(
    window, tmp_path
):
    """A region the scan found as a chain of length-prefixed records becomes a
    block that reads it that way — the length prefix and the header in front of
    it — and no end token is guessed for a string that carries its own length."""
    from mapchar.core.block import Pascal
    from mapchar.engines.scan import Records, Region
    from mapchar.project.formats.table_native import HEADER

    data = b"\x90\xa1\x02AB\x90\xa1\x03ABC"
    open_rom_and_table(
        window, tmp_path, data, table=f"{HEADER}\n@table main\n41=A\n42=B\n43=C\n"
    )
    table_entry = window.workspace.entry_for_table("main")
    before = len(table_entry.table.entries)
    window._block_from_region(Region(0, len(data), 1.0, records=Records(header=2)))
    entry = window._entry
    assert entry.config.string_type == Pascal(1) and entry.config.header == 2
    assert texts(entry.doc.strings) == ["AB", "ABC"]
    assert len(table_entry.table.entries) == before
    window.undo_stack.undo()
    assert window._entry is not entry


def test_the_scan_window_says_how_each_region_cuts_its_strings():
    """The Strings column is the Block dialog's words, never a class name."""
    from mapchar.engines.scan import Records, Region
    from mapchar.ui.scan_window import _strings_of

    assert _strings_of(Region(0, 8, 1.0, records=Records(header=2))) == (
        "Length prefix, header 2"
    )
    assert _strings_of(Region(0, 8, 1.0, records=Records())) == "Length prefix"
    assert _strings_of(Region(0, 8, 1.0, terminator=0x00)) == "End token 00"
    assert _strings_of(Region(0, 8, 1.0)) == ""


def test_autosave_writes_a_copy_and_offers_it_back(window, tmp_path, monkeypatch):
    """The copy beside the project is written on the timer and after a write,
    goes with a save, and is offered when the project is next opened."""
    import time

    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
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
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
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


# --- an import says what it will do, and waits -------------------------------


def _tsv(path: Path, *rows: str) -> Path:
    path.write_text(
        "id\taddress\toriginal\ttranslation\tstatus\tnotes\n" + "".join(rows),
        encoding="utf-8",
    )
    return path


def test_an_import_is_confirmed_before_anything_lands(window, tmp_path, monkeypatch):
    """The dialog is shown the plan, not the result: cancelling leaves the
    strings as they were, and the same file imports once it is accepted."""
    data = bytes.fromhex("41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    tsv = _tsv(tmp_path / "d.tsv", "D/0\t$0\tAB[end]\tBB[end]\tedited\t\n")

    seen: list = []
    monkeypatch.setattr(
        "mapchar.ui.dialogs.ImportDialog.exec",
        lambda self: seen.append(self._summary_for(False)) or 0,  # Rejected
    )
    window.import_file(str(tsv), "delimited")
    assert not block.doc.strings[0].edited
    summary = seen[0]
    assert summary.kind == "Translator table"
    assert [(b.name, b.strings) for b in summary.blocks] == [("D", 1)]

    monkeypatch.setattr("mapchar.ui.dialogs.ImportDialog.exec", lambda self: 1)
    window.import_file(str(tsv), "delimited")
    assert block.doc.strings[0].current_text() == "BB[end]"


def test_a_dropped_translator_file_is_confirmed_like_any_other_import(
    window, tmp_path, monkeypatch
):
    """A drop's kind is a guess from a suffix, so the drop is the path that
    most needs to say what it is about to do."""
    data = bytes.fromhex("41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    tsv = _tsv(tmp_path / "d.tsv", "D/0\t$0\tAB[end]\tBB[end]\tedited\t\n")
    shown: list[str] = []
    monkeypatch.setattr(
        "mapchar.ui.dialogs.ImportDialog.exec",
        lambda self: shown.append(self.windowTitle()) or 1,
    )
    window._open_dropped(str(tsv), "delimited")
    assert shown == ["Import d.tsv"]
    assert block.doc.strings[0].current_text() == "BB[end]"


def test_force_on_the_dialog_takes_back_what_drifted(window, tmp_path, monkeypatch):
    """The only way to an original the project has moved past, and the reason
    the dialog re-plans rather than filtering what it already drew."""
    data = bytes.fromhex("41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    tsv = _tsv(tmp_path / "d.tsv", "D/0\t$0\tmoved on[end]\tBB[end]\tedited\t\n")

    plans: list = []

    def accept_forced(self):
        plans.append(self._summary_for(False))
        self.force.setChecked(True)
        plans.append(self._summary_for(True))
        return 1

    monkeypatch.setattr("mapchar.ui.dialogs.ImportDialog.exec", accept_forced)
    window.import_file(str(tsv), "delimited")
    assert plans[0].skipped == ["D/0: original changed"] and not plans[0].blocks
    assert not plans[1].skipped and plans[1].blocks
    assert block.doc.strings[0].current_text() == "BB[end]"


def test_a_script_import_creates_its_block_and_lands_its_strings(
    window, tmp_path, monkeypatch
):
    """One pass, not two: the block the script carries is created and then the
    script is planned again over it, which is what the summary promised."""
    from helpers import translated
    from mapchar.project.formats.script import DumpMode, write_script

    data = bytes.fromhex("41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "D", RangeSource(0, 6))
    cfg = block.config
    strings, _ = translated(data, cfg, window._table_set(), {0: "BB[end]"})
    script = tmp_path / "s.txt"
    script.write_text(write_script([("Fresh", cfg, strings)], DumpMode.TRANSLATIONS))

    monkeypatch.setattr("mapchar.ui.dialogs.ImportDialog.exec", lambda self: 1)
    window.import_file(str(script), "script")
    made = next(e for e in window.workspace.entries if e.name == "Fresh")
    assert made.doc.strings[0].current_text() == "BB[end]"
    # And the whole of it undoes at once, block and text together.
    window.undo_stack.undo()
    assert not any(e.name == "Fresh" for e in window.workspace.entries)


def test_the_dump_tool_writes_a_project_s_blocks_as_a_script(window, tmp_path):
    """Dump is a development tool now, not a feature: it has no menu entry, and
    this is what keeps it working for the fixture comparisons."""
    import importlib.util

    data = bytes.fromhex("41 42 00 42 41 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6))
    project = tmp_path / "p.mapchar"
    assert window._write_project(str(project))

    spec = importlib.util.spec_from_file_location(
        "dump_script",
        Path(__file__).resolve().parent.parent / "tools" / "dump_script.py",
    )
    tool = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tool)
    out = tmp_path / "dump.txt"
    assert tool.main([str(project), "-o", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "@mapchar script 1" in text
    assert '@block "b"' in text
    assert "@string 1 at $3-$6\nBA[end]\n" in text


def test_the_block_bar_s_export_button_shows_the_file_menu_s_export_menu(
    window, tmp_path
):
    """One QMenu, two places it is shown from, so neither can offer a format
    the other does not."""
    data = bytes.fromhex("41 42 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window.block_export.menu() is window.export_menu
    rows = [a.text().replace("&", "") for a in window.export_menu.actions() if a.text()]
    assert rows == [
        "TSV…",
        "CSV…",
        "PO…",
        "Cartographer Command File…",
        "Atlas Script…",
    ]
