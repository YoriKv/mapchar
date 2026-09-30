"""Console profiles: the per-console facts capture needs, and nothing else.

A profile says how a bus address maps to a ROM offset (the emulator converts
it where it can; the profile's formula is the fallback), which memory types
hold the ROM, the RAM and the VRAM, which processors may read text and which
write RAM, and which read filters prune their reports. Every rule of tracing
is the same for every console; these facts are all that differs.

ROM offsets in capture are the emulator's: offsets into the ROM image, without
a copier or iNES header. :meth:`Console.header` says how far into the file the
image starts.

A RAM write is logged at its bus address. :meth:`Console.canon` spells every
address of one RAM byte one way — the SNES's WRAM mirrors in banks
``$00-$3F``/``$80-$BF`` as ``$7E``, the NES's internal RAM mirrors as
``$0000-$07FF`` — so the writes to one byte are one address.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field, replace

from mapchar.capture.protocol import CaptureError
from mapchar.plugins.builtins.containers.snes import copier_header_size

# -- bus address to ROM offset


def _lorom(bus: int) -> int:
    return ((bus >> 16) & 0x7F) * 0x8000 + (bus & 0x7FFF)


def _hirom(bus: int) -> int:
    return ((bus >> 16) & 0x3F) * 0x10000 + (bus & 0xFFFF)


def _exhirom(bus: int) -> int:
    """ExHiROM: banks ``$C0-$FF`` are the first 4 MiB, ``$40-$7D`` the rest;
    ``$80-$BF`` and ``$00-$3F`` mirror their upper halves."""
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if b >= 0xC0:
        return (b - 0xC0) * 0x10000 + low
    if b >= 0x80:
        return (b - 0x80) * 0x10000 + low
    if b >= 0x40:
        return 0x400000 + (b - 0x40) * 0x10000 + low
    return 0x400000 + b * 0x10000 + low


def _superfx(bus: int) -> int:
    b = (bus >> 16) & 0x7F
    if 0x40 <= b < 0x60:
        return (b - 0x40) * 0x10000 + (bus & 0xFFFF)
    return b * 0x8000 + (bus & 0x7FFF)


def _sa1(bus: int) -> int:
    """SA-1 with its banks unswitched: ``$00-$3F`` the first two MiB and
    ``$80-$BF`` the next two, 32 KiB a bank; ``$C0-$FF`` a HiROM view."""
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if b >= 0xC0:
        return (b - 0xC0) * 0x10000 + low
    if b >= 0x80:
        return (b - 0x40) * 0x8000 + (low & 0x7FFF)
    return (b & 0x3F) * 0x8000 + (low & 0x7FFF)


# -- ROM offset to the addresses a pointer may hold


def _lorom_bus(off: int) -> list[int]:
    bank, low = off // 0x8000, 0x8000 | (off & 0x7FFF)
    return [(bank << 16) | low, ((bank | 0x80) << 16) | low]


def _hirom_bus(off: int) -> list[int]:
    return [((0xC0 | (off >> 16)) << 16) | (off & 0xFFFF)]


def _exhirom_bus(off: int) -> list[int]:
    """Banks ``$7E-$7F`` are the work RAM: the last 128 KiB is reachable
    only through ``$3E-$3F:8000-FFFF``."""
    low = off & 0xFFFF
    if off < 0x400000:
        return [((0xC0 + (off >> 16)) << 16) | low]
    bank = (off - 0x400000) >> 16
    out = [((0x40 + bank) << 16) | low] if 0x40 + bank < 0x7E else []
    if low >= 0x8000:
        out.append((bank << 16) | low)
    return out


def _superfx_bus(off: int) -> list[int]:
    return _lorom_bus(off) + [0x400000 + off]


def _sa1_bus(off: int) -> list[int]:
    """The unswitched view: the first 4 MiB only. Past it, where the game's
    bank registers put a byte is not known here."""
    if off >= 0x400000:
        return []
    bank, low = off // 0x8000, 0x8000 | (off & 0x7FFF)
    return [
        ((bank if bank < 0x40 else bank + 0x40) << 16) | low,
        ((0xC0 | (off >> 16)) << 16) | (off & 0xFFFF),
    ]


def _nes_bus(off: int) -> list[int]:
    """16 KiB banks at ``$8000`` or ``$C000``, 8 KiB banks at any of the
    four windows."""
    out = [0x8000 | (off & 0x3FFF), 0xC000 | (off & 0x3FFF)]
    for base in (0x8000, 0xA000, 0xC000, 0xE000):
        a = base | (off & 0x1FFF)
        if a not in out:
            out.append(a)
    return out


# -- RAM: a bus address to (memory type, offset), and back


def _not_ram(bus: int) -> ValueError:
    return ValueError(f"${bus:06X} is in no RAM capture watches")


def _wram(bus: int) -> tuple[str, int] | None:
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if b in (0x7E, 0x7F):
        return "snesWorkRam", bus & 0x1FFFF
    if (b & 0x7F) < 0x40 and low < 0x2000:
        return "snesWorkRam", low
    return None


def _snes_ram(bus: int) -> tuple[str, int]:
    """LoROM: WRAM, and the save RAM at ``$70-$7D`` (``$F0-$FF``)."""
    w = _wram(bus)
    if w:
        return w
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if low < 0x8000 and (0x70 <= b <= 0x7D or b >= 0xF0):
        return "snesSaveRam", ((b & 0x7F) - 0x70) * 0x8000 + low
    raise _not_ram(bus)


def _snes_ram_bus(memory: str, off: int) -> int:
    if memory == "snesWorkRam":
        return 0x7E0000 + off
    if memory == "snesSaveRam" and off < 0x70000:  # past it: $7E, the WRAM
        return ((0x70 + off // 0x8000) << 16) | (off & 0x7FFF)
    raise _not_ram(off)


def _hirom_ram(bus: int) -> tuple[str, int]:
    """HiROM and ExHiROM: WRAM, and the save RAM at ``$20-$3F:6000-7FFF``
    (and the banks ``$80`` above)."""
    w = _wram(bus)
    if w:
        return w
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if 0x20 <= (b & 0x7F) < 0x40 and 0x6000 <= low < 0x8000:
        return "snesSaveRam", (b & 0x1F) * 0x2000 + low - 0x6000
    raise _not_ram(bus)


def _hirom_ram_bus(memory: str, off: int) -> int:
    if memory == "snesWorkRam":
        return 0x7E0000 + off
    if memory == "snesSaveRam":
        return ((0x20 + off // 0x2000) << 16) | (0x6000 + (off & 0x1FFF))
    raise _not_ram(off)


def _superfx_ram(bus: int) -> tuple[str, int]:
    """WRAM, and the SuperFX's RAM at ``$70-$71`` (its first 8 KiB also at
    ``$00-$3F:6000-7FFF``)."""
    w = _wram(bus)
    if w:
        return w
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if (b & 0x7F) in (0x70, 0x71):
        return "gsuWorkRam", ((b & 0x7F) - 0x70) * 0x10000 + low
    if (b & 0x7F) < 0x40 and 0x6000 <= low < 0x8000:
        return "gsuWorkRam", low - 0x6000
    raise _not_ram(bus)


def _superfx_ram_bus(memory: str, off: int) -> int:
    if memory == "snesWorkRam":
        return 0x7E0000 + off
    if memory == "gsuWorkRam":
        return 0x700000 + off
    raise _not_ram(off)


def _sa1_ram(bus: int) -> tuple[str, int]:
    """WRAM; the SA-1's I-RAM at ``$00-$3F:3000-37FF``; its BW-RAM at
    ``$40-$4F``, and through the window at ``$00-$3F:6000-7FFF`` (taken as
    its first 8 KiB)."""
    w = _wram(bus)
    if w:
        return w
    b, low = (bus >> 16) & 0xFF, bus & 0xFFFF
    if (b & 0x7F) < 0x40 and 0x3000 <= low < 0x3800:
        return "sa1InternalRam", low - 0x3000
    if 0x40 <= b < 0x50:
        return "snesSaveRam", (b - 0x40) * 0x10000 + low
    if (b & 0x7F) < 0x40 and 0x6000 <= low < 0x8000:
        return "snesSaveRam", low - 0x6000
    raise _not_ram(bus)


def _sa1_ram_bus(memory: str, off: int) -> int:
    if memory == "snesWorkRam":
        return 0x7E0000 + off
    if memory == "sa1InternalRam":
        return 0x3000 + off
    if memory == "snesSaveRam":
        return 0x400000 + off
    raise _not_ram(off)


def _nes_ram(bus: int) -> tuple[str, int]:
    if bus < 0x2000:
        return "nesInternalRam", bus & 0x7FF
    if 0x6000 <= bus < 0x8000:
        # The scripts take nesWorkRam for it on a cart without a battery.
        return "nesSaveRam", bus - 0x6000
    raise _not_ram(bus)


def _nes_ram_bus(memory: str, off: int) -> int:
    if memory == "nesInternalRam":
        return off & 0x7FF
    if memory in ("nesSaveRam", "nesWorkRam"):
        return 0x6000 + off
    raise _not_ram(off)


def _gba_ram(bus: int) -> tuple[str, int]:
    if (bus >> 24) == 3:
        return "gbaIntWorkRam", bus & 0x7FFF
    if (bus >> 24) == 2:
        return "gbaExtWorkRam", bus & 0x3FFFF
    raise _not_ram(bus)


def _gba_ram_bus(memory: str, off: int) -> int:
    if memory == "gbaIntWorkRam":
        return 0x03000000 + (off & 0x7FFF)
    if memory == "gbaExtWorkRam":
        return 0x02000000 + (off & 0x3FFFF)
    raise _not_ram(off)


def _gb_rom(bus: int) -> int:
    """Bank 0 is fixed at ``$0000-$3FFF``; the switched window
    ``$4000-$7FFF`` holds the bank above bit 16 (``$05:4123``), bank 1 when
    none is given."""
    bank, addr = bus >> 16, bus & 0xFFFF
    if addr < 0x4000:
        return addr
    return max(bank, 1) * 0x4000 + (addr & 0x3FFF)


def _gb_bus(off: int) -> list[int]:
    """Bank 0 as itself; any other byte in the window, plain and with its
    bank above bit 16."""
    if off < 0x4000:
        return [off]
    window = 0x4000 | (off & 0x3FFF)
    return [window, ((off >> 14) << 16) | window]


def _gb_ram(bus: int) -> tuple[str, int]:
    """Work RAM at ``$C000-$DFFF`` and its echo at ``$E000-$FDFF``, the
    cartridge's RAM at ``$A000-$BFFF``, high RAM at ``$FF80-$FFFE``; the
    banks past the first of a Game Boy Color's work RAM, or of a cartridge
    RAM, are spelled past ``$10000`` / ``$20000``."""
    if 0xC000 <= bus < 0xFE00:
        return "gbWorkRam", (bus - 0xC000) & 0x1FFF
    if 0xA000 <= bus < 0xC000:
        return "gbCartRam", bus - 0xA000
    if 0xFF80 <= bus < 0xFFFF:
        return "gbHighRam", bus - 0xFF80
    if 0x10000 <= bus < 0x20000:
        return "gbWorkRam", bus - 0x10000
    if 0x20000 <= bus < 0x40000:
        return "gbCartRam", bus - 0x20000
    raise _not_ram(bus)


def _gb_ram_bus(memory: str, off: int) -> int:
    if memory == "gbWorkRam":
        return 0xC000 + off if off < 0x2000 else 0x10000 + off
    if memory == "gbCartRam":
        return 0xA000 + off if off < 0x2000 else 0x20000 + off
    if memory == "gbHighRam":
        return 0xFF80 + off
    raise _not_ram(off)


def _sms_rom(bus: int) -> int:
    """A slot address with its bank above bit 16 (``$05:8123``); without
    one, the slots' power-on banks 0, 1 and 2."""
    bank, addr = bus >> 16, bus & 0xFFFF
    if bank:
        return bank * 0x4000 + (addr & 0x3FFF)
    return addr


def _sms_bus(off: int) -> list[int]:
    """The byte through each 16 KiB slot it can be paged into (slot 0's
    first KiB is always bank 0), plain and with its bank above bit 16."""
    bank, low = off >> 14, off & 0x3FFF
    out = []
    for slot in (0x0000, 0x4000, 0x8000):
        if slot == 0 and bank and low < 0x400:
            continue
        a = slot | low
        out.append(a)
        if bank:
            out.append((bank << 16) | a)
    return out


def _sms_ram(bus: int) -> tuple[str, int]:
    """Work RAM at ``$C000-$DFFF``, mirrored at ``$E000-$FFFF``; the
    cartridge's RAM in slot 2 (``$8000-$BFFF``), banks past the first
    spelled past ``$20000``."""
    if 0xC000 <= bus < 0x10000:
        return "smsWorkRam", bus & 0x1FFF
    if 0x8000 <= bus < 0xC000:
        return "smsCartRam", bus - 0x8000
    if 0x20000 <= bus < 0x40000:
        return "smsCartRam", bus - 0x20000
    raise _not_ram(bus)


def _sms_ram_bus(memory: str, off: int) -> int:
    if memory == "smsWorkRam":
        return 0xC000 + (off & 0x1FFF)
    if memory == "smsCartRam":
        return 0x8000 + off if off < 0x4000 else 0x20000 + off
    raise _not_ram(off)


def _pce_bank_rom(bank: int, split: bool = False) -> int:
    """Where a bank (a page register's value) starts in the image. A 384 KiB
    card (``split``) is wired 256 KiB + 128 KiB: the game reaches its last
    128 KiB through banks ``$40-$7F`` (as through ``$20-$3F``, which Mesen
    maps there too), each 16 banks a mirror."""
    if split and bank >= 0x20:
        return 0x40000 + (bank & 0x0F) * 0x2000
    return bank * 0x2000


def _pce_rom(bus: int, split: bool = False) -> int:
    """A logical address with its bank above bit 16 (``$05:4123``); without
    one, the address within its page."""
    return _pce_bank_rom(bus >> 16, split) + (bus & 0x1FFF)


def _pce_bus(off: int, split: bool = False) -> list[int]:
    """The byte through any 8 KiB page a game maps ROM at (``$4000-$FFFF``;
    ``$0000`` is I/O and ``$2000`` the work RAM), plain and with its bank
    above bit 16 — for a 384 KiB card's last 128 KiB, the bank the game
    uses, ``$40`` up."""
    bank, low = off >> 13, off & 0x1FFF
    if split and off >= 0x40000:
        bank = 0x40 + ((off - 0x40000) >> 13)
    out = []
    for page in range(2, 8):
        a = (page << 13) | low
        out += [a, (bank << 16) | a] if bank else [a]
    return out


def _pce_split_rom(bus: int) -> int:
    return _pce_rom(bus, True)


def _pce_split_bus(off: int) -> list[int]:
    return _pce_bus(off, True)


def _pce_ram(bus: int) -> tuple[str, int]:
    """The work RAM through page 1 (``$2000-$3FFF``), where games map it; a
    SuperGrafx's 32 KiB past the first 8 KiB spelled past ``$10000``."""
    if 0x2000 <= bus < 0x4000:
        return "pceWorkRam", bus - 0x2000
    if 0x10000 <= bus < 0x18000:
        return "pceWorkRam", bus - 0x10000
    raise _not_ram(bus)


def _pce_ram_bus(memory: str, off: int) -> int:
    if memory == "pceWorkRam":
        return 0x2000 + off if off < 0x2000 else 0x10000 + off
    raise _not_ram(off)


# -- the profiles


Hook = tuple[str, str, int, int, str, bool]
"""A write hook: ``(cpu, memory type, lo, hi, RAM, by bus)``. The callback is
set on ``lo..hi`` of the memory type (``hi`` -1: to the memory's end) for that
processor; RAM is the memory it lands in. With *by bus* the memory type is the
processor's own address space, and a range of the RAM at offset *o* is watched
at ``lo + o``."""


@dataclass(frozen=True)
class Console:
    id: str
    name: str
    lua: str
    """The console section of the scripts: ``snes``, ``nes`` or ``gba``."""
    cpu: str
    """The main CPU's ``emu.cpuType`` name."""
    top: int
    """The top of the main CPU's address space, for whole-space callbacks."""
    rom: str
    """The ROM's ``emu.memType`` name."""
    rams: tuple[str, ...]
    """Memory types hashed and saved at a capture; the last is the VRAM."""
    readers: tuple[str, ...]
    """Processors whose ROM data reads are evidence."""
    to_rom: Callable[[int], int]
    """A logged read address to a ROM offset, where the emulator gave none."""
    to_bus: Callable[[int], list[int]]
    """A ROM offset to the addresses a pointer to it may hold."""
    ram_of: Callable[[int], tuple[str, int]]
    """A logged write address to ``(memory type, offset)``; ValueError for an
    address in no RAM capture watches."""
    mapping_ids: tuple[str, ...] = ()
    """The pointer mappings a pointer table found here is tried with."""
    pointer_sizes: tuple[int, ...] = (2, 3)
    extensions: tuple[str, ...] = field(default=())
    extra_rams: tuple[str, ...] = ()
    """More memories hashed and saved, after :attr:`rams` (so a hash taken
    without them still compares): the save RAM, a coprocessor's RAM."""
    writes: tuple[Hook, ...] = ()
    """Where RAM writes are logged; see :data:`Hook`."""
    ram_bus: Callable[[str, int], int] | None = None
    """``(memory type, offset)`` to the one bus address :meth:`canon` spells
    it as."""
    aliases: tuple[tuple[str, str], ...] = ()
    """``(memory, stand-in)``: the scripts take the stand-in for a memory the
    cartridge does not have (the NES's work RAM for a save RAM)."""
    limits: str = ""
    """What the profile's pointer mappings cannot resolve, said to the user."""
    sram_size: int = 0
    """The cartridge's save RAM (or coprocessor RAM) in bytes, where
    :meth:`for_rom` read it: a save-RAM offset repeats past it. 0: unknown."""

    def for_rom(self, data: bytes) -> Console:
        """This profile with what a ROM's header or size says: the save RAM's
        size; a 384 KiB PC Engine card's split layout."""
        if self.lua == "pce":
            if len(self.image(data)) == 0x60000:
                return replace(self, to_rom=_pce_split_rom, to_bus=_pce_split_bus)
            return self
        if self.lua != "snes":
            return self
        rom = data[copier_header_size(len(data)) :]
        at = {"snes-hirom": 0xFFC0, "snes-exhirom": 0x40FFC0}.get(self.id, 0x7FC0)
        if self.id == "snes-superfx":
            n = rom[0x7FBD] if len(rom) > 0x7FBD else 0
        else:
            n = rom[at + 0x18] if len(rom) > at + 0x18 else 0
        size = 1024 << n if 0 < n <= 10 else 0
        return replace(self, sram_size=size)

    def locate(self, bus: int) -> tuple[str, int]:
        """:attr:`ram_of`, with a save-RAM offset brought within the save
        RAM's size when it is known — its mirrors are one byte."""
        mem, off = self.ram_of(bus)
        if self.sram_size and mem in ("snesSaveRam", "gsuWorkRam"):
            off %= self.sram_size
        return mem, off

    @property
    def vram(self) -> str:
        return self.rams[-1]

    def canon(self, bus: int) -> int:
        """One spelling for every bus address of a RAM byte; an address in no
        RAM stays as it is."""
        if self.ram_bus is None:
            return bus
        try:
            return self.ram_bus(*self.locate(bus))
        except ValueError:
            return bus

    def header(self, data: bytes) -> int:
        """Where the ROM image starts in the file: past an iNES header, or a
        copier's 512 bytes, the way the emulator skips them."""
        if self.lua == "sms":
            return 512 if len(data) % 0x400 == 0x200 else 0
        if self.lua == "pce":
            return 512 if len(data) % 0x2000 == 512 else 0
        if self.lua == "nes":
            if data[:4] == b"NES\x1a":
                return 16 + (512 if data[6] & 4 else 0)
            return 0
        if self.lua == "snes":
            return copier_header_size(len(data))
        return 0

    def image(self, data: bytes) -> bytes:
        """The ROM image as the emulator numbers it: the PRG ROM alone for the
        NES."""
        body = data[self.header(data) :]
        if self.lua == "nes" and data[:4] == b"NES\x1a":
            body = body[: nes_prg_size(data)]
        if not body:
            raise CaptureError("the ROM file holds no ROM image")
        return body

    def config(self) -> dict:
        """The facts the scripts need, as :func:`lua_table` spells them."""
        return {
            "console": self.lua,
            "cpu": self.cpu,
            "top": self.top,
            "rom": self.rom,
            "rams": list(self.rams),
            "extraRams": list(self.extra_rams),
            "readers": list(self.readers),
            "writes": [list(h) for h in self.writes],
            "aliases": dict(self.aliases),
        }


def nes_prg_size(data: bytes) -> int:
    """The PRG ROM's size an iNES or NES 2.0 header gives."""
    lsb = data[4]
    if (data[7] & 0x0C) == 0x08:  # NES 2.0
        msb = data[9] & 0x0F
        if msb == 0x0F:
            return (1 << (lsb >> 2)) * ((lsb & 3) * 2 + 1)
        return ((msb << 8) | lsb) * 0x4000
    return lsb * 0x4000


_SNES_RAMS = ("snesWorkRam", "snesVideoRam")
_SNES_EXTS = (".sfc", ".smc", ".swc", ".fig")
_WRAM_HOOK: Hook = ("snes", "snesWorkRam", 0, 0x1FFFF, "snesWorkRam", False)
_SRAM_HOOK: Hook = ("snes", "snesSaveRam", 0, -1, "snesSaveRam", False)

SNES_LOROM = Console(
    "snes-lorom",
    "SNES (LoROM)",
    "snes",
    "snes",
    0xFFFFFF,
    "snesPrgRom",
    _SNES_RAMS,
    ("snes",),
    _lorom,
    _lorom_bus,
    _snes_ram,
    ("lorom",),
    extensions=_SNES_EXTS,
    extra_rams=("snesSaveRam",),
    writes=(_WRAM_HOOK, _SRAM_HOOK),
    ram_bus=_snes_ram_bus,
)
SNES_HIROM = Console(
    "snes-hirom",
    "SNES (HiROM)",
    "snes",
    "snes",
    0xFFFFFF,
    "snesPrgRom",
    _SNES_RAMS,
    ("snes",),
    _hirom,
    _hirom_bus,
    _hirom_ram,
    ("hirom",),
    extensions=_SNES_EXTS,
    extra_rams=("snesSaveRam",),
    writes=(_WRAM_HOOK, _SRAM_HOOK),
    ram_bus=_hirom_ram_bus,
)
SNES_EXHIROM = Console(
    "snes-exhirom",
    "SNES (ExHiROM)",
    "snes",
    "snes",
    0xFFFFFF,
    "snesPrgRom",
    _SNES_RAMS,
    ("snes",),
    _exhirom,
    _exhirom_bus,
    _hirom_ram,
    ("exhirom",),
    extensions=_SNES_EXTS,
    extra_rams=("snesSaveRam",),
    writes=(_WRAM_HOOK, _SRAM_HOOK),
    ram_bus=_hirom_ram_bus,
)
SNES_SUPERFX = Console(
    "snes-superfx",
    "SNES (SuperFX)",
    "snes",
    "snes",
    0xFFFFFF,
    "snesPrgRom",
    _SNES_RAMS,
    ("snes", "gsu"),
    _superfx,
    _superfx_bus,
    _superfx_ram,
    ("lorom", "hirom"),
    extensions=_SNES_EXTS,
    extra_rams=("gsuWorkRam",),
    writes=(
        _WRAM_HOOK,
        # A callback on gsuWorkRam itself never fired: the SuperFX's writes
        # are watched on its own bus.
        ("gsu", "gsuMemory", 0x700000, 0x71FFFF, "gsuWorkRam", True),
        ("snes", "snesMemory", 0x700000, 0x71FFFF, "gsuWorkRam", True),
    ),
    ram_bus=_superfx_ram_bus,
)
SNES_SA1 = Console(
    "snes-sa1",
    "SNES (SA-1)",
    "snes",
    "snes",
    0xFFFFFF,
    "snesPrgRom",
    _SNES_RAMS,
    ("snes", "sa1"),
    _sa1,
    _sa1_bus,
    _sa1_ram,
    # HiROM after it: a short pointer below $8000 read through a $Cx bank.
    ("sa1", "hirom"),
    extensions=_SNES_EXTS,
    extra_rams=("sa1InternalRam", "snesSaveRam"),
    writes=(
        _WRAM_HOOK,
        ("snes", "sa1InternalRam", 0, -1, "sa1InternalRam", False),
        ("snes", "snesSaveRam", 0, -1, "snesSaveRam", False),
        ("sa1", "sa1InternalRam", 0, -1, "sa1InternalRam", False),
        ("sa1", "snesSaveRam", 0, -1, "snesSaveRam", False),
    ),
    ram_bus=_sa1_ram_bus,
    limits="A game that reprograms the Super MMC switches other ROM into its "
    "banks: pointers into it are not resolved.",
)
NES = Console(
    "nes",
    "NES",
    "nes",
    "nes",
    0xFFFF,
    "nesPrgRom",
    ("nesInternalRam", "nesSaveRam", "nesChrRam", "nesNametableRam"),
    ("nes",),
    lambda a: a,
    _nes_bus,
    _nes_ram,
    ("banked", "nes_c000", "nes_8000_2000"),
    (2,),
    extensions=(".nes",),
    extra_rams=("nesWorkRam",),
    writes=(
        # Internal RAM with its mirrors, then the cartridge's RAM.
        ("nes", "nesMemory", 0x0000, 0x1FFF, "nesInternalRam", False),
        ("nes", "nesMemory", 0x6000, 0x7FFF, "nesSaveRam", True),
    ),
    ram_bus=_nes_ram_bus,
    aliases=(("nesSaveRam", "nesWorkRam"), ("nesWorkRam", "nesSaveRam")),
)
GBA = Console(
    "gba",
    "Game Boy Advance",
    "gba",
    "gba",
    0x0FFFFFFF,
    "gbaPrgRom",
    ("gbaIntWorkRam", "gbaExtWorkRam", "gbaVideoRam"),
    ("gba",),
    lambda a: a & 0x1FFFFFF,
    lambda off: [0x08000000 | off],
    _gba_ram,
    ("gba",),
    (4,),
    extensions=(".gba", ".agb"),
    writes=(
        ("gba", "gbaIntWorkRam", 0, -1, "gbaIntWorkRam", False),
        ("gba", "gbaExtWorkRam", 0, -1, "gbaExtWorkRam", False),
    ),
    ram_bus=_gba_ram_bus,
)

GB = Console(
    "gb",
    "Game Boy / Game Boy Color",
    "gb",
    "gameboy",
    0xFFFF,
    "gbPrgRom",
    ("gbWorkRam", "gbHighRam", "gbVideoRam"),
    ("gameboy",),
    _gb_rom,
    _gb_bus,
    _gb_ram,
    ("gb",),
    (2,),
    extensions=(".gb", ".gbc"),
    extra_rams=("gbCartRam",),
    writes=(
        ("gameboy", "gbWorkRam", 0, -1, "gbWorkRam", False),
        ("gameboy", "gbHighRam", 0, -1, "gbHighRam", False),
        ("gameboy", "gbCartRam", 0, -1, "gbCartRam", False),
    ),
    ram_bus=_gb_ram_bus,
    limits="On an MBC1 cartridge of 1 MiB or more, banks $20, $40 and $60 never "
    "sit in the $4000 window, though the fallback spells them there.",
)
SMS = Console(
    "sms",
    "Master System / Game Gear",
    "sms",
    "sms",
    0xFFFF,
    "smsPrgRom",
    ("smsWorkRam", "smsVideoRam"),
    ("sms",),
    _sms_rom,
    _sms_bus,
    _sms_ram,
    ("linear", "banked:8000:4000", "banked:4000:4000"),
    (2,),
    extensions=(".sms", ".gg"),
    extra_rams=("smsCartRam",),
    writes=(
        ("sms", "smsWorkRam", 0, -1, "smsWorkRam", False),
        ("sms", "smsCartRam", 0, -1, "smsCartRam", False),
    ),
    ram_bus=_sms_ram_bus,
    limits="A pointer into a bank paged in at $0000-$3FFF is not resolved "
    "past the bank's first window.",
)
PCE = Console(
    "pce",
    "PC Engine",
    "pce",
    "pce",
    0xFFFF,
    "pcePrgRom",
    ("pceWorkRam", "pceVideoRam"),
    ("pce",),
    _pce_rom,
    _pce_bus,
    _pce_ram,
    tuple(f"banked:{page << 13:X}:2000" for page in range(2, 8)),
    (2,),
    extensions=(".pce", ".sgx"),
    writes=(("pce", "pceWorkRam", 0, -1, "pceWorkRam", False),),
    ram_bus=_pce_ram_bus,
    limits="A pointer is resolved only with the page a mapping names; which "
    "page register a game used is not known.",
)

CONSOLES: dict[str, Console] = {
    c.id: c
    for c in (
        SNES_LOROM,
        SNES_HIROM,
        SNES_EXHIROM,
        SNES_SUPERFX,
        SNES_SA1,
        NES,
        GBA,
        GB,
        SMS,
        PCE,
    )
}

_GSU_CHIPS = {0x13, 0x14, 0x15, 0x1A}


def _snes_profile(rom: bytes) -> Console:
    def score(at: int, modes: tuple[int, ...]) -> int:
        """Points for a header at ``at``: 2 for a checksum and its
        complement, 1 for a map mode of one of ``modes`` (the low nibble)."""
        if len(rom) < at + 0x20:
            return -1
        comp = int.from_bytes(rom[at + 0x1C : at + 0x1E], "little")
        chk = int.from_bytes(rom[at + 0x1E : at + 0x20], "little")
        points = 2 if comp ^ chk == 0xFFFF else 0
        mode = rom[at + 0x15]
        if 0x20 <= mode <= 0x3F and (mode & 0x0F) in modes:
            points += 1
        return points

    lo = score(0x7FC0, (0x0, 0x2, 0x3))
    hi = score(0xFFC0, (0x1, 0xA))
    ex = score(0x40FFC0, (0x5,))
    if ex > 0 and ex >= max(lo, hi):
        return SNES_EXHIROM
    if hi > lo:
        return SNES_HIROM
    mode = rom[0x7FD5] if len(rom) > 0x7FD6 else 0
    chip = rom[0x7FD6] if len(rom) > 0x7FD6 else 0
    if (mode & 0x0F) == 0x3 or (chip >> 4) == 0x3:
        return SNES_SA1
    if chip in _GSU_CHIPS:
        return SNES_SUPERFX
    return SNES_LOROM


_GBA_LOGO = bytes.fromhex("24FFAE51699AA221")
_GB_LOGO = bytes.fromhex("CEED6666CC0D000B")


def _is_gba(data: bytes) -> bool:
    """The fixed bytes of a GBA header: the logo's start, ``$96`` at ``$B2``,
    and the header checksum."""
    if len(data) < 0xC0 or data[4:12] != _GBA_LOGO or data[0xB2] != 0x96:
        return False
    return (-(sum(data[0xA0:0xBD]) + 0x19)) & 0xFF == data[0xBD]


def detect(path: str, data: bytes) -> Console | None:
    """The profile for a ROM file, or None for a console with none. The
    extension decides; a file's own magic only when the extension says
    nothing."""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".nes":
        return NES
    if ext in (".gba", ".agb"):
        return GBA
    if ext in _SNES_EXTS:
        return _snes_profile(data[copier_header_size(len(data)) :])
    for c in (GB, SMS, PCE):
        if ext in c.extensions:
            return c
    if data[:4] == b"NES\x1a":
        return NES
    if _is_gba(data):
        return GBA
    if data[0x104:0x10C] == _GB_LOGO:
        return GB
    body = data[SMS.header(data) :]
    if any(body[at : at + 8] == b"TMR SEGA" for at in (0x7FF0, 0x3FF0, 0x1FF0)):
        return SMS
    return None


# -- Lua literals

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_KEYWORDS = frozenset(
    "and break do else elseif end false for function goto if in local nil not "
    "or repeat return then true until while".split()
)


def _lua_string(v: str) -> str:
    """A long bracket of the smallest level ``v`` does not close; a newline
    after the opening bracket, which Lua skips, so one that starts ``v``
    stays."""
    n = 0
    while "]" + "=" * n + "]" in v:
        n += 1
    return "[" + "=" * n + "[\n" + v + "]" + "=" * n + "]"


def lua_value(v) -> str:
    """A Python value as a Lua literal: numbers, strings, booleans, None,
    lists and string-keyed dicts."""
    if v is None:
        return "nil"
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        return repr(v)
    if isinstance(v, str):
        return _lua_string(v)
    if isinstance(v, (list, tuple)):
        return "{ " + ", ".join(lua_value(x) for x in v) + " }"
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            key = (
                k
                if isinstance(k, str) and _IDENT.match(k) and k not in _KEYWORDS
                else f"[ {lua_value(k)} ]"
            )
            parts.append(f"{key} = {lua_value(x)}")
        return "{ " + ", ".join(parts) + " }"
    raise TypeError(f"no Lua literal for {type(v).__name__}")
