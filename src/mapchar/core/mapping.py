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

    def to_offset(self, value: int, bank: int = 0, ptr_address: int = 0) -> int | None:
        if value > 0xFFFF:
            return value & 0x3FFFFF
        return (bank & 0x3F) * 0x10000 + value

    def to_value(self, offset: int, bank: int = 0, ptr_address: int = 0) -> int:
        return 0xC00000 + offset


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


def resolve_mapping(registry, id: str):
    """A mapping plugin by id, including on-demand banked ids; None if unknown.

    A retired id is forwarded here too (:func:`~mapchar.plugins.aliases.current_id`),
    since a parameterised id is built rather than registered and so never reaches
    the registry's own fallback.
    """
    plugin = registry.plugin(Stage.MAPPING, id)
    if plugin is not None:
        return plugin
    from mapchar.plugins.aliases import current_id

    return parse_banked(current_id(id))


def mapping_for(source, registry=None):
    """The mapping plugin a source names, or ``None`` when it is unknown.

    Without a ``registry`` the built-in one answers.
    """
    if registry is None:
        from mapchar.plugins.registry import default_registry

        registry = default_registry()
    return resolve_mapping(registry, source.mapping_id)


def read_pointer(data: bytes, address: int, size: int, endian: str) -> int | None:
    chunk = data[address : address + size]
    if len(chunk) < size:
        return None
    return int.from_bytes(chunk, "big" if endian == "big" else "little")


def pointer_bytes(value: int, size: int, endian: str) -> bytes:
    return (value & ((1 << (size * 8)) - 1)).to_bytes(
        size, "big" if endian == "big" else "little"
    )
