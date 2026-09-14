from __future__ import annotations

import re
from pathlib import Path

import pytest

from helpers import pointer_rom, texts
from mapchar.core.block import RangeSource, Status
from mapchar.project.workspace import Entry, EntryKind
from mapchar.ui.main_window import MainWindow
from window_helpers import ASCII_TABLE, TABLE, add_block, open_rom_and_table


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


def test_open_rom_table_block_and_dump(window, tmp_path, monkeypatch):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
    entry = open_rom_and_table(window, tmp_path, data, rom_name="game.bin")
    assert entry is not None and window._doc is not None and window._doc.size == 26
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
    add_block(window, entry, "b", RangeSource(0, 6))
    assert texts(window._doc.strings) == ["AB[end]", "BA[end]"]
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
    assert window.text.edit.toPlainText() == "AB[end]BA[end][$FF][$FF][$FF][$FF]"
    window.raw.set_selection(3, 5)
    window._on_selection(3, 5)
    cursor = window.text.edit.textCursor()
    assert (cursor.selectionStart(), cursor.selectionEnd()) == (7, 9)
    cursor.setPosition(0)
    cursor.setPosition(7, cursor.MoveMode.KeepAnchor)
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

    # Each "ABBBB[end]" is ten characters over six bytes.
    send(QEvent.Type.MouseButtonPress, 60, left)
    for char in (57, 50, 40, 30):
        send(QEvent.Type.MouseMove, char, Qt.MouseButton.NoButton)
    assert edit.textCursor().anchor() == 60
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
    block = add_block(window, file_entry, "b", RangeSource(0, 6), fill=0xEE)
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
    assert (
        Path(file_entry.path).read_bytes()
        == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    )
    assert not block.dirty and block.doc.strings[0].translation is None
    assert block.doc.strings[0].original_text() == "A[end]"
    window.undo_stack.undo()  # undoing the edit after a write re-marks the block
    assert block.doc.strings[0].translation is None
    # Strictly dirty, not "dirty or unchanged": the command restores the revision
    # token the entry had *before* the edit, which is not the one the write saved.
    assert block.dirty
    window.overtype_bytes(1, b"\x42")
    assert file_entry.dirty and file_entry.doc.data[1] == 0x42
    window.undo_stack.undo()
    assert file_entry.doc.data[1] == 0x00


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
    assert {0, 1, 2, 3} <= window.raw._model.pointer_bytes
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
    assert block.doc.strings[0].translation == "BA[end]"
    assert block.doc.strings[1].translation == "A[end]"
    assert window._string_at(3) is block.doc.strings[1]


def test_table_editor_shift_and_fill(window, tmp_path, monkeypatch):
    from mapchar.core.table import Table

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
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(payload, PipelineContext())
    slot = len(packed) + 8  # the compressed slot has spare room at its end
    data = b"\xff" * 16 + packed + b"\xff" * 8 + b"\xff" * 24
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    window.compression_pick.setCurrentIndex(
        window.compression_pick.findData("gba_lz77")
    )
    window._go_to(16)
    assert "compressed bytes at 10 → 90 bytes" in window.decompress_window.status.text()
    block = add_block(
        window,
        file_entry,
        "Z",
        RangeSource(0, len(payload)),
        fill=0x20,
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
    assert out.startswith(b"HI HI HI\x00" + b" " * 9 + b"WORLD WORLD\x00")
    assert block.doc.strings[0].original_text() == "HI HI HI[end]"


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
        fill=0x20,
        compression_id="gba_lz77",
        slice_offset=16,
        slice_length=slot,
    )
    second = add_block(
        window,
        file_entry,
        "back",
        RangeSource(30, 60),
        fill=0x20,
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
    assert out.startswith(b"HI HI HI\x00" + b" " * 9)
    assert out[30:].startswith(b"BYE\x00" + b" " * 14)
    assert not first.dirty and not second.dirty


def test_siblings_over_a_written_slot_refresh_but_keep_their_edits(window, tmp_path):
    """A block that was not written still reads the region that just changed.

    Its bytes are a *decode* of the slot rather than a window on it, so a clean
    one is dropped and decompresses again when it is next shown. One carrying
    unsaved edits keeps its document, because that is where they live.
    """
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(payload, PipelineContext())
    slot = len(packed) + 16
    data = b"\xff" * 16 + packed + b"\xff" * 16 + b"\xff" * 24
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    slice_fields = dict(
        fill=0x20, compression_id="gba_lz77", slice_offset=16, slice_length=slot
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

    window._activate_entry(written)
    window._on_translation_edited(0, "HI HI HI[end]")
    assert window._write_blocks([written])

    assert clean.doc is None  # dropped: it decodes the slot afresh next time
    assert edited.doc is not None
    assert edited.doc.strings[0].translation == "KEPT[end]"
    assert edited.dirty
    # And the dropped one reads the bytes the write left behind.
    assert window._load_document(clean).data[:8] == b"HI HI HI"


def test_preview_and_wrap(window, tmp_path):
    from PySide6.QtGui import QColor, QImage

    from mapchar.core.font import CodeEffect, Effect, TextBox

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
    file_entry = open_rom_and_table(
        window, tmp_path, b"AB CD EF GH IJ\x00", table=ASCII_TABLE + "FE=[line]\\n\n"
    )
    font_entry = window.open_font(str(png))
    block = add_block(window, file_entry, "F", RangeSource(0, 15))
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
    assert "01000011" in table_entry.tables[0].entries
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


def test_fonts_panel_lists_and_binds(window, tmp_path):
    from PySide6.QtGui import QImage

    file_entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    png = tmp_path / "f.png"
    QImage(128, 8, QImage.Format.Format_ARGB32).save(str(png))
    font_entry = window.open_font(str(png))
    assert window.fonts_panel.tree.topLevelItemCount() == 1
    block = add_block(window, file_entry, "B", RangeSource(0, 3))
    window._edit_font_entry(font_entry)
    assert block.box is not None and block.box.font_index == 0
    window.fonts_panel.rebuild()
    assert window.fonts_panel.tree.topLevelItem(0).childCount() == 1


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


def test_saving_a_project_offers_to_write_the_unsaved_edits(window, tmp_path):
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._on_translation_edited(0, "BA[end]")
    assert block.dirty
    # The fixture answers the gate with "Continue Without": the project saves and
    # the edit stays in memory, unwritten.
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert block.dirty
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
    assert not window.codecs_bar.isEnabled()
    assert not window.offset_box.isEnabled()
    assert not window.write_action.isEnabled()
    assert not window.block_bar.isVisible()


def test_a_file_gates_the_string_surfaces_off(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, b"AB\x00")
    window._activate_entry(entry)
    assert window.codecs_bar.isEnabled()
    assert window.offset_box.isEnabled() and window.goto_action.isEnabled()
    assert window.container_action.isEnabled()
    assert not window.strings_tab_action.isEnabled()
    assert not window.find_replace_action.isEnabled()
    assert not window.tabs.isTabEnabled(window.tabs.indexOf(window.strings))
    assert not window.block_bar.isVisibleTo(window)


def test_a_block_gates_the_string_surfaces_on(window, tmp_path):
    data = bytes.fromhex("41 42 00")
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 3))
    assert window._entry is block
    assert window.strings_tab_action.isEnabled()
    assert window.find_replace_action.isEnabled()
    assert window.preview_action.isEnabled()
    assert window.tabs.isTabEnabled(window.tabs.indexOf(window.strings))
    assert window.block_bar.isVisibleTo(window)
    assert window.block_edit.isEnabled() and window.block_dump.isEnabled()
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
# (armed by the trail), the undo pair (armed by the stack), the entry clipboard
# (scoped to the Files panel, which has a selection of its own) and Show Whole
# File (armed by the refresh, while the view is confined).
ALWAYS_ON = frozenset(
    {
        "Open ROM…",
        "Open Table…",
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
        "Light Theme",
        "Dark Theme",
        "Back",
        "Forward",
        "Show Whole File",
        "Files",
        "Tables",
        "Fonts",
        "Hex",
        "Reset Panel Layout",
        "Shortcuts…",
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


def test_a_shift_jis_script_is_offered_a_re_read(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    path = tmp_path / "commands.txt"
    path.write_bytes("#BLOCK ソ\n".encode("cp932"))
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(
            b
            for b in self.buttons()
            if self.buttonRole(b) == QMessageBox.ButtonRole.AcceptRole
        ),
    )
    text = window._read_text(str(path))
    assert text is not None and "ソ" in text


def test_declining_the_re_read_reports_nothing_read(window, tmp_path):
    path = tmp_path / "commands.txt"
    path.write_bytes("#BLOCK ソ\n".encode("cp932"))
    # The fixture's clickedButton takes the destructive button, and this box has
    # none — so the dialog reads as cancelled.
    assert window._read_text(str(path)) is None


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
    tools read keeps saying what it said until Save Table folds the overlay in."""
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
    open_rom_and_table(window, tmp_path, data)
    table_entry = window.workspace.of_kind(EntryKind.TABLE)[0]
    tbl = Path(str(table_entry.path))
    on_disk = tbl.read_text()

    editor = window.table_editor
    editor.set_entry(table_entry)
    editor.new_line.setText("43=C")
    editor._add()
    assert "01000011" in table_entry.tables[0].entries
    assert tbl.read_text() == on_disk  # the file is untouched
    assert table_entry.table_overlay == {"main": {"01000011": "43=C"}}

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
    assert {"01000011", "01000100"} <= set(back.tables[0].entries)

    # A reload from disk picks up what changed there and keeps the edits on top.
    tbl.write_text(on_disk + "45=E\n")
    window.reload_table(back)
    keys = set(back.tables[0].entries)
    assert "01000101" in keys and {"01000011", "01000100"} <= keys
    assert back.table_overlay  # still unspent

    # Save As Native writes them out, and the overlay is spent.
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
    assert back.tables[0].entries["01000001"].text == "A"


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


def test_a_plugin_refresh_keeps_a_clean_block_s_translations(window, tmp_path):
    """F5 drops every cached document it can; a block's translations live in one,
    so they have to be stashed on the entry on the way out."""
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20
    file_entry = open_rom_and_table(window, tmp_path, data)
    block = add_block(window, file_entry, "b", RangeSource(0, 6))
    window._on_translation_edited(0, "ZZ[end]")
    window.workspace.mark_saved(block)  # as if it had been written back
    assert not block.dirty
    window._reload_plugins = lambda project_dir: (window.registry, [])
    window._refresh_plugins()
    assert block.doc is not None
    assert block.doc.strings[0].translation == "ZZ[end]"
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))
    assert "ZZ[end]" in Path(proj).read_text()


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


def test_a_reopened_block_with_translations_is_unsaved_and_writes(window, tmp_path):
    """Translations a project carries are not on disk: the block they belong
    to has to read unsaved again, or Write All would report nothing to write."""
    data = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 4
    file_entry = open_rom_and_table(window, tmp_path, data)
    add_block(window, file_entry, "b", RangeSource(0, 6), fill=0xEE)
    window._on_translation_edited(0, "A[end]")
    proj = tmp_path / "p.mapchar"
    assert window._write_project(str(proj))  # Continue Without, by the fixture
    window._new_project()
    assert window.open_project(str(proj))
    back = window.workspace.of_kind(EntryKind.BLOCK)[0]
    assert back.dirty and window._dirty_blocks() == [back]
    assert window._write_all()
    assert (
        Path(file_entry.path).read_bytes()
        == bytes.fromhex("41 00 EE 42 41 00") + b"\xff" * 4
    )
    assert not back.dirty


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
    assert window.table_pick.currentData() == "other"
    window._activate_entry(block)
    assert window.table_pick.currentData() == "main"
    assert block.doc is doc
