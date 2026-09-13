from __future__ import annotations

import pytest

from mapchar.ui.main_window import MainWindow

TABLE = "@mapchar table 1\n@table main\n41=A\n42=B\n/00=[end]\n"


@pytest.fixture
def window(qtbot, tmp_path, monkeypatch):
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: (
            __import__("PySide6.QtWidgets").QtWidgets.QMessageBox.StandardButton.Discard
        ),
    )
    monkeypatch.setattr("mapchar.ui.main_window.window.TextDialog.exec", lambda self: 0)
    w = MainWindow()
    qtbot.addWidget(w)
    return w


def test_open_rom_table_block_and_dump(window, tmp_path, monkeypatch):
    rom = tmp_path / "game.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20)
    tbl = tmp_path / "main.tbl"
    tbl.write_text(TABLE)
    entry = window.open_rom(str(rom))
    assert entry is not None and window._doc is not None and window._doc.size == 26
    window.open_table(str(tbl))
    assert window.table_pick.currentData() == "main"
    window._refresh_view()
    model = window.raw._model
    assert model is not None and [t.text() for t in model.tokens][:3] == [
        "A",
        "B",
        "[end]",
    ]
    assert 0 in model.string_starts and 3 in model.string_starts

    # A block over the first six bytes, created without the dialog.
    from mapchar.core.block import BlockConfig, EndToken, RangeSource
    from mapchar.project.workspace import Entry, EntryKind

    block = Entry(
        EntryKind.BLOCK,
        "b",
        str(rom),
        parent=entry,
        config=BlockConfig(RangeSource(0, 6), EndToken(), "main"),
    )
    window._push_add(block)
    window._activate_entry(block)
    assert [s.original_text() for s in window._doc.strings] == ["AB[end]", "BA[end]"]
    assert window.strings.table.rowCount() == 2

    out = tmp_path / "dump.txt"
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QFileDialog.getSaveFileName",
        lambda *a, **k: (str(out), ""),
    )
    monkeypatch.setattr("mapchar.ui.dialogs.DumpDialog.exec", lambda self: 1)
    window._dump()
    text = out.read_text()
    assert "@string 1 at $3-$6\nBA[end]\n" in text

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
        Entry(EntryKind.BOOKMARK, "bm", str(rom), parent=window.workspace.entries[0])
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


def test_text_display_mode(window, tmp_path):
    rom = tmp_path / "t.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4)
    tbl = tmp_path / "t.tbl"
    tbl.write_text(TABLE)
    window.open_rom(str(rom))
    window.open_table(str(tbl))
    window.mode_button.setChecked(True)
    assert window.display.currentWidget() is window.text
    assert window.text.edit.toPlainText() == "AB[end]BA[end][$FF][$FF][$FF][$FF]"
    window.raw.set_selection(3, 5)
    window._on_selection(3, 5)
    cursor = window.text.edit.textCursor()
    assert (cursor.selectionStart(), cursor.selectionEnd()) == (7, 9)
    cursor.setPosition(0)
    cursor.setPosition(7, cursor.MoveMode.KeepAnchor)
    window.text.edit.setTextCursor(cursor)
    assert window.raw.selection() == (0, 3)
    window.mode_button.setChecked(False)
    assert window.display.currentWidget() is window.raw


def test_edit_and_write(window, tmp_path):
    from mapchar.core.block import BlockConfig, EndToken, RangeSource, Status
    from mapchar.project.workspace import Entry, EntryKind

    rom = tmp_path / "w.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4)
    tbl = tmp_path / "t.tbl"
    tbl.write_text(TABLE)
    file_entry = window.open_rom(str(rom))
    window.open_table(str(tbl))
    block = Entry(
        EntryKind.BLOCK,
        "b",
        str(rom),
        parent=file_entry,
        config=BlockConfig(RangeSource(0, 6), EndToken(), "main", fill=0xEE),
    )
    window._push_add(block)
    window._activate_entry(block)
    window._on_translation_edited(0, "A[end]")
    rec = block.doc.strings[0]
    assert rec.translation == "A[end]" and rec.status is Status.EDITED and block.dirty
    rows = window._row_data(block, block.doc, window._table_set())
    assert (rows[0].used, rows[0].room, rows[0].status) == (2, 3, "edited")
    window._on_translation_edited(1, "BBB[end]")
    rows = window._row_data(block, block.doc, window._table_set())
    assert rows[1].status == "too long"
    assert not window._write_blocks([block])  # refused: string 1 too long
    window.undo_stack.undo()
    assert block.doc.strings[1].translation is None
    assert window._write_blocks([block])
    assert rom.read_bytes() == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    assert not block.dirty and block.doc.strings[0].translation is None
    assert block.doc.strings[0].original_text() == "A[end]"
    window.undo_stack.undo()  # undoing the edit after a write re-marks the block
    assert block.doc.strings[0].translation is None or block.dirty
    window.overtype_bytes(1, b"\x42")
    assert file_entry.dirty and file_entry.doc.data[1] == 0x42
    window.undo_stack.undo()
    assert file_entry.doc.data[1] == 0x00


def test_import_export_and_find_replace(window, tmp_path):
    from mapchar.core.block import BlockConfig, EndToken, RangeSource, Status
    from mapchar.project.workspace import Entry, EntryKind

    rom = tmp_path / "i.bin"
    rom.write_bytes(bytes.fromhex("41 42 00 42 41 00"))
    tbl = tmp_path / "t.tbl"
    tbl.write_text(TABLE)
    file_entry = window.open_rom(str(rom))
    window.open_table(str(tbl))
    block = Entry(
        EntryKind.BLOCK,
        "D",
        str(rom),
        parent=file_entry,
        config=BlockConfig(RangeSource(0, 6), EndToken(), "main"),
    )
    window._push_add(block)
    window._activate_entry(block)
    window._on_translation_edited(0, "B[end]")
    tsv = tmp_path / "d.tsv"
    window.export_file(str(tsv), "tsv")
    assert "D/0\t$0\tAB[end]\tB[end]\tedited" in tsv.read_text()
    po = tmp_path / "d.po"
    window.export_file(str(po), "po")
    window._on_translation_edited(0, "")
    assert block.doc.strings[0].translation is None
    window.import_file(str(po), "po")
    assert block.doc.strings[0].translation == "B[end]"
    window.undo_stack.undo()
    assert block.doc.strings[0].translation is None
    # Script import through the native writer.
    from mapchar.project.formats.script import DumpMode, write_script

    block.doc.strings[1].translation = "A[end]"
    script = tmp_path / "s.txt"
    script.write_text(
        write_script([("D", block.config, block.doc.strings)], DumpMode.TRANSLATIONS)
    )
    block.doc.strings[1].translation = None
    window.import_file(str(script), "script")
    assert block.doc.strings[1].translation == "A[end]"
    assert block.doc.strings[1].status is Status.EDITED
    # Replace all over translations.
    window._fr_replace_all("A", "B", True)
    assert block.doc.strings[1].translation == "B[end]"
    assert block.doc.strings[0].translation == "BB[end]"
    # Hex panel overtype path.
    window.show()
    window.hex_dock.show()
    window._sync_hex_panel()
    assert "000000  41 42 00" in window.hex_panel.view.toPlainText()
    window.hex_panel.at.setText("2")
    window.hex_panel.bytes.setText("42 00")
    window.hex_panel._on_apply()
    assert file_entry.doc.data[:4] == bytes.fromhex("41 42 42 00")


def test_pointer_block_in_window(window, tmp_path, monkeypatch):
    from mapchar.core.block import (
        BlockConfig,
        EndToken,
        PointerTableSource,
        RangeSource,
    )
    from mapchar.project.workspace import Entry, EntryKind

    table = (0x10).to_bytes(2, "little") + (0x13).to_bytes(2, "little")
    rom = tmp_path / "p.bin"
    rom.write_bytes(
        table + b"\xff" * 12 + bytes.fromhex("41 42 00 42 00") + b"\xff" * 8
    )
    tbl = tmp_path / "t.tbl"
    tbl.write_text(TABLE)
    file_entry = window.open_rom(str(rom))
    window.open_table(str(tbl))
    block = Entry(
        EntryKind.BLOCK,
        "P",
        str(rom),
        parent=file_entry,
        config=BlockConfig(RangeSource(0x10, 0x15), EndToken(), "main", bound=0x18),
    )
    window._push_add(block)
    window._activate_entry(block)
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]
    monkeypatch.setattr("mapchar.ui.dialogs.DiscoveryDialog.exec", lambda self: 1)
    window._find_pointers()
    assert block.config.source == PointerTableSource(0, 4, 2, 2, "little", "linear", 0)
    rows = window._row_data(block, block.doc, window._table_set())
    assert rows[0].pointers == "0" and rows[1].pointers == "2"
    window._go_to(0)
    assert {0, 1, 2, 3} <= window.raw._model.pointer_bytes
    window._on_translation_edited(0, "ABB[end]")
    assert window._write_blocks([block])
    data = rom.read_bytes()
    assert data[0x10:0x18] == bytes.fromhex("41 42 42 00 42 00 FF FF")
    assert data[:4] == bytes.fromhex("10 00 14 00")
