"""The replay's process and log: killed however the wait ends, errors found
however the log arrives, and every log line form read."""

from __future__ import annotations

import os
import threading
import time

import pytest

from capture_fake import (
    BUFFER,
    CONSOLE,
    FakeEmulator,
    FakeProc,
    Game,
    build_rom,
    write_moment,
)
from mapchar.capture import consoles, evidence
from mapchar.capture.protocol import WAIT, CaptureError, drive, wait_process


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "cap"
    write_moment(str(f))
    return str(f)


# -- the replay's process


def test_a_replay_stopped_midway_kills_its_emulator(folder):
    emu = FakeEmulator(Game(build_rom()), replay_hang=True)
    step = evidence.replay(emu, "rom.bin", CONSOLE, folder)
    for _ in range(20):
        assert next(step) is WAIT
    (proc,) = emu.procs
    assert proc.poll() is None
    step.close()  # Stop, Skip or the app closing
    assert proc.killed and proc.poll() is not None


def test_a_replay_past_its_deadline_is_killed_and_said(folder):
    emu = FakeEmulator(Game(build_rom()), replay_hang=True)
    with pytest.raises(CaptureError, match="in time"):
        drive(evidence.replay(emu, "rom.bin", CONSOLE, folder, seconds=0.3), 10)
    assert emu.procs[0].killed


def test_an_error_marker_split_across_two_reads_ends_the_wait(tmp_path):
    log = tmp_path / "out.txt"
    log.write_bytes(b"starting\n!")
    gate = threading.Event()
    proc = FakeProc(lambda p: p.stopped.wait())
    step = evidence.watch(proc, str(log), time.monotonic() + 30)
    for _ in range(3):
        next(step)

    def finish():
        gate.wait()
        with open(log, "ab") as fh:
            fh.write(b" script error: boom\n")

    threading.Thread(target=finish, daemon=True).start()
    gate.set()
    out = drive(step, 10)
    assert "! script error: boom" in out and proc.killed


def test_wait_process_leaves_the_process_to_its_caller():
    proc = FakeProc(lambda p: p.stopped.wait())
    assert drive(wait_process(proc, time.monotonic() + 0.05)) is None
    assert proc.poll() is None
    proc.kill()
    assert drive(wait_process(proc, time.monotonic() + 1)) == -9


# -- the log


def write_log(folder: str, text: str) -> None:
    with open(os.path.join(folder, evidence.EVIDENCE), "w") as fh:
        fh.write(text)


def test_a_log_cut_in_the_middle_of_a_line_is_read(folder):
    write_log(folder, "F 1\nE 9000 200 41\nW 9010 7E2000 41\nE 9000 2")
    ev = drive(evidence.Evidence.load(folder, CONSOLE, 0))
    assert ev.events == [("E", 0x200, 0x41, 1, 0x9000), ("W", BUFFER, 0x41, 1, 0x9010)]
    write_log(folder, "F 1\nE 9000\nW zz 1 2\nE 9000 201 42\n")
    ev = drive(evidence.Evidence.load(folder, CONSOLE, 0))
    assert ev.events == [("E", 0x201, 0x42, 1, 0x9000)]


def test_reads_carry_the_emulators_offset_and_writes_one_spelling(folder):
    lorom = consoles.SNES_LOROM
    write_log(
        folder,
        "F 5\nE 8000 85A5D9 41 2A5D9\nE 8000 05A5DA 42\n"
        "W 8000 1234 1\nW 8000 801234 2\nW 8000 7E1234 3\n"
        "W 2008000 3010 4 snesWorkRam:10\n",
    )
    ev = drive(evidence.Evidence.load(folder, lorom, 0))
    assert [e[1] for e in ev.events] == [
        0x2A5D9,
        0x2A5DA,
        0x7E1234,
        0x7E1234,
        0x7E1234,
        0x7E0010,
    ]
    assert ev.bus_of == {0x2A5D9: 0x85A5D9, 0x2A5DA: 0x05A5DA}
    assert all(e[3] == 5 for e in ev.events)


def test_the_index_finds_reads_by_byte_and_by_pc(folder):
    evs = [
        ("E", 0x10, 1, 1, 0xA),
        ("W", 0x7E0000, 1, 1, 0xB),
        ("E", 0x11, 2, 2, 0xA),
        ("E", 0x10, 1, 3, 0xC),
    ]
    ev = evidence.Evidence.of(folder, CONSOLE, evs)
    drive(ev.index())
    assert ev.reads_at == {0x10: [0, 3], 0x11: [2]}
    assert ev.reads_by == {0xA: [0, 2], 0xC: [3]}
    assert ev.frames == [1, 1, 2, 3]
    assert ev.first_read(0x10) == 0 and ev.first_read(0x10, 0xC) == 3
    assert ev.first_read(0x99) is None


def test_touched_with_and_without_the_access_file(folder):
    ev = evidence.Evidence.of(folder, CONSOLE, [])
    assert ev.touched(5)  # unknown: every byte may have been read
    with open(os.path.join(folder, evidence.EVIDENCE + ".acc"), "wb") as fh:
        fh.write(b"\0\1")
    ev = evidence.Evidence.of(folder, CONSOLE, [])
    assert ev.touched(1) and not ev.touched(0) and not ev.touched(7)


def test_pages_the_replay_dropped_are_read(folder):
    write_log(
        folder,
        "F 1\nW 1 2000010 5\nF 2\nB 20000\nF 3\nF 4\nU 20000\nF 5\nB 20001\n",
    )
    ev = drive(evidence.Evidence.load(folder, consoles.GBA, 0))
    assert ev.bulk == [(0x20000, 2, 4), (0x20001, 5, None)]
    assert [e[1] for e in ev.events] == [0x2000010]
