"""A ROM changed on disk by another program: noticed, offered, and reloaded
with the edits made here kept."""

from __future__ import annotations

from pathlib import Path

from mapchar.core.block import RangeSource
from mapchar.pipeline.filechange import DiskState, merge
from window_helpers import ASCII_TABLE, add_block, gba_packed_rom, open_rom_and_table

DATA = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20


def _block(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    return file_entry, add_block(window, file_entry, "b", RangeSource(0, 6))


def _answer(window, monkeypatch, reload: bool):
    """Record what the window asks — ``(names, unsaved)`` — answering ``reload``."""
    asked: list[tuple[list[str], list[str]]] = []
    monkeypatch.setattr(
        type(window),
        "_ask_reload",
        lambda self, names, unsaved: (asked.append((names, unsaved)), reload)[1],
    )
    return asked


def _changed(window, *paths: str) -> None:
    """What the watcher's signal and its rest timer do, without the wait."""
    for path in paths:
        window._on_file_changed(path)
    window._check_changed_files()


# --- the merge and the fingerprints ------------------------------------------


def test_edits_win_and_everything_else_takes_the_disk():
    base = bytes(range(16))
    local = bytearray(base)
    local[2:4] = b"\xaa\xbb"
    local[9] = 0xCC
    disk = bytes(16)
    out = merge(base, bytes(local), disk)
    assert out.data == b"\x00\x00\xaa\xbb" + b"\x00" * 5 + b"\xcc" + b"\x00" * 6
    assert (out.kept, out.conflicts, out.dropped) == (3, 3, 0)
    same = merge(base, base, disk)
    assert same.data is disk and same.kept == 0


def test_a_byte_both_sides_changed_is_a_conflict_the_edit_still_wins():
    base, local = b"ABCD", b"AXCD"
    out = merge(base, local, b"AYCZ")
    assert out.data == b"AXCZ" and (out.kept, out.conflicts) == (1, 1)
    # The disk changing a byte the edit did not is no conflict at all.
    out = merge(base, local, b"ABcD")
    assert out.data == b"AXcD" and (out.kept, out.conflicts) == (1, 0)


def test_an_edit_past_the_end_of_a_shrunk_file_is_dropped_and_counted():
    base = bytes(10000)
    local = bytearray(base)
    local[4090:4100] = b"\x01" * 10
    local[9998:] = b"\x02\x02"
    out = merge(base, bytes(local), bytes(4095))
    assert out.data == bytes(4090) + b"\x01" * 5
    assert (out.kept, out.conflicts, out.dropped) == (5, 0, 7)
    # An edit that lengthened the buffer has nowhere to land either.
    out = merge(b"AB", b"ABCD", b"ab")
    assert out.data == b"ab" and (out.kept, out.dropped) == (0, 2)


def test_fingerprints_tell_a_touch_from_a_change_and_skip_a_missing_file(tmp_path):
    rom = tmp_path / "r.bin"
    rom.write_bytes(DATA)
    state = DiskState()
    assert not state.changed(str(rom))  # never recorded: nothing to go stale
    state.record((str(rom),))
    assert not state.changed(str(rom))
    rom.write_bytes(DATA)  # the time moves, the bytes do not
    assert not state.changed(str(rom))
    rom.write_bytes(DATA[:-1] + b"\x00")
    assert state.changed(str(rom))
    rom.unlink()
    assert not state.changed(str(rom))
    state.forget((str(rom),))
    rom.write_bytes(DATA)
    assert not state.changed(str(rom))


# --- noticing ---------------------------------------------------------------


def test_an_open_rom_is_watched_and_a_closed_one_is_not(window, tmp_path):
    file_entry, _ = _block(window, tmp_path)
    assert file_entry.path in window.file_watcher.files()
    window.workspace.close(file_entry)
    assert file_entry.path not in window.file_watcher.files()


def test_a_touch_that_changed_nothing_asks_nothing(window, tmp_path, monkeypatch):
    file_entry, _ = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    Path(file_entry.path).write_bytes(DATA)
    _changed(window, file_entry.path)
    assert not asked


def test_a_file_that_is_gone_asks_nothing(window, tmp_path, monkeypatch):
    file_entry, _ = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    Path(file_entry.path).unlink()
    _changed(window, file_entry.path)
    assert not asked and not window.errors


def test_the_apps_own_write_asks_nothing(window, tmp_path, monkeypatch):
    file_entry, block = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    window._on_translation_edited(0, "B[end]")
    assert window._write_all()
    _changed(window, file_entry.path)
    assert not asked
    # Nor does undoing it, which writes the file too: the buffer keeps the
    # edit, unsaved again, and the disk is what it was.
    window.undo_stack.undo()
    _changed(window, file_entry.path)
    assert not asked and file_entry.dirty
    assert Path(file_entry.path).read_bytes() == DATA
    assert block.doc.strings[0].current_text() == "B[end]"


def test_the_watcher_itself_reaches_the_prompt(window, tmp_path, monkeypatch, qtbot):
    """The whole path for once: the watcher's signal, the rest timer, the
    look at the bytes, the question."""
    file_entry, _ = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, False)
    Path(file_entry.path).write_bytes(bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20)
    qtbot.waitUntil(lambda: bool(asked), timeout=5000)
    assert asked == [(["rom.bin"], [])]


def test_the_question_waits_while_another_dialog_is_up(window, tmp_path, monkeypatch):
    from PySide6.QtWidgets import QApplication

    file_entry, _ = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, False)
    Path(file_entry.path).write_bytes(bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20)
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: window))
    _changed(window, file_entry.path)
    assert not asked and window._file_change_rest.isActive()
    assert file_entry.path in window._changed_paths
    monkeypatch.setattr(QApplication, "activeModalWidget", staticmethod(lambda: None))
    window._check_changed_files()
    assert len(asked) == 1


def test_several_changed_files_are_one_question(window, tmp_path, monkeypatch):
    first, _ = _block(window, tmp_path)
    other = tmp_path / "other.bin"
    other.write_bytes(DATA)
    second = window.open_rom(str(other))
    asked = _answer(window, monkeypatch, True)
    changed = bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20
    Path(first.path).write_bytes(changed)
    other.write_bytes(changed)
    _changed(window, first.path, str(other))
    assert asked == [(["other.bin", "rom.bin"], [])]
    assert first.doc.data == changed and second.doc.data == changed
    assert window.statusBar().currentMessage() == "Reloaded 2 files"


# --- reloading --------------------------------------------------------------


def test_a_changed_file_is_offered_and_reloaded(window, tmp_path, monkeypatch):
    file_entry, block = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    changed = bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20
    Path(file_entry.path).write_bytes(changed)
    _changed(window, file_entry.path)
    assert asked == [(["rom.bin"], [])]
    assert file_entry.doc.data == changed and file_entry.doc.base == changed
    assert block.doc.data == changed
    assert block.doc.strings[0].current_text() == "BA[end]"
    assert not file_entry.dirty and not block.dirty
    assert window.statusBar().currentMessage() == "Reloaded rom.bin"
    # The same bytes again ask nothing more.
    _changed(window, file_entry.path)
    assert len(asked) == 1


def test_edits_made_here_are_laid_over_what_was_reloaded(window, tmp_path, monkeypatch):
    file_entry, block = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    window._on_translation_edited(0, "BB[end]")
    assert file_entry.dirty
    # Another program changed the second string, and the padding after both.
    changed = bytes.fromhex("41 42 00 41 41 00") + b"\x00" * 20
    Path(file_entry.path).write_bytes(changed)
    _changed(window, file_entry.path)
    assert asked == [(["rom.bin"], ["rom.bin"])]
    assert file_entry.doc.data == bytes.fromhex("42 42 00 41 41 00") + b"\x00" * 20
    assert file_entry.doc.base == changed
    assert [r.current_text() for r in block.doc.strings] == ["BB[end]", "AA[end]"]
    assert block.doc.strings[0].original == "AB[end]"
    assert file_entry.dirty
    assert window.statusBar().currentMessage() == "Reloaded rom.bin, 1 edited byte kept"
    # Written, the file holds both.
    assert window._write_all()
    assert Path(file_entry.path).read_bytes() == file_entry.doc.data


def test_an_edit_wins_where_the_disk_changed_the_same_byte_and_says_so(
    window, tmp_path, monkeypatch
):
    """The edit is the bytes it changed: the first letter here. The second,
    which the edit left as it was, is the disk's to change."""
    file_entry, _ = _block(window, tmp_path)
    _answer(window, monkeypatch, True)
    window._on_translation_edited(0, "BB[end]")
    Path(file_entry.path).write_bytes(bytes.fromhex("43 43 00 42 41 00") + b"\xff" * 20)
    _changed(window, file_entry.path)
    assert file_entry.doc.data[:3] == bytes.fromhex("42 43 00")
    assert window.statusBar().currentMessage() == (
        "Reloaded rom.bin, 1 edited byte kept (1 changed on disk too)"
    )


def test_an_edit_the_file_shrank_under_is_dropped_and_said(
    window, tmp_path, monkeypatch
):
    file_entry, _ = _block(window, tmp_path)
    _answer(window, monkeypatch, True)
    window.overtype_bytes(24, b"\x01\x02")
    Path(file_entry.path).write_bytes(DATA[:25])
    _changed(window, file_entry.path)
    assert file_entry.doc.data == DATA[:24] + b"\x01"
    assert window.statusBar().currentMessage() == (
        "Reloaded rom.bin, 1 edited byte kept (1 past the new end of the file dropped)"
    )


def test_a_declined_reload_is_not_asked_again_until_the_next_change(
    window, tmp_path, monkeypatch
):
    file_entry, block = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, False)
    changed = bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20
    Path(file_entry.path).write_bytes(changed)
    _changed(window, file_entry.path)
    _changed(window, file_entry.path)
    assert len(asked) == 1
    assert file_entry.doc.data == DATA
    assert block.doc.strings[0].current_text() == "AB[end]"
    Path(file_entry.path).write_bytes(changed[:-1] + b"\x00")
    _changed(window, file_entry.path)
    assert len(asked) == 2
    # The menu row is the way back to what was declined.
    window._reload_current_file()
    assert file_entry.doc.data == changed[:-1] + b"\x00"


def test_reload_from_disk_reads_the_file_on_screen(window, tmp_path, monkeypatch):
    from window_helpers import menu_actions

    row = dict(menu_actions(window))["Reload from Disk"]
    assert not row.isEnabled()
    file_entry, block = _block(window, tmp_path)
    assert row.isEnabled()
    asked = _answer(window, monkeypatch, True)
    window._reload_current_file()
    assert window.statusBar().currentMessage() == "rom.bin is up to date"
    window._on_translation_edited(0, "BB[end]")
    Path(file_entry.path).write_bytes(bytes.fromhex("41 42 00 41 41 00") + b"\xff" * 20)
    row.trigger()
    assert not asked
    assert [r.current_text() for r in block.doc.strings] == ["BB[end]", "AA[end]"]
    assert window.statusBar().currentMessage() == "Reloaded rom.bin, 1 edited byte kept"
    window._new_project()
    assert not row.isEnabled()
    window._reload_current_file()
    assert window.statusBar().currentMessage() == "Nothing to reload"


def test_a_file_not_on_screen_is_reloaded_too(window, tmp_path, monkeypatch):
    first, block = _block(window, tmp_path)
    other = tmp_path / "other.bin"
    other.write_bytes(DATA)
    other_entry = window.open_rom(str(other))
    window._activate_entry(block)
    _answer(window, monkeypatch, True)
    changed = bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20
    other.write_bytes(changed)
    _changed(window, str(other))
    assert other_entry.doc.data == changed
    assert window._entry is block and window._doc is block.doc


def _packed_block(window, tmp_path):
    payload = b"HELLO HELLO HELLO\x00WORLD WORLD\x00" * 3
    _packed, slot, data = gba_packed_rom(payload, spare=9, tail=24)
    file_entry = open_rom_and_table(window, tmp_path, data, table=ASCII_TABLE)
    block = add_block(
        window,
        file_entry,
        "Z",
        RangeSource(0, len(payload)),
        fill=b"\xff",
        compression_id="gba_lz77",
        slot_offset=16,
        slot_length=slot,
    )
    return file_entry, block, slot


def _repacked(other: bytes, slot: int) -> bytes:
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    packed = GbaLz77().compress(other, PipelineContext())
    return b"\xff" * 16 + packed + b"\xff" * (slot - len(packed) + 24)


def test_a_compressed_block_without_edits_reads_the_new_bytes(
    window, tmp_path, monkeypatch
):
    file_entry, block, slot = _packed_block(window, tmp_path)
    _answer(window, monkeypatch, True)
    Path(file_entry.path).write_bytes(
        _repacked(b"HI HI HI HI HI HI\x00WORLD WORLD\x00" * 3, slot)
    )
    _changed(window, file_entry.path)
    assert window._entry is block
    assert block.doc.strings[0].current_text() == "HI HI HI HI HI HI[end]"


def test_a_compressed_block_with_edits_keeps_them(window, tmp_path, monkeypatch):
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

    file_entry, block, slot = _packed_block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    window._on_translation_edited(0, "HI HI HI[end]")
    Path(file_entry.path).write_bytes(
        _repacked(b"YO YO YO YO YO YO\x00WORLD WORLD\x00" * 3, slot)
    )
    _changed(window, file_entry.path)
    assert asked == [(["rom.bin"], ["rom.bin"])]
    assert block.dirty and block.doc.strings[0].current_text() == "HI HI HI[end]"
    # Written, the slot holds the edit over the new file.
    assert window._write_blocks([block])
    written = Path(file_entry.path).read_bytes()
    out = GbaLz77().decompress(written[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00")
