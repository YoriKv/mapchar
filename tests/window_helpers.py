"""Helpers for the window tests: they drive a live ``MainWindow``, so they
live apart from the headless :mod:`helpers`."""

from __future__ import annotations

from PySide6.QtCore import Qt

from helpers import ASCII_TABLE as _ASCII_BODY
from mapchar.core.block import BlockConfig, EndToken
from mapchar.project.entry import Entry, EntryKind
from mapchar.project.formats.table_native import HEADER

TABLE = f"{HEADER}\n@table main\n41=A\n42=B\n/00=[end]\n"
"""A native table file: two letters and an end token."""

ASCII_TABLE = f"{HEADER}\n{_ASCII_BODY}"
"""The same as a file, over the ASCII charset."""

_ENTRY_FIELDS = ("compression_id", "slot_offset", "slot_length")


def open_rom_and_table(window, tmp_path, data, table=TABLE, rom_name="rom.bin"):
    """Write ``data`` and ``table`` into ``tmp_path``, open both in ``window``
    and return the file entry."""
    rom = tmp_path / rom_name
    rom.write_bytes(data)
    tbl = tmp_path / "main.tbl"
    tbl.write_text(table)
    entry = window.open_rom(str(rom))
    window.open_table(str(tbl))
    return entry


def arm_scheme(window, scheme_id: str) -> None:
    """Arm the Decompressed View's preview through ``scheme_id``, the way the
    Format bar's Compression picker does."""
    from mapchar.ui.widgets import select_data

    assert select_data(window.compression_pick, scheme_id), scheme_id
    # Directly as well: the picker fires nothing when it already showed it.
    window._on_compression_pick()


def add_block(window, file_entry, name, source, string_type=None, **config) -> Entry:
    """A block over ``source`` under ``file_entry``, added and made current.

    Keywords go to the block's :class:`BlockConfig`, which reads end-token
    strings — or ``string_type``'s — through the ``main`` table, except the
    compression and slice fields, which belong to the entry.
    """
    fields = {k: config.pop(k) for k in _ENTRY_FIELDS if k in config}
    block = Entry(
        EntryKind.BLOCK,
        name,
        file_entry.path,
        parent=file_entry,
        config=BlockConfig(source, string_type or EndToken(), "main", **config),
        **fields,
    )
    window._push_add(block)
    window._activate_entry(block)
    return block


def grid_keys(editor) -> list[str]:
    """The Key cell of every row the Table Editor's grid shows, in its order.

    The grid is a view over :class:`~mapchar.ui.table_grid._EntryModel`, so a
    row is read from the model rather than from a widget per cell, and a row
    the filter drops is not there at all.
    """
    model = editor.entry_model
    return [model.index(row, 0).data() for row in range(model.rowCount())]


def grid_row(editor, key: str) -> int:
    """The row the Table Editor's grid shows ``key`` in."""
    return grid_keys(editor).index(key)


def grid_cell(editor, row: int, column: int, role=Qt.ItemDataRole.DisplayRole):
    """What one cell of the grid says, or holds under ``role``."""
    return editor.entry_model.index(row, column).data(role)


def type_in_grid(editor, row: int, column: int, text: str) -> None:
    """Type over a Text or Comment cell, as editing it in the grid does."""
    model = editor.entry_model
    model.setData(model.index(row, column), text)


def make_window(qtbot, monkeypatch):
    """A live ``MainWindow`` whose modals answer themselves: one left open is a
    hang the offscreen platform can never clear.

    The three-way "unsaved edits" gate is a box with its own labels rather than a
    standard question, so it is answered by taking its destructive button; the
    reported warnings are collected onto ``window.errors`` for a test to read.
    """
    from PySide6.QtWidgets import QMessageBox

    from mapchar.ui.main_window import MainWindow

    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Discard),
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    errors: list[str] = []
    monkeypatch.setattr(
        QMessageBox,
        "warning",
        staticmethod(lambda _p, _t, message, *a, **k: errors.append(message)),
    )
    monkeypatch.setattr(
        QMessageBox,
        "clickedButton",
        lambda self: next(
            (
                b
                for b in self.buttons()
                if self.buttonRole(b) == QMessageBox.ButtonRole.DestructiveRole
            ),
            None,
        ),
    )
    window = MainWindow()
    window.errors = errors
    qtbot.addWidget(window)
    return window
