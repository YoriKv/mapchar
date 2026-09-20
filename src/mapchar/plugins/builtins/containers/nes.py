"""The NES container: a ``.nes`` file behind its iNES header.

The header, and the 512-byte trainer it may flag, are stripped on read and put
back on write, so the offsets the app quotes are offsets into the image the
console sees.
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
    format_size,
    publish_hints,
    slot_write,
    splice,
)

NES_MAGIC = b"NES\x1a"


INES_HEADER = 16
INES_TRAINER = 512

KEY_INES_SOURCE = "ines.source"
"""The image a read came from, so a write with nothing to splice into can
rebuild the cartridge instead of emitting a bare body. Container-local."""


class INes:
    """A ``.nes`` file, read past the iNES header and written back behind it.

    A save recomputes the header size from the destination's own bytes, and
    with nothing at the destination rebuilds from the image the read stashed:
    the body alone is not a cartridge and will not reopen as one.
    """

    info = PluginInfo(
        "ines",
        "NES (iNES header)",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".nes",),
        magic=((0, NES_MAGIC),),
        min_size=INES_HEADER,
    )

    def header_size(self, source: ReadSource) -> int:
        """16, plus 512 where a trainer is flagged; 0 for a file that is not iNES."""
        data = source.data
        if data[:4] != NES_MAGIC or len(data) < INES_HEADER:
            return 0
        return INES_HEADER + (INES_TRAINER if data[6] & 0x04 else 0)

    def default_mapping(self, source: ReadSource) -> str | None:
        if not self.header_size(source):
            return None
        return "banked"

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        size = self.header_size(source)
        if not size:
            ctx.note(
                "Not an iNES file: read as plain bytes",
                detail="The first four bytes are not NES\\x1a, so there is no "
                "header to skip. The whole file is read as it lies.",
                source=self.info.id,
            )
        publish_hints(ctx, size, size, self.default_mapping(source))
        # What a save with nothing at the destination has to rebuild from: the
        # header, and any trainer, that the body on its own is missing.
        ctx.set(KEY_INES_SOURCE, source.data)
        return source.data[size:]

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        if not target.whole_file:
            return slot_write(data, target)
        existing = target.existing
        if not existing:
            stashed = ctx.get(KEY_INES_SOURCE)
            if isinstance(stashed, (bytes, bytearray)):
                existing = bytes(stashed)
        size = self.header_size(ReadSource(existing, target.paths))
        if not size:
            # No header to preserve, here or on the way in: the payload is the
            # file, which is what the read's plain fallback handed over.
            return data
        return splice(existing, size, data)

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        data = source.data
        size = self.header_size(source)
        if not size:
            return (
                ContainerField(
                    "Header",
                    "not an iNES image",
                    "The first four bytes are not NES\\x1a, so the file is "
                    "read whole, exactly as a flat file would be.",
                ),
            )
        trainer = bool(data[6] & 0x04)
        return (
            ContainerField(
                "Header",
                f"iNES, {size} bytes",
                "Skipped on read and preserved on write, so a save leaves "
                "the cartridge's own metadata alone.",
            ),
            ContainerField(
                "Trainer",
                "present, 512 bytes" if trainer else "none",
                "Flag 6 bit 2. Counted into the header, so a trainer that "
                "went unnoticed would put every offset 512 bytes out.",
            ),
            ContainerField(
                "PRG ROM",
                f"{data[4]} x 16 KiB ({format_size(data[4] * 0x4000)} total)",
                "Program ROM, where a NES game's text lives.",
            ),
            ContainerField(
                "CHR ROM",
                f"{data[5]} x 8 KiB ({format_size(data[5] * 0x2000)} total)"
                if data[5]
                else "0 - CHR-RAM cartridge",
                "Tile ROM, after the program banks. Not text, but it is "
                "part of the payload this container hands on.",
            ),
            ContainerField(
                "Mapper",
                str((data[6] >> 4) | (data[7] & 0xF0)),
                "The bank hardware the cartridge uses, which is what the "
                "banked mapping's bank numbers mean.",
            ),
            ContainerField(
                "Payload",
                f"{size:#06x} to end of file",
                "Everything past the header, the banked mapping applying "
                "to it with the header subtracted.",
            ),
        )
