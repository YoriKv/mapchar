"""A pointer table's strings shown through a capture's own slot, on the fake
emulator of :mod:`capture_fake`."""

from __future__ import annotations

import os
import time
from dataclasses import replace

import pytest

from capture_fake import CONSOLE, MESSAGES, TABLE, FakeEmulator, Game, build_rom
from mapchar.capture.combine import combine
from mapchar.capture.proposals import BLOCK, propose
from mapchar.capture.protocol import WAIT, CaptureError
from mapchar.capture.tablesweep import (
    Slots,
    own_slot,
    pointer_frame,
    sweep,
    table_of,
)
from mapchar.capture.trace import Pointer, Result
from mapchar.core.block import BlockConfig, PointerTableSource, RangeSource
from test_capture import MAPPINGS, sightings


def run(step):
    """Drive a sweep to its end: its shots, and what it returns."""
    shots = []
    end = time.monotonic() + 60
    while True:
        try:
            y = next(step)
        except StopIteration as stop:
            return shots, stop.value
        if isinstance(y, tuple):
            shots.append(y)
        elif y is WAIT:
            time.sleep(0.002)
        assert time.monotonic() < end


def test_a_tables_slots():
    t = Slots(0x100, 0x108, 2, 2)
    assert t.count == 4 and t.slot(3) == 0x106
    assert Slots(0x100, 0x101, 2, 2).count == 0
    assert Slots(0x100, 0x10A, 3, 4).count == 2
    cfg = BlockConfig(PointerTableSource(0x110, 0x118, 2, 2))
    assert table_of(cfg, 0x10) == t
    assert table_of(BlockConfig(RangeSource(0, 16)), 0) is None
    assert table_of(None, 0) is None


def test_the_captures_own_slot():
    t = Slots(0x100, 0x108, 2, 2)
    r = Result(pointers=[Pointer(0x103, 512, "read before")])
    assert own_slot(r, t) == 0x102
    assert own_slot(Result(pointers=[Pointer(0x200, 2, "")]), t) is None
    assert own_slot(None, t) is None


@pytest.fixture
def traced(tmp_path):
    ss = sightings(tmp_path, [(1, MESSAGES[1]), (2, MESSAGES[2])])
    block = next(
        p
        for p in propose(combine(ss, build_rom(), CONSOLE, MAPPINGS), "t")
        if p.kind == BLOCK
    )
    return str(tmp_path / "c0"), ss[0].result, table_of(block.config, 0)


def test_every_string_is_shown_through_the_captures_slot(traced):
    folder, r, table = traced
    game = Game(build_rom(), 1)
    assert table == Slots(TABLE, TABLE + 2 * len(MESSAGES), 2, 2)
    assert own_slot(r, table) == TABLE + 2
    step = pointer_frame(folder, CONSOLE, TABLE + 2, 2, r)
    assert run(step)[1] == 3  # the frame the game read its pointer
    # Settling past the probe server's second state: the shot still starts
    # from the state before the pointer was read.
    r = replace(r, settled=11)
    shots, end = run(
        sweep(FakeEmulator(game), "rom", game.rom, CONSOLE, folder, r, table)
    )
    assert end == len(MESSAGES) and [i for i, _ in shots] == [0, 1, 2, 3]
    rom = game.rom
    for i, path in shots:
        with open(path, "rb") as fh:
            said = fh.read().decode("latin-1")
        a, b = TABLE + 2 * i, TABLE + 2 * i + 1
        ws = f"{TABLE + 2:X}:{rom[a]:X},{TABLE + 3:X}:{rom[b]:X}"
        assert said == f"\x89PNGshot 1 11 sweep_{i:04d}.png {ws}"


def test_a_sweep_shows_a_run_of_strings_at_a_time(traced):
    folder, r, table = traced
    game = Game(build_rom(), 1)
    emu = FakeEmulator(game)
    shots, end = run(sweep(emu, "rom", game.rom, CONSOLE, folder, r, table, 1, 2))
    assert [i for i, _ in shots] == [1, 2] and end == 3


def test_a_sweep_closed_early_ends_its_emulator(traced):
    folder, r, table = traced
    game = Game(build_rom(), 1)
    step = sweep(FakeEmulator(game), "rom", game.rom, CONSOLE, folder, r, table)
    end = time.monotonic() + 30
    while not isinstance(next(step), tuple):
        time.sleep(0.002)
        assert time.monotonic() < end
    step.close()
    with pytest.raises(StopIteration):
        next(step)


def test_a_capture_not_read_through_the_table_cannot_show_it(traced):
    folder, _, table = traced
    game = Game(build_rom(), 1)
    step = sweep(FakeEmulator(game), "rom", game.rom, CONSOLE, folder, Result(), table)
    with pytest.raises(CaptureError):
        next(step)


# -- review findings


def test_a_run_never_starts_after_the_slot_is_read_even_after_a_restart(traced):
    from mapchar.capture.probe import ProbeServer

    folder, _, _ = traced
    p = ProbeServer(FakeEmulator(Game(build_rom(), 1)), "rom", CONSOLE, folder)
    p.states = [0, 10]
    p.max_state_frame = 3
    assert p.state_for(10) == 1
    p.states = [0, 10]  # as start() sets them again on a restart
    assert p.state_for(10) == 1
    p.max_state_frame = None
    assert p.state_for(10) == 2


def test_a_slot_read_before_every_state_cannot_be_shown(traced, monkeypatch):
    from mapchar.capture import tablesweep

    folder, r, table = traced
    game = Game(build_rom(), 1)

    def before_everything(*a, **k):
        return -5
        yield

    monkeypatch.setattr(tablesweep, "pointer_frame", before_everything)
    step = sweep(FakeEmulator(game), "rom", game.rom, CONSOLE, folder, r, table)
    with pytest.raises(CaptureError, match="no state"):
        run(step)


def test_the_slots_read_is_found_as_the_evidence_reads_it(tmp_path):
    from capture_fake import write_moment
    from mapchar.capture.evidence import EVIDENCE

    folder = str(tmp_path / "cap")
    write_moment(folder)
    with open(os.path.join(folder, EVIDENCE), "w") as fh:
        fh.write(
            "F 2\nE 8200 FFFFFF 0\nE zz\n"  # no offset for it; cut short
            "F 3\nE 8200 ABC102 0 102\nE 8200 ABC103 2 103\n"
            "E 9000 ABC200 48 200\nF 4\nE 8200 ABC102 0 102\n"
        )

    def to_rom(bus):
        if bus == 0xFFFFFF:
            raise ValueError("not in the ROM")
        return bus & 0xFFF

    console = replace(CONSOLE, to_rom=to_rom)
    r = Result(first_read=0x200)
    assert run(pointer_frame(folder, console, 0x102, 2, r))[1] == 3
