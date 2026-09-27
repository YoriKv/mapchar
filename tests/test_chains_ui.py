"""Chained blocks in the window: the Chained setting, the chain's write
settings, breaks and alignment per string, the grid's marks and the repair."""

from __future__ import annotations

from mapchar.core.block import Align, ChainMode, Pascal, RangeSource
from mapchar.project.projectfile import load_project, save_project
from mapchar.ui.strings_view import COL_INDEX, COL_TRANSLATION, PADDED_ROLE
from window_helpers import ASCII_TABLE, add_block, open_rom_and_table


def _records(*texts: str, tail: int = 4) -> bytes:
    """``[header][length][text]`` records back to back, then ``$FF``."""
    out = b""
    for n, text in enumerate(texts):
        out += bytes([0x10 + n, 0x20, len(text)]) + text.encode()
    return out + b"\xff" * tail


def _chained(window, tmp_path, data, **config):
    file_entry = open_rom_and_table(window, tmp_path, data, ASCII_TABLE)
    config = {"header": 2, "chain": ChainMode.PAD, **config}
    block = add_block(
        window,
        file_entry,
        "story",
        RangeSource(0, len(data) - 4),
        Pascal(1),
        **config,
    )
    window._show_view("strings")
    return file_entry, block


def test_the_chained_box_is_greyed_where_a_block_cannot_chain(window, tmp_path):
    data = _records("HELLO THERE", "BYE")
    file_entry = open_rom_and_table(window, tmp_path, data, ASCII_TABLE)
    block = add_block(window, file_entry, "b", RangeSource(0, 20), Pascal(1), header=2)
    bar = window.reading_bar
    assert bar.chain.isEnabled() and not bar.chain.isChecked()
    assert not bar.writing.chain_mode.isEnabled()
    bar.chain.setChecked(True)
    assert block.config.chain is ChainMode.PAD and block.config.chained
    assert bar.writing.chain_mode.isEnabled() and not bar.writing.write_mode.isEnabled()
    assert bar.writing.itemText(0).startswith("Chained, pad inside string")
    bar.string_type.setCurrentIndex(bar.string_type.findData("fixed"))
    assert not bar.chain.isEnabled()
    assert bar.chain.toolTip().startswith("Unavailable")


def test_the_writing_popup_sets_how_a_chain_is_written(window, tmp_path):
    _file, block = _chained(window, tmp_path, _records("HELLO THERE", "BYE"))
    writing = window.reading_bar.writing
    writing.chain_mode.setCurrentIndex(writing.chain_mode.findData(ChainMode.PACK))
    assert block.config.chain is ChainMode.PACK
    writing.align.setCurrentIndex(writing.align.findData(Align.RIGHT))
    assert block.config.align is Align.RIGHT
    writing.pad.setText("2E")
    writing.pad.editingFinished.emit()
    assert block.config.pad == b"."


def test_an_edit_pads_inside_the_string_and_the_grid_shows_the_pad(window, tmp_path):
    file_entry, block = _chained(window, tmp_path, _records("HELLO THERE", "BYE"))
    window._on_translation_edited(0, "HI")
    assert file_entry.doc.data[:14] == bytes([0x10, 0x20, 11]) + b"HI" + b" " * 9
    table = window.strings.table
    assert table.item(0, COL_INDEX).text() == "» 0"
    assert table.item(1, COL_INDEX).text() == "1"
    cell = table.item(0, COL_TRANSLATION)
    assert cell.data(PADDED_ROLE) == "HI" + "·" * 9
    assert cell.text() == "HI" + " " * 9
    # Too long for its place in the chain: refused, kept unwritten.
    window._on_translation_edited(0, "HELLO THERE!")
    assert block.doc.strings[0].unwritten == "HELLO THERE!"


def test_a_chain_break_and_an_alignment_are_kept(window, tmp_path):
    file_entry, block = _chained(window, tmp_path, _records("HELLO THERE", "BYE", "X"))
    window._toggle_chain_break([2])
    assert block.config.chain_breaks == (2,)
    assert window.strings.table.item(2, COL_INDEX).text() == "» 2"
    window._toggle_chain_break([2])
    assert block.config.chain_breaks == ()
    window.undo_stack.undo()
    assert block.config.chain_breaks == (2,)
    window._align_selected([0], Align.CENTRE)
    assert block.doc.strings[0].align is Align.CENTRE
    window._on_translation_edited(0, "HI")
    assert file_entry.doc.data[3:14] == b"    HI     "
    path = tmp_path / "p.mapchar"
    save_project(str(path), window.workspace.entries, block)
    loaded = load_project(str(path))
    saved = next(e for e in loaded.entries if e.name == "story")
    assert saved.config.chain_breaks == (2,)
    assert saved.pending_strings[0].align is Align.CENTRE


def test_repair_chains_closes_the_gap_a_slotted_write_left(window, tmp_path):
    # "HI" written slotted into an 11-byte string: the fill sits in the chain.
    data = bytes([0x10, 0x20, 2]) + b"HI" + b"\xff" * 9 + _records("BYE")[:-4]
    data = data[:14] + bytes([0x11]) + data[15:] + b"\xff" * 4
    file_entry, block = _chained(window, tmp_path, data)
    assert block.doc.chain_gaps == {1: 5}
    assert any("starts in fill" in n.message for n in block.doc.notices)
    assert window.strings.table.item(1, COL_INDEX).foreground().color().red() > 200
    window._show_view("raw")
    model = window.raw._model
    assert 0 in model.chain_starts and 5 in model.chain_gaps
    window._repair_chains()
    assert file_entry.doc.data[:14] == bytes([0x10, 0x20, 11]) + b"HI" + b" " * 9
    assert not block.doc.chain_gaps
    window.undo_stack.undo()
    assert block.doc.chain_gaps == {1: 5}
