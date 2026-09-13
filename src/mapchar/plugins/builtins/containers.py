"""ROM containers for the first release: flat files, iNES, SNES, GB, GBA."""

from __future__ import annotations

from typing import Any

from mapchar.core.context import (
    KEY_HEADER_SIZE,
    KEY_SOURCE_OFFSET,
    KEY_SUGGESTED_MAPPING,
    PipelineContext,
)
from mapchar.plugins.base import PluginInfo, ReadSource, Stage, WriteTarget

NES_MAGIC = b"NES\x1a"
GB_LOGO = bytes.fromhex("CEED6666CC0D000B")
GBA_LOGO = bytes.fromhex("24FFAE51699AA221")


class _Flat:
    """A container whose payload is the whole file: no header, no offset.

    ``mapping`` names the pointer mapping the format implies, if any.
    """

    def mapping(self, source: ReadSource) -> str | None:
        return None

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        ctx.set(KEY_SOURCE_OFFSET, 0)
        ctx.set(KEY_HEADER_SIZE, 0)
        suggested = self.mapping(source)
        if suggested is not None:
            ctx.set(KEY_SUGGESTED_MAPPING, suggested)
        return source.data

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        return data


class Raw(_Flat):
    info = PluginInfo("raw", "Flat file", Stage.CONTAINER, "Generic")


class INes:
    info = PluginInfo(
        "ines",
        "NES (iNES header)",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".nes",),
        magic=((0, NES_MAGIC),),
        min_size=16,
    )

    @staticmethod
    def _header_size(data: bytes) -> int:
        trainer = len(data) > 6 and data[6] & 0x04
        return 16 + (512 if trainer else 0)

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        size = self._header_size(source.data)
        ctx.set(KEY_SOURCE_OFFSET, size)
        ctx.set(KEY_HEADER_SIZE, size)
        ctx.set(KEY_SUGGESTED_MAPPING, "banked")
        return source.data[size:]

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        size = self._header_size(target.existing)
        return target.existing[:size] + data

    def describe(self, source: ReadSource, ctx: PipelineContext) -> dict[str, Any]:
        d = source.data
        return {
            "PRG ROM": f"{d[4]} × 16 KiB",
            "CHR ROM": f"{d[5]} × 8 KiB",
            "Mapper": (d[6] >> 4) | (d[7] & 0xF0),
            "Trainer": bool(d[6] & 0x04),
        }


class SnesHeadered:
    info = PluginInfo(
        "snes_headered",
        "SNES (copier header)",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".smc", ".sfc", ".swc", ".fig"),
        min_size=512,
        size_multiple=1024,
        size_remainder=512,
    )

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        ctx.set(KEY_SOURCE_OFFSET, 512)
        ctx.set(KEY_HEADER_SIZE, 512)
        ctx.set(KEY_SUGGESTED_MAPPING, _snes_mapping(source.data[512:]))
        return source.data[512:]

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        return target.existing[:512] + data


class Snes(_Flat):
    info = PluginInfo(
        "snes",
        "SNES",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".sfc", ".smc"),
        min_size=0x8000,
        size_multiple=1024,
    )

    def mapping(self, source: ReadSource) -> str | None:
        return _snes_mapping(source.data)


def _snes_mapping(rom: bytes) -> str:
    """LoROM or HiROM, from whichever internal header's checksum pair adds up."""

    def valid(at: int) -> bool:
        if len(rom) < at + 0x20:
            return False
        comp = int.from_bytes(rom[at + 0x1C : at + 0x1E], "little")
        chk = int.from_bytes(rom[at + 0x1E : at + 0x20], "little")
        return (comp ^ chk) == 0xFFFF

    if valid(0xFFC0) and not valid(0x7FC0):
        return "hirom"
    return "lorom"


class GameBoy(_Flat):
    info = PluginInfo(
        "gb",
        "Game Boy / Color",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".gb", ".gbc", ".sgb"),
        magic=((0x104, GB_LOGO),),
        min_size=0x150,
    )

    def mapping(self, source: ReadSource) -> str | None:
        return "gb"


class GameBoyAdvance(_Flat):
    info = PluginInfo(
        "gba",
        "Game Boy Advance",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".gba",),
        magic=((0x04, GBA_LOGO),),
        min_size=0xC0,
    )

    def mapping(self, source: ReadSource) -> str | None:
        return "gba"


def register(registry) -> None:
    # Detection priority: magic-bearing containers first, then size rules,
    # then the flat file as the last resort.
    for plugin in (INes(), GameBoy(), GameBoyAdvance(), SnesHeadered(), Snes(), Raw()):
        registry.register(plugin)
