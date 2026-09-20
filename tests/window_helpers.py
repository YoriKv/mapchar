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

ABC_TABLE = f"{HEADER}\n@table main\n41=A\n42=B\n43=C\n/00=[end]\n"
"""Three letters and an end token."""

CODES_TABLE = (
    f"{HEADER}\n@table main\n41=A\n42=B\n43=C\nFE=[line]\n$FD=[color],u8\n/00=[end]\n"
)
"""Three letters, a line code, a code with an operand, and an end token."""

ABCDE_TABLE = "@main\n41=A\n42=B\n/00=[end]\n"
"""What :data:`TABLE` holds, in the abcde dialect a table read has to guess."""

_ENTRY_FIELDS = ("compression_id", "slot_offset", "slot_length")


def ab_ba_rom(tail: int = 20) -> bytes:
    """``AB[end]BA[end]`` and ``tail`` bytes of ``$FF`` padding after it: the
    six bytes most window tests read, over :data:`TABLE`."""
    return bytes.fromhex("41 42 00 42 41 00") + b"\xff" * tail


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
    compression and slot fields, which belong to the entry.
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


def gba_packed(payload: bytes) -> bytes:
    """``payload`` as a GBA LZ77 stream."""
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    return GbaLz77().compress(payload, PipelineContext())


def gba_packed_rom(
    payload: bytes, *, spare: int = 16, tail: int = 8
) -> tuple[bytes, int, bytes]:
    """The payload compressed, the slot it needs, and a ROM holding it at 16.

    The slot is the stream plus ``spare`` bytes — room for a re-compression that
    packs worse than the original, which is what editing text does — and the ROM
    is ``$FF`` up to 16, the stream, the slot's spare room and ``tail`` bytes
    past the slot.
    """
    packed = gba_packed(payload)
    return packed, len(packed) + spare, b"\xff" * 16 + packed + b"\xff" * (spare + tail)


def item_for(window, entry):
    """The Files panel's row for ``entry``."""
    return window.files_panel._items[id(entry)]


def menu_actions(window, menu=None):
    """Every ``(label, action)`` in the menu bar, submenus walked in place.

    The label is the action's text with its mnemonic marker dropped. Open Recent
    is skipped: its rows are project names that come and go.
    """
    from mapchar.ui.help_dialogs import submenus

    menu = window.menuBar() if menu is None else menu
    for action in menu.actions():
        if action.isSeparator():
            continue
        yield action.text().replace("&", "").strip(), action
        submenu = submenus(window.menuBar()).get(action)
        if submenu is not None and submenu is not window.recent_menu:
            yield from menu_actions(window, submenu)


def menu_state(menu) -> dict[str, bool]:
    """``{label: enabled}`` for the rows of one menu, mnemonic markers dropped."""
    return {
        a.text().replace("&", ""): a.isEnabled() for a in menu.actions() if a.text()
    }


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


def make_yes_window(qtbot, monkeypatch):
    """A live ``MainWindow`` whose every modal answers Yes without showing.

    What the entry tests want, and where it parts from :func:`make_window`: a
    question is taken rather than declined, and a reported warning is swallowed
    rather than collected, so a path that warns carries straight on.
    """
    from PySide6.QtWidgets import QMessageBox

    from mapchar.ui.main_window import MainWindow

    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.question",
        lambda *a, **k: QMessageBox.StandardButton.Yes,
    )
    monkeypatch.setattr(
        "mapchar.ui.main_window.window.QMessageBox.warning", lambda *a, **k: 0
    )
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    window = MainWindow()
    qtbot.addWidget(window)
    return window
