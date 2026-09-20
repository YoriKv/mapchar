"""Find Pointers in the window: what the search covers, and what is done with it.

The engine is tested headless in :mod:`test_pointers`; here is the pair of
dialogs around it — the scope and offset range asked before the search, and the
two ways out of the results.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QMessageBox

from helpers import pointer_rom
from mapchar.core.block import RangeSource
from mapchar.ui.dialogs import DiscoveryDialog, PointerSearchDialog
from window_helpers import add_block, make_window, open_rom_and_table

ROM = pointer_rom((0x10, 0x13, 0x10), "41 42 00 42 00")
"""Three pointers at 0, one a duplicate, to AB[end] at $10 and B[end] at $13."""


@pytest.fixture
def window(qtbot, monkeypatch):
    return make_window(qtbot, monkeypatch)


@pytest.fixture
def block(window, tmp_path):
    entry = open_rom_and_table(window, tmp_path, ROM)
    return add_block(window, entry, "b", RangeSource(0x10, 0x15))


def _answer(monkeypatch, *, attach: bool) -> None:
    """Take both dialogs, leaving the results table on its top row."""
    monkeypatch.setattr(
        PointerSearchDialog,
        "exec",
        lambda self: PointerSearchDialog.DialogCode.Accepted,
    )

    def take(self):
        self.attach = attach
        return DiscoveryDialog.DialogCode.Accepted

    monkeypatch.setattr(DiscoveryDialog, "exec", take)


# --- what the search covers -------------------------------------------------


def test_the_offset_range_is_both_ends_and_a_step(qtbot):
    dialog = PointerSearchDialog(3, None, None)
    assert dialog.offsets() == (0,)  # the default range is the single offset 0
    dialog.offset_to.setText("$10")
    dialog.offset_step.setText("8")
    assert dialog.offsets() == (0, 8, 0x10)
    dialog.offset_from.setText("-4")
    dialog.offset_to.setText("4")
    dialog.offset_step.setText("4")
    assert dialog.offsets() == (-4, 0, 4)
    # Not really a range: a backwards end, or no step, is the offset it starts at.
    dialog.offset_step.setText("0")
    assert dialog.offsets() == (-4,)


def test_an_unreadable_offset_range_refuses_to_close(qtbot, monkeypatch):
    said: list[str] = []
    monkeypatch.setattr(
        QMessageBox, "warning", staticmethod(lambda *a, **k: said.append(a[2]))
    )
    dialog = PointerSearchDialog(3, None, None)
    dialog.offset_to.setText("1-2")
    dialog.accept()
    assert dialog.result() != PointerSearchDialog.DialogCode.Accepted
    assert said and "not made of numbers" in said[0]


def test_scope_is_offered_only_when_a_string_is_selected(qtbot):
    assert PointerSearchDialog(3, None, None).scope.count() == 1
    dialog = PointerSearchDialog(3, 1, None)
    assert dialog.scope.count() == 2
    assert not dialog.selected_only()  # the whole block is the default
    dialog.scope.setCurrentIndex(1)
    assert dialog.selected_only()


def test_the_selected_string_narrows_the_search(window, block, monkeypatch):
    """The scope reaches the engine as the starts it is handed, so one string is
    one string's worth of work."""
    seen: list[list[int]] = []
    monkeypatch.setattr(
        "mapchar.ui.main_window.pointers.discover",
        lambda data, starts, mappings, **kw: seen.append(list(starts)) or [],
    )
    monkeypatch.setattr(
        PointerSearchDialog,
        "exec",
        lambda self: (
            self.scope.setCurrentIndex(1),
            PointerSearchDialog.DialogCode.Accepted,
        )[1],
    )
    window.strings.select_index(1)
    window._find_pointers()
    assert seen == [[0x13]]


def test_the_offset_range_reaches_the_engine(window, block, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        "mapchar.ui.main_window.pointers.discover",
        lambda data, starts, mappings, **kw: seen.update(kw) or [],
    )
    monkeypatch.setattr(
        PointerSearchDialog,
        "exec",
        lambda self: (
            self.offset_to.setText("4"),
            self.offset_step.setText("2"),
            PointerSearchDialog.DialogCode.Accepted,
        )[2],
    )
    window._find_pointers()
    assert seen["offsets"] == (0, 2, 4)


# --- what is done with the result -------------------------------------------


def test_attach_puts_the_pointers_on_the_strings_and_undoes(window, block, monkeypatch):
    """Attach believes the addresses and not the table: the source stays as it
    was, and the strings gain the pointers that reach them."""
    before = block.config.source
    assert not any(rec.pointers for rec in block.doc.strings)
    _answer(monkeypatch, attach=True)
    window._find_pointers()

    assert block.config.source is before  # no re-read, no new source
    assert [p.address for p in block.doc.strings[0].pointers] == [0, 4]
    assert [p.address for p in block.doc.strings[1].pointers] == [2]
    ref = block.doc.strings[1].pointers[0]
    assert (ref.size, ref.endian, ref.mapping_id, ref.offset, ref.value) == (
        2,
        "little",
        "linear",
        0,
        0x13,
    )
    # The Pointers column is where it shows, and it is one undo step.
    rows = window._row_data(block, block.doc)
    assert rows[0].pointers == "0 4" and rows[1].pointers == "2"
    window.undo_stack.undo()
    assert not any(rec.pointers for rec in block.doc.strings)
    window.undo_stack.redo()
    assert len(block.doc.strings[0].pointers) == 2


def test_attach_twice_finds_nothing_left_to_do(window, block, monkeypatch):
    _answer(monkeypatch, attach=True)
    window._find_pointers()
    steps = window.undo_stack.count()
    window._find_pointers()
    assert window.undo_stack.count() == steps  # no empty step on the stack
    assert any("already carry" in message for message in window.errors)


def test_use_as_pointer_table_still_rewrites_the_source(window, block, monkeypatch):
    from mapchar.core.block import PointerTableSource

    _answer(monkeypatch, attach=False)
    window._find_pointers()
    assert isinstance(block.config.source, PointerTableSource)
