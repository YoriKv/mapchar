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
    low = off & 0xFFFF
    if off < 0x400000:
        return [((0xC0 + (off >> 16)) << 16) | low]
    bank = (off - 0x400000) >> 16
    out = [((0x40 + bank) << 16) | low]
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
        """This profile with the save RAM's size a ROM's header gives."""
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
        """Where the ROM image starts in the file."""
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
    ("hirom",),
    extensions=_SNES_EXTS,
    extra_rams=("snesSaveRam",),
    writes=(_WRAM_HOOK, _SRAM_HOOK),
    ram_bus=_hirom_ram_bus,
    limits="Pointers into the upper 4 MiB are not resolved: the HiROM mapping "
    "reads 4 MiB.",
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
    ("lorom", "hirom"),
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
    limits="Pointers into banks $80-$BF, and into ROM the game's bank registers "
    "switch in, are not resolved: the LoROM mapping mirrors $80-$BF.",
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

CONSOLES: dict[str, Console] = {
    c.id: c
    for c in (SNES_LOROM, SNES_HIROM, SNES_EXHIROM, SNES_SUPERFX, SNES_SA1, NES, GBA)
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
    if data[:4] == b"NES\x1a":
        return NES
    if _is_gba(data):
        return GBA
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
