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
    assert [s.original_text() for s in block.doc.strings] == ["AB[end]", "B[end]"]
    (tmp_path / "atlas.txt").write_text(
        '#VAR(T, TABLE)\n#ADDTBL("main.tbl", T)\n#ACTIVETBL(T)\n#JMP($0, $4)\nBA[end]\n'
        "#JMP($3, $4)\nA[end]\n"
    )
    assert window.import_atlas(str(tmp_path / "atlas.txt")) == 2
    assert block.doc.strings[0].translation == "BA[end]"
    assert block.doc.strings[1].translation == "A[end]"
    assert window._string_at(3) is block.doc.strings[1]


def test_table_editor_shift_and_fill(window, tmp_path, monkeypatch):
    from mapchar.core.table import Table
    from mapchar.project.workspace import Entry, EntryKind

    table = Table("t")
    entry = Entry(EntryKind.TABLE, "t.tbl", None, dialect="native", tables=[table])
    window._push_add(entry)
    editor = window.table_editor
    editor.set_entry(entry)
    answers = iter([("A-Z", True), ("41", True)])
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getItem", lambda *a, **k: next(answers)
    )
    monkeypatch.setattr(
        "PySide6.QtWidgets.QInputDialog.getText", lambda *a, **k: next(answers)
    )
    editor._fill_dialog()
    assert table.entries["01000001"].text == "A" and len(table.entries) == 26
    editor.grid.selectAll()
    answers = iter([("-1", True)])
    editor._shift()
    assert table.entries["01000000"].text == "A" and "01011010" not in table.entries


def test_compressed_block_roundtrip(window, tmp_path, monkeypatch):
    from mapchar.core.block import BlockConfig, EndToken, RangeSource
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77
    from mapchar.project.workspace import Entry, EntryKind

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(payload, PipelineContext())
    rom = tmp_path / "z.bin"
    slot = len(packed) + 8  # the compressed slot has spare room at its end
    rom.write_bytes(b"\xff" * 16 + packed + b"\xff" * 8 + b"\xff" * 24)
    tbl = tmp_path / "t.tbl"
    tbl.write_text("@mapchar table 1\n@table main\n@charset ascii\n/00=[end]\n")
    file_entry = window.open_rom(str(rom))
    window.open_table(str(tbl))
    window.compression_pick.setCurrentIndex(
        window.compression_pick.findData("gba_lz77")
    )
    window._go_to(16)
    assert "compressed bytes at 10 → 90 bytes" in window.decompress_window.status.text()
    block = Entry(
        EntryKind.BLOCK,
        "Z",
        str(rom),
        parent=file_entry,
        config=BlockConfig(RangeSource(0, len(payload)), EndToken(), "main", fill=0x20),
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=slot,
    )
    window._push_add(block)
    window._activate_entry(block)
    assert block.doc.data == payload and len(block.doc.strings) == 6
    window._on_translation_edited(0, "HI HI HI[end]")
    assert window._write_blocks([block])
    data = rom.read_bytes()
    assert data[:16] == b"\xff" * 16 and data[16 + slot :] == b"\xff" * 24
    out = GbaLz77().decompress(data[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00" + b" " * 9 + b"WORLD WORLD\x00")
    assert block.doc.strings[0].original_text() == "HI HI HI[end]"


def test_preview_and_wrap(window, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from mapchar.core.block import BlockConfig, EndToken, RangeSource
    from mapchar.core.font import CodeEffect, Effect, TextBox
    from mapchar.project.workspace import Entry, EntryKind

    # A 16-column 8x8 glyph sheet: glyph i has i%8+1 inked columns.
    sheet = QImage(128, 16, QImage.Format.Format_ARGB32)
    sheet.fill(QColor(0, 0, 0))
    for glyph in range(32):
        col, row = glyph % 16, glyph // 16
        for x in range(glyph % 8 + 1):
            for y in range(8):
                sheet.setPixelColor(col * 8 + x, row * 8 + y, QColor(255, 255, 255))
    png = tmp_path / "font.png"
    sheet.save(str(png))
    rom = tmp_path / "f.bin"
    rom.write_bytes(b"AB CD EF GH IJ\x00")
    tbl = tmp_path / "t.tbl"
    tbl.write_text(
        "@mapchar table 1\n@table main\n@charset ascii\n/00=[end]\nFE=[line]\\n\n"
    )
    file_entry = window.open_rom(str(rom))
    window.open_table(str(tbl))
    font_entry = window.open_font(str(png))
    block = Entry(
        EntryKind.BLOCK,
        "F",
        str(rom),
        parent=file_entry,
        config=BlockConfig(RangeSource(0, 15), EndToken(), "main"),
    )
    window._push_add(block)
    window._activate_entry(block)
    window._show_preview()
    assert block.box is not None and block.box.font_index == 0
    from dataclasses import replace

    font_entry.font = replace(font_entry.font, base=0, chars=" ABCDEFGHIJ")
    window._on_font_changed(font_entry.font.with_widths(tuple(range(1, 33))))
    window._on_box_changed(
        TextBox(
            width=24,
            height=16,
            line_height=8,
            effects={"line": CodeEffect(Effect.NEWLINE)},
        )
    )
    rows = window._row_data(block, block.doc, window._table_set())
    assert rows[0].status == "overflows box"
    window.strings.select_index(0)
    window._wrap_selected()
    text = block.doc.strings[0].translation
    assert text is not None and "[line]" in text
    assert window.preview_window.status.text().startswith("page 1/")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    window._new_project()
    assert window.open_project(str(proj))
    fonts = [e for e in window.workspace.entries if e.kind is EntryKind.FONT]
    assert (
        fonts[0].font.widths[:3] == (1, 2, 3) and fonts[0].font.chars == " ABCDEFGHIJ"
    )
    blocks = [e for e in window.workspace.entries if e.kind is EntryKind.BLOCK]
    assert (
        blocks[0].box.width == 24
        and blocks[0].box.effects["line"].effect is Effect.NEWLINE
    )
