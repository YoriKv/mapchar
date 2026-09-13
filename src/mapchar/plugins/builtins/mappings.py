"""Built-in pointer mappings."""

from __future__ import annotations

from mapchar.core.mapping import (
    Banked,
    GameBoyMapping,
    GbaMapping,
    HiRom,
    Linear,
    LoRom,
    Relative,
)


def register(registry) -> None:
    for plugin in (
        Linear(),
        Relative(),
        LoRom(),
        HiRom(),
        GameBoyMapping(),
        GbaMapping(),
        Banked(0x4000, 0x8000, "banked"),
        Banked(0x4000, 0xC000, "nes_c000"),
        Banked(0x2000, 0x8000, "nes_8000_2000"),
    ):
        registry.register(plugin)
