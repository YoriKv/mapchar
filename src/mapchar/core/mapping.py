"""Pointer mappings: pointer value to payload offset and back.

Offsets are into the decompressed payload, which is already header-less.
``bank`` supplies what a short pointer leaves out; ``ptr_address`` is where
the pointer itself sits, for relative mappings.
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
    wider pointer carries the bank number above bit 16.
    """

    sizes = (2, 3, 4)
    needs_bank = True

    def __init__(self, bank_size: int, bank_base: int, id: str | None = None):
        self.bank_size = bank_size
        self.bank_base = bank_base
        self.info = PluginInfo(
            id or f"banked:{bank_base:X}:{bank_size:X}",
            f"Banked {bank_size:X} at {bank_base:X}",
            Stage.MAPPING,
            "Generic",
        )

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        addr = value & 0xFFFF
        if value > 0xFFFF:
            bank = value >> 16
        if not (self.bank_base <= addr < self.bank_base + self.bank_size):
            return None
        return bank * self.bank_size + (addr - self.bank_base)

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return self.bank_base + offset % self.bank_size


class LoRom:
    info = PluginInfo("lorom", "SNES LoROM", Stage.MAPPING, "Nintendo")
    sizes = (2, 3)
    needs_bank = True

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        addr = value & 0xFFFF
        if value > 0xFFFF:
            bank = (value >> 16) & 0x7F
        if addr < 0x8000:
            return None
        return bank * 0x8000 + (addr - 0x8000)

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return ((offset // 0x8000) << 16) | (0x8000 + offset % 0x8000)


class HiRom:
    info = PluginInfo("hirom", "SNES HiROM", Stage.MAPPING, "Nintendo")
    sizes = (2, 3)
    needs_bank = True

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        if value > 0xFFFF:
            return value & 0x3FFFFF
        return (bank & 0x3F) * 0x10000 + value

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return 0xC00000 + offset


class GameBoyMapping:
    info = PluginInfo("gb", "Game Boy banked", Stage.MAPPING, "Nintendo")
    sizes = (2, 3)
    needs_bank = True

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        addr = value & 0xFFFF
        if value > 0xFFFF:
            bank = value >> 16
        if addr < 0x4000:
            return addr
        return bank * 0x4000 + (addr - 0x4000)

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        b = offset // 0x4000
        if b == 0:
            return offset
        return (b << 16) | (0x4000 + offset % 0x4000)


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


def resolve_mapping(registry, id: str):
    """A mapping plugin by id, including on-demand banked ids; None if unknown."""
    plugin = registry.plugin(Stage.MAPPING, id)
    if plugin is not None:
        return plugin
    return parse_banked(id)


def read_pointer(data: bytes, address: int, size: int, endian: str) -> int | None:
    chunk = data[address : address + size]
    if len(chunk) < size:
        return None
    return int.from_bytes(chunk, "big" if endian == "big" else "little")


def pointer_bytes(value: int, size: int, endian: str) -> bytes:
    return (value & ((1 << (size * 8)) - 1)).to_bytes(
        size, "big" if endian == "big" else "little"
    )
