"""A ROM changed on disk by another program: noticed, offered, and reloaded
with the edits made here kept."""

from __future__ import annotations

from pathlib import Path

from mapchar.core.block import RangeSource
from mapchar.pipeline.filechange import edit_runs, replay
from window_helpers import ASCII_TABLE, add_block, gba_packed_rom, open_rom_and_table

DATA = bytes.fromhex("41 42 00 42 41 00") + b"\xff" * 20


def _block(window, tmp_path):
    file_entry = open_rom_and_table(window, tmp_path, DATA)
    return file_entry, add_block(window, file_entry, "b", RangeSource(0, 6))


def _answer(window, monkeypatch, yes: bool):
    """Record what the window asks, answering ``yes``."""
    asked: list[str] = []
    monkeypatch.setattr(
        type(window), "_ask", lambda self, title, text: (asked.append(text), yes)[1]
    )
    return asked


def _changed(window, path: str) -> None:
    """What the watcher's signal and its rest timer do, without the wait."""
    window._on_file_changed(path)
    window._check_changed_files()


# --- the runs ---------------------------------------------------------------


def test_edit_runs_are_the_differences_and_replay_lays_them_over():
    before = bytes(range(16))
    after = bytearray(before)
    after[2:4] = b"\xaa\xbb"
    after[9] = 0xCC
    runs = edit_runs(before, bytes(after))
    assert runs == ((2, b"\xaa\xbb"), (9, b"\xcc"))
    base = bytes(16)
    assert replay(runs, base) == (
        b"\x00\x00\xaa\xbb" + b"\x00" * 5 + b"\xcc" + b"\x00" * 6
    )
    assert edit_runs(before, before) == () and replay((), base) is base


def test_edit_runs_cross_chunks_and_carry_a_longer_tail():
    before = bytes(10000)
    after = bytearray(before)
    after[4090:4100] = b"\x01" * 10
    assert edit_runs(before, bytes(after)) == ((4090, b"\x01" * 10),)
    # A run still open at the end takes the tail with it, as one splice.
    longer = bytes(after) + b"\x02\x02"
    after[9999] = 0x01
    assert edit_runs(before, bytes(after) + b"\x02\x02")[-1] == (9999, b"\x01\x02\x02")
    assert edit_runs(before, longer)[-1] == (10000, b"\x02\x02")
    assert replay(((12, b"\xaa"),), b"\x00" * 4) == b"\x00" * 4 + b"\xff" * 8 + b"\xaa"


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
    assert asked == ["rom.bin changed on disk. Reload it?"]


# --- reloading --------------------------------------------------------------


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
    assert window.statusBar().currentMessage() == "Reloaded rom.bin"
    window._new_project()
    assert not row.isEnabled()
    window._reload_current_file()
    assert window.statusBar().currentMessage() == "Nothing to reload"


def test_a_changed_file_is_offered_and_reloaded(window, tmp_path, monkeypatch):
    file_entry, block = _block(window, tmp_path)
    asked = _answer(window, monkeypatch, True)
    changed = bytes.fromhex("42 41 00 41 42 00") + b"\xff" * 20
    Path(file_entry.path).write_bytes(changed)
    _changed(window, file_entry.path)
    assert asked == ["rom.bin changed on disk. Reload it?"]
    assert file_entry.doc.data == changed and file_entry.doc.raw == changed
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
    assert len(asked) == 1 and "edits here that are not written yet" in asked[0]
    assert file_entry.doc.data == bytes.fromhex("42 42 00 41 41 00") + b"\x00" * 20
    assert [r.current_text() for r in block.doc.strings] == ["BB[end]", "AA[end]"]
    assert block.doc.strings[0].original == "AB[end]"
    assert file_entry.dirty
    # Written, the file holds both.
    assert window._write_all()
    assert Path(file_entry.path).read_bytes() == file_entry.doc.data


def test_an_edit_wins_byte_for_byte_where_the_disk_changed_the_same_bytes(
    window, tmp_path, monkeypatch
):
    """The edit is the bytes it changed: the first letter here. The second,
    which the edit left as it was, is the disk's to change."""
    file_entry, block = _block(window, tmp_path)
    _answer(window, monkeypatch, True)
    window._on_translation_edited(0, "BB[end]")
    Path(file_entry.path).write_bytes(bytes.fromhex("43 43 00 42 41 00") + b"\xff" * 20)
    _changed(window, file_entry.path)
    assert file_entry.doc.data[:3] == bytes.fromhex("42 43 00")


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


def test_a_file_not_on_screen_is_reloaded_too(window, tmp_path, monkeypatch):
    first, block = _block(window, tmp_path)
    second = window.open_rom(str(tmp_path / "rom.bin"))
    assert second is first
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


def test_a_compressed_block_without_edits_reads_the_new_bytes(
    window, tmp_path, monkeypatch
):
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

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
    _answer(window, monkeypatch, True)
    other = b"HI HI HI HI HI HI\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(other, PipelineContext())
    Path(file_entry.path).write_bytes(
        b"\xff" * 16 + packed + b"\xff" * (slot - len(packed) + 24)
    )
    _changed(window, file_entry.path)
    assert window._entry is block
    assert block.doc.strings[0].current_text() == "HI HI HI HI HI HI[end]"


def test_a_compressed_block_with_edits_keeps_them(window, tmp_path, monkeypatch):
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.builtins.compression import GbaLz77

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
    asked = _answer(window, monkeypatch, True)
    window._on_translation_edited(0, "HI HI HI[end]")
    other = b"YO YO YO YO YO YO\x00WORLD WORLD\x00" * 3
    packed = GbaLz77().compress(other, PipelineContext())
    Path(file_entry.path).write_bytes(
        b"\xff" * 16 + packed + b"\xff" * (slot - len(packed) + 24)
    )
    _changed(window, file_entry.path)
    assert len(asked) == 1 and "edits here" in asked[0]
    assert block.dirty and block.doc.strings[0].current_text() == "HI HI HI[end]"
    # Written, the slot holds the edit over the new file.
    assert window._write_blocks([block])
    written = Path(file_entry.path).read_bytes()
    out = GbaLz77().decompress(written[16:], PipelineContext())
    assert out.startswith(b"HI HI HI\x00")
