"""Console profiles, the Lua literals the scripts are built with, and the
Mesen bridge's switches and paths — no emulator needed."""

from __future__ import annotations

import re
import subprocess

import pytest

from mapchar.capture import consoles, emulator
from mapchar.capture.consoles import lua_value
from mapchar.capture.emulator import PROBE, RECORDER, REPLAY, Mesen, build_script
from mapchar.capture.protocol import CaptureError

# -- detection


def snes_header(size: int, at: int, mode: int, chip: int = 0) -> bytearray:
    rom = bytearray(size)
    rom[at + 0x15] = mode
    rom[at + 0x16] = chip
    rom[at + 0x1C : at + 0x20] = bytes([0xFF, 0xFF, 0x00, 0x00])
    return rom


def test_an_sfc_file_is_snes_whatever_its_byte_b2():
    rom = snes_header(0x80000, 0x7FC0, 0x20)
    rom[0xB2] = 0x96  # what the GBA's header holds there
    assert consoles.detect("game.sfc", bytes(rom)) is consoles.SNES_LOROM


def test_gba_needs_its_whole_fixed_header():
    gba = bytearray(0x200)
    gba[0xB2] = 0x96
    assert consoles.detect("x.bin", bytes(gba)) is None
    gba[4:12] = bytes.fromhex("24FFAE51699AA221")
    gba[0xBD] = (-(sum(gba[0xA0:0xBD]) + 0x19)) & 0xFF
    assert consoles.detect("x.bin", bytes(gba)) is consoles.GBA
    assert consoles.detect("x.gba", b"") is consoles.GBA


def test_sa1_exhirom_and_hirom_are_told_apart():
    sa1 = snes_header(0x100000, 0x7FC0, 0x23, 0x35)
    assert consoles.detect("kss.sfc", bytes(sa1)) is consoles.SNES_SA1
    hi = snes_header(0x100000, 0xFFC0, 0x21)
    assert consoles.detect("x.sfc", bytes(hi)) is consoles.SNES_HIROM
    ex = snes_header(0x600000, 0x40FFC0, 0x25)
    assert consoles.detect("x.sfc", bytes(ex)) is consoles.SNES_EXHIROM


# -- mappings


def test_exhirom_maps_both_halves():
    ex = consoles.SNES_EXHIROM
    assert ex.to_rom(0xC01234) == 0x001234
    assert ex.to_rom(0xFF8000) == 0x3F8000
    assert ex.to_rom(0x401234) == 0x401234
    assert ex.to_rom(0x3E8000) == 0x7E8000
    for off in (0x001234, 0x3F8000, 0x401234, 0x7E8000):
        assert all(ex.to_rom(b) == off for b in ex.to_bus(off))


def test_sa1_banks_above_80_are_the_upper_rom():
    sa1 = consoles.SNES_SA1
    assert sa1.to_rom(0x008000) == 0
    assert sa1.to_rom(0x3FFFFF) == 0x1FFFFF
    assert sa1.to_rom(0x808000) == 0x200000
    assert sa1.to_rom(0xBFFFFF) == 0x3FFFFF
    assert sa1.to_rom(0xC12345) == 0x012345
    for off in (0, 0x123456, 0x2ABCDE, 0x3FFFFF):
        assert all(sa1.to_rom(b) == off for b in sa1.to_bus(off))
    assert "sa1" in sa1.readers and "sa1InternalRam" in sa1.extra_rams


def test_nes_pointers_in_8k_windows():
    assert set(consoles.NES.to_bus(0x1234)) == {0x9234, 0xB234, 0xD234, 0xF234}
    assert set(consoles.NES.to_bus(0x2345)) == {0xA345, 0xE345, 0x8345, 0xC345}


def test_ram_outside_wram_is_refused_and_mirrors_fold():
    lorom = consoles.SNES_LOROM
    with pytest.raises(ValueError):
        lorom.ram_of(0x002118)  # a PPU register
    with pytest.raises(ValueError):
        lorom.ram_of(0x006000)
    assert lorom.ram_of(0x801234) == ("snesWorkRam", 0x1234)
    assert lorom.canon(0x001234) == lorom.canon(0x801234) == 0x7E1234
    assert lorom.ram_of(0x700010) == ("snesSaveRam", 0x10)
    assert lorom.canon(0xF00010) == 0x700010
    assert consoles.SNES_HIROM.ram_of(0x206010) == ("snesSaveRam", 0x10)
    assert consoles.NES.canon(0x0812) == consoles.NES.canon(0x1812) == 0x012
    assert consoles.NES.ram_of(0x6010) == ("nesSaveRam", 0x10)
    with pytest.raises(ValueError):
        consoles.NES.ram_of(0x2000)
    assert consoles.SNES_SA1.ram_of(0x003010) == ("sa1InternalRam", 0x10)
    assert consoles.SNES_SUPERFX.ram_of(0x701234) == ("gsuWorkRam", 0x1234)
    assert consoles.GBA.canon(0x02000010) == 0x02000010


def test_every_profile_says_where_its_writes_land():
    for c in consoles.CONSOLES.values():
        assert c.writes, c.id
        for _cpu, _mem, lo, hi, ram, _by_bus in c.writes:
            assert ram in c.rams + c.extra_rams, (c.id, ram)
            assert hi == -1 or hi >= lo


# -- the NES image


def test_an_nes_image_empty_or_nes2():
    empty = b"NES\x1a\x00\x01" + bytes(10) + bytes(0x2000)
    with pytest.raises(CaptureError):
        consoles.NES.image(empty)
    nes2 = bytearray(b"NES\x1a\x00\x00" + bytes(10) + bytes(0x100 * 0x4000))
    nes2[4], nes2[7], nes2[9] = 0x00, 0x08, 0x01  # 256 banks of 16 KiB
    assert len(consoles.NES.image(bytes(nes2))) == 0x400000


# -- Lua literals


def test_a_string_holding_the_closing_bracket_survives():
    for v in ("p", "a]]b", "a]==]b]=]c", "\nstarts with a newline", ""):
        lit = lua_value(v)
        m = re.fullmatch(r"\[(=*)\[\n(.*)\]\1\]", lit, re.S)
        assert m and m.group(2) == v, lit
        assert "]" + m.group(1) + "]" not in v


def test_keys_that_are_not_names_are_quoted():
    # "[ [[" with its space: "[[[" would open a long string.
    assert lua_value({"a b": 1, "end": 2, "ok": 3}) == (
        "{ [ [[\na b]] ] = 1, [ [[\nend]] ] = 2, ok = 3 }"
    )


def test_the_profile_reaches_the_scripts():
    text = build_script(PROBE, consoles.SNES_SA1, {"port": 5})
    assert "extraRams = { [[\nsa1InternalRam]]" in text
    assert "writes = { {" in text and "aliases = {  }" in text


# -- the Mesen bridge


def test_the_recorder_gets_the_long_script_timeout(tmp_path):
    exe = tmp_path / "mesen"
    exe.write_text("")
    m = Mesen(str(exe))
    rec = m.arguments("r.sfc", RECORDER, "s.lua")
    assert f"--debug.scriptWindow.scriptTimeout={emulator.SCRIPT_TIMEOUT}" in rec
    assert "--testRunner" not in rec
    rep = m.arguments("r.sfc", REPLAY, "s.lua")
    assert f"--timeout={emulator.HEADLESS_TIMEOUT}" in rep
    assert not any("scriptTimeout" in a for a in rep)
    assert emulator.HEADLESS_TIMEOUT < 3600
    assert f"--timeout={emulator.PROBE_TIMEOUT}" in m.arguments("r", PROBE, "s")


def wsl_mesen(monkeypatch, run):
    monkeypatch.setattr(emulator, "_in_wsl", lambda: True)
    monkeypatch.setattr(emulator.subprocess, "run", run)
    return Mesen("/mnt/c/Mesen/Mesen.exe")


def test_windows_paths_are_asked_once_per_folder(monkeypatch):
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "D:\\games\\\n", "")

    m = wsl_mesen(monkeypatch, run)
    assert m.native_path("/mnt/d/games/a.sfc") == "D:\\games\\a.sfc"
    assert m.native_path("/mnt/d/games/b.lua") == "D:\\games\\b.lua"
    assert len(calls) == 1 and calls[0][:2] == ["wslpath", "-w"]


def test_a_failing_wslpath_is_a_capture_error(monkeypatch):
    def run(args, **kw):
        return subprocess.CompletedProcess(args, 1, "", "wslpath: bad path")

    m = wsl_mesen(monkeypatch, run)
    with pytest.raises(CaptureError, match="bad path"):
        m.native_path("/nowhere/x.sfc")

    def missing(args, **kw):
        raise FileNotFoundError("wslpath")

    m = wsl_mesen(monkeypatch, missing)
    with pytest.raises(CaptureError):
        m.native_path("/nowhere/x.sfc")


def test_save_ram_offsets_repeat_at_its_size():
    hi = snes_header(0x100000, 0xFFC0, 0x21)
    hi[0xFFD8] = 3  # 8 KiB
    h = consoles.SNES_HIROM.for_rom(bytes(hi))
    assert h.sram_size == 0x2000
    assert h.locate(0x306000) == ("snesSaveRam", 0)  # bank $30: its mirror
    assert h.locate(0x206010) == ("snesSaveRam", 0x10)
    assert h.canon(0xB06010) == h.canon(0x206010)
    lo = snes_header(0x80000, 0x7FC0, 0x20)
    lo[0x7FD8] = 1  # 2 KiB
    lorom = consoles.SNES_LOROM.for_rom(bytes(lo))
    assert lorom.locate(0xFE346A) == ("snesSaveRam", 0x46A)
    # Never spelled as WRAM, sized or not.
    assert consoles.SNES_LOROM.canon(0xFE346A) != 0x7E346A
    assert lorom.canon(0xFE346A) == 0x70046A


def test_sa1_and_exhirom_pointer_forms_round_trip():
    for con in (consoles.SNES_SA1, consoles.SNES_EXHIROM):
        for off in range(0, 0x800000, 0x1234):
            for b in con.to_bus(off):
                assert con.to_rom(b) == off, (con.id, hex(off), hex(b))
    assert consoles.SNES_SA1.to_bus(0x401104) == []
    assert consoles.SNES_SA1.limits and consoles.SNES_EXHIROM.limits
