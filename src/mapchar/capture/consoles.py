"""Console profiles: the per-console facts capture needs, and nothing else.

A profile says how a bus address maps to a ROM offset (or that the emulator
converts it, for a bank-switched console), which memory types hold the ROM,
the RAM and the VRAM, which processors may read text, and which read filters
prune their reports. Every rule of tracing is the same for every console; these
facts are all that differs.

ROM offsets in capture are the emulator's: offsets into the ROM image, without
a copier or iNES header. :meth:`Console.header` says how far into the file the
image starts.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field

from mapchar.plugins.builtins.containers.snes import copier_header_size


def _lorom(bus: int) -> int:
    return ((bus >> 16) & 0x7F) * 0x8000 + (bus & 0x7FFF)


def _hirom(bus: int) -> int:
    return ((bus >> 16) & 0x3F) * 0x10000 + (bus & 0xFFFF)


def _superfx(bus: int) -> int:
    b = (bus >> 16) & 0x7F
    if 0x40 <= b < 0x60:
        return (b - 0x40) * 0x10000 + (bus & 0xFFFF)
    return b * 0x8000 + (bus & 0x7FFF)


def _lorom_bus(off: int) -> list[int]:
    bank, low = off // 0x8000, 0x8000 | (off & 0x7FFF)
    return [(bank << 16) | low, ((bank | 0x80) << 16) | low]


def _hirom_bus(off: int) -> list[int]:
    return [((0xC0 | (off >> 16)) << 16) | (off & 0xFFFF)]


def _superfx_bus(off: int) -> list[int]:
    return _lorom_bus(off) + [0x400000 + off]


def _snes_ram(bus: int) -> tuple[str, int]:
    if (bus >> 16) in (0x7E, 0x7F):
        return "snesWorkRam", bus & 0x1FFFF
    return "snesWorkRam", bus & 0x1FFF


def _nes_bus(off: int) -> list[int]:
    return [0x8000 | (off & 0x3FFF), 0xC000 | (off & 0x3FFF)]


def _nes_ram(bus: int) -> tuple[str, int]:
    if bus >= 0x6000:
        return "nesSaveRam", bus - 0x6000
    return "nesInternalRam", bus & 0x7FF


def _gba_ram(bus: int) -> tuple[str, int]:
    if (bus >> 24) == 3:
        return "gbaIntWorkRam", bus & 0x7FFF
    return "gbaExtWorkRam", bus & 0x3FFFF


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
    """A logged read address to a ROM offset."""
    to_bus: Callable[[int], list[int]]
    """A ROM offset to the addresses a pointer to it may hold."""
    ram_of: Callable[[int], tuple[str, int]]
    """A logged write address to ``(memory type, offset)``."""
    mapping_ids: tuple[str, ...] = ()
    """The pointer mappings a pointer table found here is tried with."""
    pointer_sizes: tuple[int, ...] = (2, 3)
    extensions: tuple[str, ...] = field(default=())

    @property
    def vram(self) -> str:
        return self.rams[-1]

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
            return body[: data[4] * 0x4000]
        return body

    def config(self) -> dict:
        """The facts the scripts need, as :func:`lua_table` spells them."""
        return {
            "console": self.lua,
            "cpu": self.cpu,
            "top": self.top,
            "rom": self.rom,
            "rams": list(self.rams),
            "readers": list(self.readers),
        }


_SNES_RAMS = ("snesWorkRam", "snesVideoRam")

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
    extensions=(".sfc", ".smc"),
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
    _snes_ram,
    ("hirom",),
    extensions=(".sfc", ".smc"),
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
    _snes_ram,
    ("lorom", "hirom"),
    extensions=(".sfc", ".smc"),
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
    extensions=(".gba",),
)

CONSOLES: dict[str, Console] = {
    c.id: c for c in (SNES_LOROM, SNES_HIROM, SNES_SUPERFX, NES, GBA)
}

_GSU_CHIPS = {0x13, 0x14, 0x15, 0x1A}


def _snes_profile(rom: bytes) -> Console:
    def score(at: int, hirom: bool) -> int:
        if len(rom) < at + 0x20:
            return -1
        comp = int.from_bytes(rom[at + 0x1C : at + 0x1E], "little")
        chk = int.from_bytes(rom[at + 0x1E : at + 0x20], "little")
        points = 2 if comp ^ chk == 0xFFFF else 0
        mode = rom[at + 0x15]
        if 0x20 <= mode <= 0x3F and bool(mode & 0x01) is hirom:
            points += 1
        return points

    if score(0xFFC0, True) > score(0x7FC0, False):
        return SNES_HIROM
    if len(rom) > 0x7FD6 and rom[0x7FD6] in _GSU_CHIPS:
        return SNES_SUPERFX
    return SNES_LOROM


def detect(path: str, data: bytes) -> Console | None:
    """The profile for a ROM file, or None for a console with none."""
    ext = os.path.splitext(path)[1].lower()
    if data[:4] == b"NES\x1a" or ext == ".nes":
        return NES
    if ext in (".gba", ".agb") or (len(data) > 0xB2 and data[0xB2] == 0x96):
        return GBA
    if ext in (".sfc", ".smc", ".swc", ".fig"):
        return _snes_profile(data[copier_header_size(len(data)) :])
    return None


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
        return "[==[" + v + "]==]"
    if isinstance(v, (list, tuple)):
        return "{ " + ", ".join(lua_value(x) for x in v) + " }"
    if isinstance(v, dict):
        return "{ " + ", ".join(f"{k} = {lua_value(x)}" for k, x in v.items()) + " }"
    raise TypeError(f"no Lua literal for {type(v).__name__}")
