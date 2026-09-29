"""The probe client against the fake emulator's probe server: its life (quit,
kill, restart), probes that get no answer, and what the answers say."""

from __future__ import annotations

import time

import pytest

from capture_fake import (
    BUFFER,
    CONSOLE,
    READER_PC,
    STRINGS,
    TABLE,
    WRITER_PC,
    FakeEmulator,
    Game,
    Memory,
    build_rom,
    write_moment,
)
from mapchar.capture.probe import ProbeServer, ReadSub, changes
from mapchar.capture.protocol import WAIT, CaptureError, drive

OBS = ("snesWorkRam", 0x2000, 0x201E)


@pytest.fixture
def folder(tmp_path):
    f = tmp_path / "cap"
    write_moment(str(f))
    return str(f)


def server(folder, **kw) -> tuple[ProbeServer, FakeEmulator]:
    emu = FakeEmulator(Game(build_rom(), 1), **kw)
    p = ProbeServer(emu, "rom.bin", CONSOLE, folder, obs=OBS, obs_from=3, obs_to=3)
    drive(p.start(), 30)
    return p, emu


START = STRINGS + len("Hello there.") + 1  # message 1's first byte


def test_a_probe_and_its_answer(folder):
    p, _ = server(folder)
    ref, got = drive(p.effect([(START, 0x00)], 3), 30)
    assert got[0] == 0x00 and got[1:] == ref[1:]
    assert p.answer.count == len(got)
    assert drive(p.test([(0x800, 0x00)], 3), 30) is False
    p.close()


def test_close_quits_an_idle_server_and_kills_a_busy_one(folder):
    p, emu = server(folder)
    p.close()
    assert not emu.procs[0].killed  # it quit when asked
    p, emu = server(folder, fault="silent")
    step = p.run([(START, 0)], 3)
    assert next(step) is WAIT and p.awaiting
    t = time.monotonic()
    p.close()  # mid-request: no quit is waited for
    assert emu.procs[0].killed and time.monotonic() - t < 2


def test_close_with_no_wait_kills_at_once(folder):
    p, emu = server(folder)
    p.close(wait=0)
    assert emu.procs[0].killed and not p.alive()


def test_a_server_that_never_answers_is_restarted(folder, monkeypatch):
    p, emu = server(folder, fault="silent")
    monkeypatch.setattr("mapchar.capture.probe.PROBE_SECONDS", 0.3)
    monkeypatch.setattr("mapchar.capture.probe.FRAMES_PER_SECOND", 10**9)
    assert drive(p.run([(START, 0)], 3), 30) is None
    assert p.no_answer == 1 and emu.runs == 2
    assert drive(p.test([(START, 0)], 3), 30) is None  # never "no change"
    p.close(0)


def test_a_server_closing_mid_line_is_no_answer_then_goes_on(folder):
    p, emu = server(folder, fault="cut")
    assert drive(p.run([(START, 0)], 3), 30) is None
    assert p.no_answer == 1 and emu.runs == 2
    assert drive(p.effect([(START, 0)], 3), 30) is not None
    p.close()


def test_a_server_that_died_between_requests_is_asked_again(folder):
    p, emu = server(folder, fault="die")
    path = drive(p.shot(5, [(START, 0)], "shot.png"), 30)
    assert path.endswith("shot.png") and emu.runs == 2
    p.close()


def test_a_probe_to_a_server_already_dead_is_asked_again(folder):
    p, emu = server(folder, fault="die")
    emu.procs[0].thread.join(5)  # it ended after its reference
    assert drive(p.test([(START, 0)], 3), 30) is True  # asked of a fresh server
    assert p.no_answer == 0 and emu.runs == 2
    p.close()


def test_the_control_is_checked_on_every_restart(folder):
    p, emu = server(folder, fault="cut")
    p.control = ([(0x800, 0x00)], 3)  # changes nothing: ROM writes "lost"
    with pytest.raises(CaptureError, match="not applying ROM changes"):
        drive(p.run([(START, 0)], 3), 30)
    p.close(0)


def test_stale_answers_are_passed_over(folder):
    p, _ = server(folder)
    p.conn.buf += b"res 99 same\nsettled 4\n"
    assert drive(p.run([(START, 0)], 3), 30).same is False
    p.close()


def test_what_an_answer_carries(folder):
    p, _ = server(folder)
    ans = p._parse(
        "res 1 diff 2 5 00.01 n=900 v=3,16,40 d=10:1 s=77 r=85A5D9/2A5D9,40".split(), 1
    )
    assert ans.values == [0, 1] and ans.count == 900
    assert ans.vram == (3, 16, 40, {0x10: 1}) and ans.settled == 77
    assert ans.reads == [0x85A5D9, 0x40] and ans.offsets == [0x2A5D9, 0x40]
    with pytest.raises(CaptureError, match="ROM bytes be changed"):
        p._parse("res 1 same w=0".split(), 1)
    p.close()


def test_changes_are_spelled_for_the_server():
    assert changes([(0x200, 0x41)]) == "200:41"
    subs = [
        ReadSub("snesPrgRom", 0x200, 0x41),
        ReadSub("snesPrgRom", 0x200, 0x41, pc=READER_PC, nth=2),
        ReadSub("snesWorkRam", 0x10, 1, nth=3),
        ReadSub("snesWorkRam", 0x10, 1, after=1),
    ]
    assert changes([], subs) == (
        "r:snesPrgRom:200:41,r:snesPrgRom:200:41:9000:2,"
        "r:snesWorkRam:10:1::3,r:snesWorkRam:10:1:::1"
    )


def test_a_read_substitute_changes_only_the_read_asked(folder):
    p, _ = server(folder)
    sub = ReadSub(CONSOLE.rom, START + 2, 0x00, pc=READER_PC, nth=1)
    ref, got = drive(p.effect([], 3, [sub]), 30)
    assert got[2] == 0 and got[:2] == ref[:2] and got[3:] == ref[3:]
    for other in (
        ReadSub(CONSOLE.rom, START + 2, 0x00, pc=READER_PC, nth=2),  # no 2nd read
        ReadSub(CONSOLE.rom, START + 2, 0x00, pc=0x1234),  # nor by that PC
    ):
        assert drive(p.effect([], 3, [other]), 30) is None
    p.close()


class TwiceGame(Game):
    """Writes a RAM cell twice and copies it after each write."""

    def run(self, rom=None, subs=()):
        m = Memory(self.rom if rom is None else rom, subs)
        ev = []
        for k, c in enumerate((0x41, 0x42)):
            m.write(0x1000, c)
            ev.append(("W", 0x9030, 0x7E1000, c, 3))
            ev.append(
                ("W", WRITER_PC, BUFFER + k, m.read(0x9040, 0x1000, "snesWorkRam"), 3)
            )
        return ev


def test_a_substitute_after_the_nth_write(folder):
    emu = FakeEmulator(TwiceGame(build_rom()))
    p = ProbeServer(
        emu, "rom.bin", CONSOLE, folder, obs=("snesWorkRam", 0x2000, 0x2001)
    )
    drive(p.start(), 30)
    ref, got = drive(
        p.effect([], 3, [ReadSub("snesWorkRam", 0x1000, 0x7, after=2)]), 30
    )
    assert ref == [0x41, 0x42] and got == [0x41, 0x07]
    p.close()


class HalfwordGame(Game):
    """Reads its string a halfword at a time, as a GBA's LDRH does."""

    def text(self, m, at):
        ev = []
        for k in range(0, 32, 2):
            v = m.read_access(READER_PC, at + k, 2)
            ev.append(("E", READER_PC, at + k, v, 3))
            ev.append(("W", WRITER_PC, BUFFER + k, v & 0xFF, 3))
            ev.append(("W", WRITER_PC, BUFFER + k + 1, v >> 8, 3))
        return ev


def test_a_substitute_changes_its_lane_of_a_wider_read(folder):
    rom = bytearray(build_rom())
    rom[TABLE + 2 : TABLE + 4] = (0x300).to_bytes(2, "little")  # an even string
    rom[0x300:0x320] = bytes(range(0x40, 0x60))
    emu = FakeEmulator(HalfwordGame(bytes(rom), 1))
    p = ProbeServer(
        emu, "rom.bin", CONSOLE, folder, obs=("snesWorkRam", 0x2000, 0x201F)
    )
    drive(p.start(), 30)
    # Byte $305 is the high lane of the halfword read at $304: that read,
    # the first from its word's start up to it, is the one changed.
    ref, got = drive(
        p.effect([], 3, [ReadSub("snesPrgRom", 0x305, 0x00, READER_PC, 1)]), 30
    )
    assert got[5] == 0 and got[:5] == ref[:5] and got[6:] == ref[6:]
    p.close()
