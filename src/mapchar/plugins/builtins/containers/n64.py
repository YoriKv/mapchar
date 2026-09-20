"""The Nintendo 64 container: one image in three byte orders.

``.z64`` is native, ``.v64`` has every byte pair reversed and ``.n64`` every
4-byte word; the read normalises to native, which is what every published N64
offset is quoted in, and the write restores the order the file arrived in.
"""

from __future__ import annotations

from mapchar.core.bits import format_hex_bytes
from mapchar.core.context import PipelineContext
from mapchar.plugins.base import (
    ContainerField,
    PluginInfo,
    ReadSource,
    Stage,
    WriteTarget,
)
from mapchar.plugins.builtins.containers._common import splice

KEY_N64_SWAP = "n64.swap_width"
"""The group width whose reversal converts this file between its on-disk order
and native order (2 for ``.v64``, 4 for ``.n64``; 0 already native). Set by the
read so the write can restore the order the file arrived in. Container-local."""

N64_NATIVE = b"\x80\x37\x12\x40"
N64_BYTESWAPPED = b"\x37\x80\x40\x12"  # .v64 - 2-byte groups
N64_LITTLE = b"\x40\x12\x37\x80"  # .n64 - 4-byte groups
N64_ORDERS: dict[bytes, int] = {N64_NATIVE: 0, N64_BYTESWAPPED: 2, N64_LITTLE: 4}
"""Signature to the group width that normalises a dump in that order."""


def swap_groups(data: bytes, width: int) -> bytes:
    """``data`` with every whole ``width``-byte group reversed (``width`` 0: as is).

    Written as ``width`` strided assignments rather than a loop over the groups:
    an N64 image is tens of megabytes. A trailing partial group is left alone,
    so a truncated dump keeps the bytes it has.
    """
    if width < 2:
        return data
    whole = len(data) - len(data) % width
    out = bytearray(data)
    body = data[:whole]  # sliced once, not once per stride
    for i in range(width):
        out[i:whole:width] = body[width - 1 - i :: width]
    return bytes(out)


def n64_swap_width(head: bytes) -> int:
    """The normalising width for a dump starting with ``head``; 0 if unknown."""
    return N64_ORDERS.get(bytes(head[:4]), 0)


class N64Rom:
    """A Nintendo 64 dump, normalised to native byte order and written back.

    The same ROM exists in three orders — ``.z64`` big-endian, ``.v64`` with
    every 2-byte pair reversed, ``.n64`` with every 4-byte word reversed — and
    every published offset is quoted in the native one. The read normalises so
    tables and pointers mean what the documentation says; the write restores
    the order the file arrived in, so a ``.v64`` stays a ``.v64``.

    Reversing a group twice restores it, so one width describes both
    directions. It rides on the context rather than being re-derived at save
    time: by then the bytes in hand are normalised and no longer say.
    """

    info = PluginInfo(
        "n64",
        "Nintendo 64 (byte order)",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".z64", ".v64", ".n64"),
        magic=tuple((0, sig) for sig in N64_ORDERS),
        min_size=0x40,
    )

    def header_size(self, source: ReadSource) -> int:
        return 0

    def default_mapping(self, source: ReadSource) -> str | None:
        # A game's pointers address whatever segment it DMA'd into RAM, which
        # differs per game, so there is nothing honest to suggest.
        return None

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        width = n64_swap_width(source.data)
        if bytes(source.data[:4]) not in N64_ORDERS:
            ctx.note(
                "Unrecognised N64 header: assuming native byte order",
                detail="The first four bytes match none of the three known "
                "orders, so the file is read (and written) unswapped. If the "
                "text looks byte-swapped, this is why.",
                source=self.info.id,
            )
        ctx.set(KEY_N64_SWAP, width)
        # No header published, like every container that strips nothing: the
        # swap moves bytes inside their group but adds none in front of them.
        return swap_groups(source.data, width)

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        # The read recorded the order; without one, the destination's own bytes
        # still say, and a file that is not there yet is written native.
        width = ctx.get(KEY_N64_SWAP)
        if width is None:
            width = n64_swap_width(target.existing)
        # Spliced in native order and swapped back whole, never spliced into the
        # on-disk order: an offset that is not group-aligned names different
        # bytes in the two, and native is what everything else addressed.
        native = splice(swap_groups(target.existing, width), target.offset, data)
        return swap_groups(native, width)

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        head = bytes(source.data[:4])
        width = n64_swap_width(head)
        orders = {
            0: ".z64 big-endian (native)",
            2: ".v64 byteswapped",
            4: ".n64 little-endian",
        }
        order = orders[width] if head in N64_ORDERS else "unrecognised - assumed native"
        return (
            ContainerField(
                "Header bytes",
                format_hex_bytes(head) or "file is empty",
                "The same four header bytes in whichever order this dump was "
                "made in - the only thing that says which, the suffix being "
                "no guarantee.",
            ),
            ContainerField(
                "Byte order",
                order,
                "The read normalises to native order, which is what every "
                "published N64 offset is quoted in.",
            ),
            ContainerField(
                "Swap width",
                f"{width}-byte groups reversed" if width else "none - read as it lies",
                "Carried forward so a save can restore the order the file "
                "arrived in, the normalised bytes no longer saying which.",
            ),
        )
