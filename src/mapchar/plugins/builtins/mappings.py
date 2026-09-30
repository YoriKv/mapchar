"""Built-in pointer mappings: pointer value to payload offset and back.

Offsets are into the decompressed payload, which is already header-less.
``bank`` supplies what a short pointer leaves out; ``ptr_address`` is where
the pointer itself sits, for relative mappings.

Every mapping here with ``needs_bank`` also answers ``bank_of(offset)``: which
bank an offset sits in, so that a search for the pointers reaching it need not
be told the bank it would have to guess
(:func:`mapchar.engines.pointers.discover`). It is optional, since a mapping
whose banks are not the file's own order may not be able to say.
"""

from __future__ import annotations

from mapchar.plugins.base import PluginInfo, Stage


class Linear:
    info = PluginInfo("linear", "Linear (file offset)", Stage.MAPPING, "Generic")
    sizes = (1, 2, 3, 4)
    needs_bank = False

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        return value if value >= 0 else None

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return offset


class Relative:
    info = PluginInfo("relative", "Relative to the pointer", Stage.MAPPING, "Generic")
    sizes = (1, 2, 3, 4)
    needs_bank = False

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        return ptr_address + value

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return offset - ptr_address


class Banked:
    """Fixed-size banks mapped at one CPU address; NES-style.

    A 16-bit pointer holds the CPU address; the bank comes from ``bank``. A
    wider pointer carries the bank number above bit 16, kept to ``bank_mask``
    when one is set. ``low_bank`` maps an address below ``bank_base`` straight
    to that offset, the way the Game Boy's fixed bank sits below the switched
    window; ``bounded`` rejects an address past the end of the window;
    ``wide_value`` writes the bank back above bit 16.
    """

    sizes = (2, 3, 4)
    needs_bank = True
    bank_mask: int | None = None
    low_bank = False
    bounded = True
    wide_value = False

    def __init__(
        self,
        bank_size: int,
        bank_base: int,
        id: str | None = None,
        name: str | None = None,
        category: str = "Generic",
    ):
        self.bank_size = bank_size
        self.bank_base = bank_base
        self.info = PluginInfo(
            id or f"banked:{bank_base:X}:{bank_size:X}",
            name or f"Banked {bank_size:X} at {bank_base:X}",
            Stage.MAPPING,
            category,
        )

    def bank_of(self, offset: int) -> int:
        return offset // self.bank_size

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        addr = value & 0xFFFF
        if value > 0xFFFF:
            bank = value >> 16
            if self.bank_mask is not None:
                bank &= self.bank_mask
        if addr < self.bank_base:
            return addr if self.low_bank else None
        if self.bounded and addr >= self.bank_base + self.bank_size:
            return None
        return bank * self.bank_size + (addr - self.bank_base)

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        b = offset // self.bank_size
        addr = self.bank_base + offset % self.bank_size
        if self.low_bank and b == 0:
            return offset
        return ((b << 16) | addr) if self.wide_value else addr


class LoRom(Banked):
    sizes = (2, 3)
    bank_mask = 0x7F
    bounded = False
    wide_value = True

    def __init__(self) -> None:
        super().__init__(0x8000, 0x8000, "lorom", "SNES LoROM", "Nintendo")


class HiRom:
    info = PluginInfo("hirom", "SNES HiROM", Stage.MAPPING, "Nintendo")
    sizes = (2, 3)
    needs_bank = True

    def bank_of(self, offset: int) -> int:
        return offset // 0x10000

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        if value > 0xFFFF:
            return value & 0x3FFFFF
        return (bank & 0x3F) * 0x10000 + value

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return 0xC00000 + offset


class ExHiRom:
    """SNES ExHiROM, for images past 4 MiB: banks ``$C0-$FF`` are the first
    4 MiB, ``$40-$7D`` the next; ``$80-$BF:8000-FFFF`` show ``$C0-$FF``'s
    upper halves and ``$00-$3F:8000-FFFF`` the second 4 MiB's, the last
    128 KiB's only so (``$7E-$7F`` are work RAM). A value is written as
    ``$C0``-and-up below 4 MiB, ``$40``-and-up past it, and ``$3E-$3F`` in the
    last 128 KiB, whose lower halves no address reaches. The bank a 16-bit
    pointer leaves out is the target's 64 KiB bank of the image, 0-127, so the
    image reads as one run of banks."""

    info = PluginInfo("exhirom", "SNES ExHiROM", Stage.MAPPING, "Nintendo")
    sizes = (2, 3)
    needs_bank = True

    def applies(self, data: bytes) -> bool:
        """Only an image past 4 MiB: below it this reads as HiROM does."""
        return len(data) > 0x400000

    def bank_of(self, offset: int) -> int:
        return offset // 0x10000

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        low = value & 0xFFFF
        if value <= 0xFFFF:
            return (bank & 0x7F) * 0x10000 + low
        b = (value >> 16) & 0xFF
        if b >= 0xC0:
            return (b - 0xC0) * 0x10000 + low
        if b >= 0x80:
            return (b - 0x80) * 0x10000 + low if low >= 0x8000 else None
        if b >= 0x7E:
            return None  # work RAM
        if b >= 0x40:
            return 0x400000 + (b - 0x40) * 0x10000 + low
        if low < 0x8000:
            return None  # system area
        return 0x400000 + b * 0x10000 + low

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        if offset < 0x400000:
            return 0xC00000 + offset
        if offset < 0x7E0000:
            return offset  # $40-$7D
        if offset < 0x800000 and offset & 0xFFFF >= 0x8000:
            return offset - 0x400000  # $3E-$3F: banks $7E-$7F are work RAM
        return -1  # no address reaches it


class Sa1:
    """SNES SA-1 with the Super MMC as it powers on (CXB..FXB = 0..3): the
    LoROM-style banks ``$00-$1F``, ``$20-$3F``, ``$80-$9F`` and ``$A0-$BF``
    are the image's four MiB in order, 32 KiB a bank —
    ``(bank < $80 ? bank : bank - $40) * $8000 + (addr - $8000)`` — and
    ``$C0-$FF`` the same four MiB HiROM-style. ``$80-$BF`` is not a FastROM
    mirror here. A value is written LoROM-style. A game that reprograms the
    Super MMC puts other MiB in those windows, and is outside this mapping;
    past 4 MiB nothing reaches. The bank a 16-bit pointer leaves out is the
    target's 32 KiB bank of the image, 0-127."""

    info = PluginInfo("sa1", "SNES SA-1", Stage.MAPPING, "Nintendo")
    sizes = (2, 3)
    needs_bank = True

    def applies(self, data: bytes) -> bool:
        """Only an image whose header, at ``$7FC0`` as the SA-1's is, names
        the chip: map mode ``$23``/``$33``, or a chipset of ``$3x`` beside a
        map mode at all. Elsewhere this reads as LoROM does below 2 MiB."""
        return sa1_header(data)

    def bank_of(self, offset: int) -> int:
        return offset // 0x8000

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        low = value & 0xFFFF
        if value <= 0xFFFF:
            return (bank & 0x7F) * 0x8000 + (low - 0x8000) if low >= 0x8000 else None
        b = (value >> 16) & 0xFF
        if b >= 0xC0:
            return (b - 0xC0) * 0x10000 + low
        if low < 0x8000 or 0x40 <= b < 0x80:
            return None  # I/O, RAM, BW-RAM
        return (b if b < 0x80 else b - 0x40) * 0x8000 + (low - 0x8000)

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        if not 0 <= offset < 0x400000:
            return -1  # the default Super MMC state reaches 4 MiB
        b = offset // 0x8000
        return ((b if b < 0x40 else b + 0x40) << 16) | 0x8000 | (offset & 0x7FFF)


def sa1_header(data: bytes) -> bool:
    """Whether a SNES image's internal header names the SA-1."""
    if len(data) < 0x7FD7:
        return False
    mode, chip = data[0x7FD5], data[0x7FD6]
    return mode in (0x23, 0x33) or (0x20 <= mode <= 0x3F and chip >> 4 == 0x3)


class GameBoyMapping(Banked):
    sizes = (2, 3)
    low_bank = True
    bounded = False
    wide_value = True

    def __init__(self) -> None:
        super().__init__(0x4000, 0x4000, "gb", "Game Boy banked", "Nintendo")


class GbaMapping:
    info = PluginInfo("gba", "GBA ROM (0x08000000)", Stage.MAPPING, "Nintendo")
    sizes = (4,)
    needs_bank = False

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        if 0x08000000 <= value < 0x0A000000:
            return value - 0x08000000
        return None

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return 0x08000000 + offset


def parse_banked(id: str) -> Banked | None:
    """``banked:<base hex>:<size hex>`` built on demand."""
    parts = id.split(":")
    if len(parts) != 3 or parts[0] != "banked":
        return None
    try:
        return Banked(int(parts[2], 16), int(parts[1], 16), id)
    except ValueError:
        return None


def register(registry) -> None:
    for plugin in (
        Linear(),
        Relative(),
        LoRom(),
        HiRom(),
        ExHiRom(),
        Sa1(),
        GameBoyMapping(),
        GbaMapping(),
        Banked(0x4000, 0x8000, "banked"),
        Banked(0x4000, 0xC000, "nes_c000"),
        Banked(0x2000, 0x8000, "nes_8000_2000"),
    ):
        registry.register(plugin)
