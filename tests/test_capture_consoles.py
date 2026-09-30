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
    assert consoles.SNES_SA1.limits and not consoles.SNES_EXHIROM.limits


# -- Game Boy, Master System, PC Engine


def test_gb_sms_and_pce_are_known_by_extension_and_magic():
    assert consoles.detect("x.gb", b"") is consoles.GB
    assert consoles.detect("x.gbc", b"") is consoles.GB
    assert consoles.detect("x.sms", b"") is consoles.SMS
    assert consoles.detect("x.gg", b"") is consoles.SMS
    assert consoles.detect("x.pce", b"") is consoles.PCE
    gb = bytearray(0x8000)
    gb[0x104:0x10C] = bytes.fromhex("CEED6666CC0D000B")
    assert consoles.detect("x.bin", bytes(gb)) is consoles.GB
    for at in (0x7FF0, 0x3FF0, 0x1FF0):
        sms = bytearray(0x8000)
        sms[at : at + 8] = b"TMR SEGA"
        assert consoles.detect("x.bin", bytes(sms)) is consoles.SMS
        assert consoles.detect("x.bin", bytes(512) + bytes(sms)) is consoles.SMS
    assert consoles.detect("x.bin", bytes(0x8000)) is None


def test_copier_headers_are_skipped_as_the_emulator_skips_them():
    assert consoles.SMS.header(bytes(0x8000 + 512)) == 512
    assert consoles.SMS.header(bytes(0x8000)) == 0
    assert consoles.PCE.header(bytes(0x60000 + 512)) == 512  # 384 KiB and a header
    assert consoles.PCE.header(bytes(0x60000)) == 0
    assert consoles.GB.header(bytes(0x8000 + 512)) == 0


@pytest.mark.parametrize(
    ("con", "bank", "banks"),
    [
        (consoles.GB, 0x4000, 256),
        (consoles.SMS, 0x4000, 64),
        (consoles.PCE, 0x2000, 128),
    ],
)
def test_banked_pointer_forms_round_trip_over_every_bank(con, bank, banks):
    for n in range(banks):
        for low in (0, 0x123, bank - 1):
            off = n * bank + low
            buses = con.to_bus(off)
            assert buses, (con.id, hex(off))
            # A spelling that carries its bank above bit 16 names the byte; a
            # plain one only in its power-on slot (the PC Engine has none),
            # and otherwise the right place within a window.
            for b in buses:
                if b > 0xFFFF or (b == off and con is not consoles.PCE):
                    assert con.to_rom(b) == off, (con.id, hex(off), hex(b))
                assert b & (bank - 1) == low


def test_banked_ram_mirrors_are_one_byte():
    gb, sms, pce = consoles.GB, consoles.SMS, consoles.PCE
    assert gb.ram_of(0xE123) == gb.ram_of(0xC123) == ("gbWorkRam", 0x123)
    assert gb.canon(0xE123) == 0xC123
    assert gb.ram_of(0xFF90) == ("gbHighRam", 0x10)
    assert gb.ram_of(0xA010) == ("gbCartRam", 0x10)
    assert gb.ram_bus("gbWorkRam", 0x3456) == 0x13456  # a Game Boy Color bank
    assert gb.ram_of(0x13456) == ("gbWorkRam", 0x3456)
    with pytest.raises(ValueError):
        gb.ram_of(0x8000)  # VRAM
    assert sms.ram_of(0xE123) == sms.ram_of(0xC123) == ("smsWorkRam", 0x123)
    assert sms.canon(0xFFFC) == 0xDFFC  # the mapper registers' RAM
    assert pce.ram_of(0x2010) == ("pceWorkRam", 0x10)
    with pytest.raises(ValueError):
        pce.ram_of(0x0000)


def test_banked_readers_are_their_own_processors():
    from mapchar.capture.probe import reader_cpu

    assert reader_cpu(consoles.GB, 0x1234) == "gameboy"
    assert reader_cpu(consoles.SMS, 0x1234) == "sms"
    assert reader_cpu(consoles.PCE, 0x1234) == "pce"
    for con in (consoles.GB, consoles.SMS, consoles.PCE):
        assert con.readers == (con.cpu,) and con.vram in con.rams
        assert not any(h[1].endswith("VideoRam") for h in con.writes)


def test_exhirom_never_spells_rom_as_work_ram_banks():
    ex = consoles.SNES_EXHIROM
    for off in range(0x7E0000, 0x800000, 0x1000):
        for b in ex.to_bus(off):
            assert (b >> 16) not in (0x7E, 0x7F) and ex.to_rom(b) == off
    assert ex.to_bus(0x7E1234) == []  # no bank shows that half
    assert ex.to_bus(0x7E9234) == [0x3E9234]


def test_a_384k_pc_engine_card_reaches_its_last_128k_from_bank_40():
    card = bytes(0x60000)
    pce = consoles.PCE.for_rom(card)
    assert pce.to_rom(0x404000) == 0x40000
    assert pce.to_rom(0x4F5FFF) == 0x5FFFF
    assert pce.to_rom(0x204000) == 0x40000  # Mesen maps $20-$3F there too
    assert 0x404000 in pce.to_bus(0x40000)
    for off in range(0, 0x60000, 0x777):
        for b in pce.to_bus(off):
            if b > 0xFFFF:
                assert pce.to_rom(b) == off, hex(off)
    assert consoles.PCE.for_rom(bytes(0x80000)).to_rom(0x404000) == 0x80000


def test_supergrafx_work_ram_past_8k_is_its_own_byte():
    pce = consoles.PCE
    assert pce.ram_bus("pceWorkRam", 0x10) == 0x2010
    assert pce.ram_bus("pceWorkRam", 0x2010) == 0x12010
    assert pce.ram_of(0x12010) == ("pceWorkRam", 0x2010)
    assert pce.canon(0x12010) != pce.canon(0x2010)
    assert consoles.detect("x.sgx", b"") is pce


def test_mesen_is_launched_on_a_name_it_knows(tmp_path):
    from mapchar.capture.emulator import runnable_rom

    gb = bytearray(0x8000)
    gb[0x104:0x10C] = bytes.fromhex("CEED6666CC0D000B")
    rom = tmp_path / "game.bin"
    rom.write_bytes(bytes(gb))
    cap = tmp_path / "cap"
    cap.mkdir()
    link = runnable_rom(str(rom), str(cap))
    assert link == str(cap / "_rom.gb") and open(link, "rb").read() == bytes(gb)
    assert runnable_rom(str(rom), str(cap)) == link  # made once
    named = tmp_path / "game.gb"
    named.write_bytes(bytes(gb))
    assert runnable_rom(str(named), str(cap)) == str(named)
    assert consoles.detect("x.sgb", bytes(gb)) is consoles.GB  # by its logo
    assert ".sgb" not in consoles.GB.extensions


def test_hidden_emulators_are_silent(tmp_path):
    # --testRunner starts Mesen's core with no audio device and no window:
    # it is what keeps the replay and probe emulators silent and unseen. The
    # emulator the user plays in has neither switch.
    exe = tmp_path / "mesen"
    exe.write_text("")
    m = Mesen(str(exe))
    for role in (REPLAY, PROBE):
        assert "--testRunner" in m.arguments("r.sfc", role, "s.lua")
    assert "--testRunner" not in m.arguments("r.sfc", RECORDER, "s.lua")
