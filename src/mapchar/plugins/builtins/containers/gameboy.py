"""Nintendo's handheld containers: Game Boy / Color and Game Boy Advance.

Neither strips anything — a cartridge dump is the image — so both are here for
what they know about the file rather than for a header they remove: the Game
Boy's two checksums, which every text edit makes stale and which a save repairs,
and the boot logos that say a file is a cartridge dump where its suffix cannot.
"""

from __future__ import annotations

from mapchar.core.context import PipelineContext
from mapchar.plugins.base import (
    ContainerField,
    PluginInfo,
    ReadSource,
    Stage,
    WriteTarget,
)
from mapchar.plugins.builtins.containers._common import (
    _Flat,
    _sum_value,
    format_size,
    slot_write,
)

GB_LOGO = bytes.fromhex("CEED6666CC0D000B")
GBA_LOGO = bytes.fromhex("24FFAE51699AA221")


GB_SUM_RANGE = (0x134, 0x14D)
"""``[start, end)`` — the header fields the boot ROM's checksum covers."""
GB_HEADER_SUM_AT = 0x14D
GB_GLOBAL_SUM_AT = 0x14E
GB_HEADER_END = 0x150
"""The smallest file that has a cartridge header at all."""


def repair_gb_checksums(rom: bytes) -> bytes:
    """``rom`` with its two checksums recomputed; a headerless file untouched.

    The **header** checksum at ``0x14D`` is ``x = x - byte - 1`` over the title
    and cartridge fields; the boot ROM refuses to run a cartridge whose copy
    disagrees, so an edited ROM can come back as a blank screen. The **global**
    checksum at ``0x14E`` is the big-endian sum of every other byte, which
    every text edit makes stale; no boot ROM checks it, but tooling does.
    """
    if len(rom) < GB_HEADER_END:
        return rom
    out = bytearray(rom)
    header_sum = 0
    for byte in out[GB_SUM_RANGE[0] : GB_SUM_RANGE[1]]:
        header_sum = (header_sum - byte - 1) & 0xFF
    out[GB_HEADER_SUM_AT] = header_sum
    # The global sum covers the whole ROM but its own two bytes, so they are
    # zeroed first; subtracting the old value would carry a wrong one forward.
    out[GB_GLOBAL_SUM_AT : GB_GLOBAL_SUM_AT + 2] = b"\x00\x00"
    total = sum(out) & 0xFFFF
    out[GB_GLOBAL_SUM_AT : GB_GLOBAL_SUM_AT + 2] = total.to_bytes(2, "big")
    return bytes(out)


class GameBoy(_Flat):
    """A Game Boy ROM: read as it lies, written with its checksums repaired."""

    info = PluginInfo(
        "gb",
        "Game Boy / Color",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".gb", ".gbc", ".sgb"),
        magic=((0x104, GB_LOGO),),
        min_size=GB_HEADER_END,
    )

    def default_mapping(self, source: ReadSource) -> str | None:
        return "gb"

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        # Repaired after the edit, so what is summed is the file as it will be.
        return repair_gb_checksums(slot_write(data, target))

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        data = source.data
        logo = data[0x104 : 0x104 + len(GB_LOGO)] == GB_LOGO
        fields = [
            ContainerField(
                "Boot logo",
                "matches" if logo else "does not match",
                "The bitmap at 0x104 the boot ROM compares against, which "
                "says this is a cartridge dump where the suffix cannot.",
            ),
            ContainerField(
                "Payload",
                f"the whole file, {format_size(len(data))}",
                "Nothing is stripped: a GB ROM's bytes are read where they "
                "lie. This container is here for its write half.",
            ),
        ]
        if len(data) < GB_HEADER_END:
            fields.append(
                ContainerField(
                    "Checksums",
                    "no header to repair",
                    f"A file under {GB_HEADER_END:#x} bytes holds no cartridge "
                    "header, so a save writes it through as it is.",
                )
            )
            return tuple(fields)
        repaired = repair_gb_checksums(bytes(data))
        fields.append(
            ContainerField(
                "Header checksum",
                _sum_value(data[GB_HEADER_SUM_AT], repaired[GB_HEADER_SUM_AT], "02X"),
                "The byte at 0x14D over the title and cartridge fields. The "
                "boot ROM refuses a cartridge whose copy is wrong.",
            )
        )
        fields.append(
            ContainerField(
                "Global checksum",
                _sum_value(
                    int.from_bytes(
                        data[GB_GLOBAL_SUM_AT : GB_GLOBAL_SUM_AT + 2], "big"
                    ),
                    int.from_bytes(
                        repaired[GB_GLOBAL_SUM_AT : GB_GLOBAL_SUM_AT + 2], "big"
                    ),
                    "04X",
                ),
                "The word at 0x14E, the sum of every other byte. Text lives "
                "inside it, so every edit makes it stale.",
            )
        )
        return tuple(fields)


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

    def default_mapping(self, source: ReadSource) -> str | None:
        return "gba"

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        data = source.data
        logo = data[0x04 : 0x04 + len(GBA_LOGO)] == GBA_LOGO
        title = "too short to hold one"
        if len(data) >= 0xB0:
            raw = data[0xA0:0xAC]
            title = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in raw).strip()
        return (
            ContainerField(
                "Nintendo logo",
                "matches" if logo else "does not match",
                "The bitmap at 0x04 the BIOS compares against, which says "
                "this is a cartridge dump where the suffix cannot.",
            ),
            ContainerField(
                "Title",
                title,
                "The game title at 0xA0, twelve ASCII bytes. Read only to "
                "show here; nothing depends on it.",
            ),
            ContainerField(
                "Payload",
                f"the whole file, {format_size(len(data))}",
                "Nothing is stripped: the cartridge is mapped whole at "
                "0x08000000, which is what the GBA mapping subtracts.",
            ),
        )
