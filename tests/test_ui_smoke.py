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
    assert window.strings.rowCount() == 2

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
