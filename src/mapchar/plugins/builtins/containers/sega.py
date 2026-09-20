"""Sega's container: the Super Magic Drive interleaved Mega Drive image."""

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
    note_partial_units,
    publish_hints,
    slot_write,
    splice,
    trailing_field,
    whole_units,
)


class Smd:
    """A Sega ``.smd`` dump, deinterleaved to contiguous Mega Drive bytes.

    ``.smd`` has a 512-byte copier header and then 16 KiB blocks storing all
    their odd bytes and then all their even ones. Each block is woven back
    together on its own, so the halves never reach across a boundary, and the
    write is the exact inverse. Plain ``.md``/``.bin`` dumps are not
    interleaved and want the flat file instead.
    """

    # Suffix only: the copier header carries no marker to assert on.
    info = PluginInfo(
        "smd",
        "Mega Drive (.smd, deinterleave)",
        Stage.CONTAINER,
        "Sega",
        extensions=(".smd",),
    )

    _HEADER = 512
    _BLOCK = 16384
    _HALF = 8192

    def header_size(self, source: ReadSource) -> int:
        return self._HEADER

    def default_mapping(self, source: ReadSource) -> str | None:
        # The 68000 sees the cartridge from address 0, so a pointer is an
        # offset into the deinterleaved image.
        return "linear"

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        body = source.data[self._HEADER :]
        block, half = self._BLOCK, self._HALF
        blocks, tail = whole_units(len(body), block)
        note_partial_units(
            ctx,
            blocks,
            tail,
            unit="16 KiB block",
            detail_tail="The odd/even split is per block, so a partial one "
            "cannot be reassembled. A save leaves those bytes as they are.",
            detail_none="Past the 512-byte header this file has less than one "
            "whole block, so there is nothing to reassemble. It may not "
            "be a .smd at all.",
            source=self.info.id,
        )
        publish_hints(ctx, self._HEADER, self._HEADER, self.default_mapping(source))
        out = bytearray(blocks * block)
        for i in range(blocks):
            at = i * block
            out[at + 1 : at + block : 2] = body[at : at + half]  # first half: odd
            out[at : at + block : 2] = body[at + half : at + block]  # second: even
        return bytes(out)

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        if not target.whole_file:
            return slot_write(data, target)
        block, half = self._BLOCK, self._HALF
        blocks = len(data) // block
        body = bytearray(blocks * block)
        for i in range(blocks):
            at = i * block
            body[at : at + half] = data[at + 1 : at + block : 2]  # odd: first half
            body[at + half : at + block] = data[at : at + block : 2]  # even: second
        return splice(target.existing, self._HEADER, bytes(body))

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        body = max(0, len(source.data) - self._HEADER)
        blocks, tail = whole_units(body, self._BLOCK)
        return (
            ContainerField(
                "Copier header",
                f"{self._HEADER} bytes, skipped",
                "The .smd wrapper's own metadata, never decoded and "
                "preserved as it stands on write.",
            ),
            ContainerField(
                "Deinterleaved blocks",
                f"{blocks} x 16 KiB ({format_size(blocks * self._BLOCK)})",
                "Each block holds all its odd bytes and then all its even "
                "ones, woven back together one block at a time.",
            ),
            trailing_field(
                tail,
                "A partial block cannot be reassembled, so it is not shown "
                "here and a save leaves it as it is.",
            ),
        )
