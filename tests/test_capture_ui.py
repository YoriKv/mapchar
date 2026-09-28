"""The capture window, the Captures dock and its Review tab, on a live
MainWindow with the scripted fake emulator of :mod:`capture_fake`."""

from __future__ import annotations

import time

from capture_fake import CONSOLE, END, LETTERS, FakeEmulator, Game, build_rom
from mapchar.capture.consoles import SNES_LOROM
from mapchar.capture.proposals import BLOCK, ENTRIES
from mapchar.capture.session import BUSY, DONE, FAILED, Session
from mapchar.capture.setup import ROM, Setup, rom_font
from mapchar.core.block import EndToken, PointerTableSource
from mapchar.project.entry import EntryKind
from mapchar.ui.capture import CaptureSetupWindow, finding_html
from test_capture import arrive
from window_helpers import open_rom_and_table


def start(window, tmp_path, message=1):
    game = Game(build_rom(), message)
    entry = open_rom_and_table(window, tmp_path, game.rom, rom_name="game.bin")
    s = Session(
        str(tmp_path / "game.bin.capture"),
        entry.path,
        CONSOLE,
        FakeEmulator(game),
        on_change=window._capture_changed,
    )
    window._game_capture, window._capture_file = s, entry
    window._capture_states = {}
    window.captures_dock.show()
    return s


def trace_one(window, qtbot, s, text):
    cap = arrive(s, f"{len(s.captures) + 1:04d}")
    window._capture_tick()
    win = window._capture_windows[cap.id]
    win.text.setText(text)
    win.capture_button.click()
    assert cap.text == text
    end = time.monotonic() + 60
    while cap.state != DONE:
        window._capture_tick()
        qtbot.wait(1)
        assert cap.state != FAILED, cap.reason
        assert time.monotonic() < end
    return cap


def test_a_capture_is_typed_traced_and_listed(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = trace_one(window, qtbot, s, "Welcome to the Island of Tests!")
    window._capture_refresh()
    items = window.captures_panel.list
    assert items.count() == 1
    assert "Done" in items.item(0).text() and cap.text in items.item(0).text()


def test_tracing_shows_its_progress(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = arrive(s)
    window._capture_tick()
    assert window.captures_panel.progress.isHidden()
    s.submit(cap, "Welcome to the Island of Tests!")
    panel, counted = window.captures_panel, []
    end = time.monotonic() + 60
    while cap.state != DONE:
        window._capture_tick()
        window._capture_refresh()
        if cap.state == "tracing" and panel.progress.maximum():
            counted.append((panel.progress.value(), panel.progress.maximum()))
            assert cap.status in panel.work.text()
        assert panel.progress.isHidden() is (cap.state not in BUSY)
        qtbot.wait(1)
        assert cap.state != FAILED, cap.reason
        assert time.monotonic() < end
    assert counted and all(0 <= v < m for v, m in counted)
    window._capture_refresh()
    assert panel.progress.isHidden()


def test_a_short_text_is_answered_in_the_window(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    cap = arrive(s)
    window._capture_tick()
    win = window._capture_windows[cap.id]
    win.text.setText("Wel")
    win.capture_button.click()
    assert not cap.text
    assert "more of the line" in win.message.text()


def test_review_proposes_and_accepting_adds_them(window, qtbot, tmp_path):
    s = start(window, tmp_path)
    trace_one(window, qtbot, s, "Welcome to the Island of Tests!")
    s.emulator.game.message = 2  # the player moved on to the next message
    trace_one(window, qtbot, s, "Every string ends here.")
    window._capture_review()
    props = {p.kind: p for p in window._capture_proposals}
    block = props[BLOCK]
    assert isinstance(block.config.source, PointerTableSource)
    assert isinstance(block.config.string_type, EndToken)
    window._capture_accept(props[ENTRIES].id)
    window._capture_accept(block.id)
    blocks = [e for e in window.workspace.entries if e.kind is EntryKind.BLOCK]
    assert [b.name for b in blocks] == [block.name]
    table = window.workspace.entry_for_table("main").table
    assert table.entries[f"{END:08b}"].kind.value == "end"
    assert table.entries[f"{LETTERS['W']:08b}"].text == "W"
    assert window._capture_states[block.id] == "accepted"
    window.undo_stack.undo()
    assert not [e for e in window.workspace.entries if e.kind is EntryKind.BLOCK]


def test_a_word_that_does_not_fit_is_underlined():
    from mapchar.capture.occurrence import Finding

    f = Finding("no-match", "These words do not fit.", ["Hello", "wrold"], ["Hello"])
    out = finding_html("Hello wrold", f)
    assert (
        "<u" in out and "wrold</u>" in out and "<u style='color:#d33'>Hello" not in out
    )


# -- the setup window


def test_the_setup_window_reads_a_font_in_rom_or_ram(qtbot):
    given = Setup(rom_font(0x70000, 0x71FFF, 0x100000))
    win = CaptureSetupWindow(given, SNES_LOROM, 0x100000, 0x200)
    qtbot.addWidget(win)
    # Shown as mapchar's offsets, past the copier header.
    assert win.font_box.isChecked() and win.memory.value() == ROM
    assert (win.rom_start.value(), win.rom_end.value()) == (0x70200, 0x721FF)
    assert win.read() == given
    win.memory.button("ram").click()
    assert win.pages.currentIndex() == 1
    win.ram_start.setText("7F1200")
    win.ram_end.setText("7F13FF")
    f = win.read().font
    assert (f.memory, f.start, f.end) == ("snesWorkRam", 0x11200, 0x113FF)
    win.ram_end.setText("7F11FF")
    win.play_button.click()
    assert "before its start" in win.message.text() and win.result() == 0
    win.font_box.setChecked(False)
    assert win.read() == Setup()


def test_play_opens_the_setup_first(window, qtbot, tmp_path, monkeypatch):
    s = start(window, tmp_path)

    def accept(dialog):
        dialog.font_box.setChecked(True)
        dialog.rom_start.set_value(0x200)
        dialog.rom_end.set_value(0x2FF)
        dialog.play_button.click()
        return dialog.result()

    monkeypatch.setattr(CaptureSetupWindow, "exec", accept)
    window._capture_timer.stop()  # as it stops itself while the setup is open
    window._play_in_emulator()
    assert s.setup.font == rom_font(0x200, 0x2FF, len(s.rom))
    assert s.emulator.launched == ["recorder"]
    assert window._capture_timer.isActive()  # the recorder is listened to
    s.stop_playing()
    monkeypatch.setattr(CaptureSetupWindow, "exec", lambda dialog: 0)
    window._play_in_emulator()
    assert s.emulator.launched == ["recorder"]  # cancelled: nothing launched
