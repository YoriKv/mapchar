"""The SNES containers: the three framings a cartridge dump comes in.

A copier header in front of the image, nothing in front of it at all, or the
HiROM interleave a copier wrote — each stripped or undone on read and put back
on write, so the offsets the app quotes are offsets into the image the console
sees. Which of the two internal-header locations looks like a real one is what
tells LoROM from HiROM, and both of the plain containers report it.
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
    format_size,
    note_partial_units,
    publish_hints,
    slot_write,
    splice,
    trailing_field,
    whole_units,
)

COPIER_HEADER = 512
"""The header 1980s-90s cartridge copiers wrote in front of a SNES dump."""
COPIER_SIZE_MULTIPLE = 1024
COPIER_MIN_SIZE = COPIER_HEADER + 0x8000
"""The floor under the size rule: a cart is at least 32 KiB, and a small
extracted chunk named ``.sfc`` is "512 over zero KiB" like every headered dump."""


def copier_header_size(size: int) -> int:
    """512 when a file is that much over a whole number of KiB, else 0.

    The only rule that spots a copier header — it carries no marker — and shared
    by both directions so they cannot disagree about where the image starts.
    """
    return COPIER_HEADER if size % COPIER_SIZE_MULTIPLE == COPIER_HEADER else 0


class SnesHeadered:
    """A SNES dump behind a copier header, read past it and written behind it.

    The 512 bytes are copier metadata this container never decodes, so a save
    preserves them and the file stays the dump it was.
    """

    info = PluginInfo(
        "snes_headered",
        "SNES (copier header)",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".smc", ".sfc", ".swc", ".fig"),
        min_size=COPIER_MIN_SIZE,
        size_multiple=COPIER_SIZE_MULTIPLE,
        size_remainder=COPIER_HEADER,
    )

    def header_size(self, source: ReadSource) -> int:
        return COPIER_HEADER

    def default_mapping(self, source: ReadSource) -> str | None:
        return _snes_mapping(source.data[COPIER_HEADER:])

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        if not copier_header_size(len(source.data)):
            # Detection would not have picked this container for such a file, so
            # it was chosen by hand — possibly by mistake, and the cost is 512
            # real bytes going missing off the front of the image.
            ctx.note(
                "This file does not look headered",
                detail="A copier header leaves the file 512 bytes over a whole "
                "number of KiB, and this one is not. Skipping 512 bytes anyway "
                "takes real data off the front of the image.",
                source=self.info.id,
            )
        publish_hints(ctx, COPIER_HEADER, COPIER_HEADER, self.default_mapping(source))
        return source.data[COPIER_HEADER:]

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        if not target.whole_file:
            return slot_write(data, target)
        return splice(target.existing, COPIER_HEADER, data)

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        size = len(source.data)
        return (
            ContainerField(
                "File length",
                f"{size} ({format_size(size)})",
                "The whole of what identifies a copier header: a cart is a "
                "whole number of KiB, so 512 over one has 512 in front.",
            ),
            ContainerField(
                "Size rule",
                "matches - headered" if copier_header_size(size) else "does not match",
                "512 bytes are skipped either way, this container having "
                "been chosen; where the rule misses, that is real data.",
            ),
            ContainerField(
                "Copier header",
                f"{COPIER_HEADER} bytes, skipped",
                "Copier metadata, never decoded and preserved on write. "
                "Skipping it lines every published ROM offset up.",
            ),
            *_snes_header_fields(source.data[COPIER_HEADER:]),
        )


class Snes(_Flat):
    info = PluginInfo(
        "snes",
        "SNES",
        Stage.CONTAINER,
        "Nintendo",
        extensions=(".sfc", ".smc"),
        min_size=0x8000,
        size_multiple=COPIER_SIZE_MULTIPLE,
    )

    def default_mapping(self, source: ReadSource) -> str | None:
        return _snes_mapping(source.data)

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        return (
            ContainerField(
                "Payload",
                f"the whole file, {format_size(len(source.data))}",
                "A headerless dump is the cartridge image itself, so a "
                "file offset is a ROM offset.",
            ),
            *_snes_header_fields(source.data),
        )


class SnesInterleaved:
    """An interleaved SNES HiROM image, restored to contiguous ROM bytes.

    Game Doctor / Super UFO copiers stored a HiROM image with the upper 32 KiB
    half of every 64 KiB bank first and then all the lower halves, which is
    what puts the internal header at the LoROM-style offset ``0x7FC0``. A
    copier header is skipped first where the size rule says there is one.

    LoROM images were never interleaved and this container always
    deinterleaves, so it is picked by hand: nothing in an interleaved file says
    that it is one, and deinterleaving a plain image scrambles it.
    """

    # Neither a suffix nor a signature, so detection never reaches it, and it
    # registers after the flat file so a scoreless tie cannot hand it one.
    info = PluginInfo(
        "snes_interleaved",
        "SNES interleaved (deinterleave)",
        Stage.CONTAINER,
        "Nintendo",
    )

    _HALF = 0x8000  # half of a 64 KiB HiROM bank

    def header_size(self, source: ReadSource) -> int:
        return copier_header_size(len(source.data))

    def default_mapping(self, source: ReadSource) -> str | None:
        # Interleaving is the HiROM copier layout; LoROM images never had one.
        return "hirom"

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        header = self.header_size(source)
        body = source.data[header:]
        bank = 2 * self._HALF
        banks, tail = whole_units(len(body), bank)
        note_partial_units(
            ctx,
            banks,
            tail,
            unit="64 KiB bank",
            detail_tail="The upper/lower split is defined across whole banks, so "
            "a partial one cannot be placed. A save leaves those bytes "
            "exactly as they are.",
            detail_none="This file is smaller than one bank, so there is nothing "
            "to deinterleave. It is probably not an interleaved image.",
            source=self.info.id,
        )
        publish_hints(ctx, header, header, self.default_mapping(source))
        half, lowers = self._HALF, banks * self._HALF
        out = bytearray()
        for i in range(banks):
            out += body[lowers + i * half : lowers + (i + 1) * half]
            out += body[i * half : (i + 1) * half]
        return bytes(out)

    def write(self, data: bytes, target: WriteTarget, ctx: PipelineContext) -> bytes:
        if not target.whole_file:
            return slot_write(data, target)
        half = self._HALF
        bank = 2 * half
        banks = len(data) // bank
        body = bytearray(banks * bank)
        lowers = banks * half
        for i in range(banks):
            at = i * bank
            body[i * half : (i + 1) * half] = data[at + half : at + bank]
            body[lowers + i * half : lowers + (i + 1) * half] = data[at : at + half]
        header = copier_header_size(len(target.existing))
        return splice(target.existing, header, bytes(body))

    def describe(
        self, source: ReadSource, ctx: PipelineContext
    ) -> tuple[ContainerField, ...]:
        header = self.header_size(source)
        bank = 2 * self._HALF
        banks, tail = whole_units(len(source.data) - header, bank)
        return (
            ContainerField(
                "Copier header",
                f"{header} bytes, skipped" if header else "none",
                "Spotted by the same size rule the headered container "
                "uses, and skipped before the halves are woven back.",
            ),
            ContainerField(
                "Deinterleaved banks",
                f"{banks} x 64 KiB ({format_size(banks * bank)})",
                "Every bank's upper half sits in the first region of the "
                "file and every lower half in the second.",
            ),
            trailing_field(
                tail,
                "A partial bank cannot be placed, so it is not shown here "
                "and a save leaves it as it is.",
            ),
        )


def _snes_mapping(rom: bytes) -> str:
    """LoROM or HiROM, from whichever internal header looks more like one.

    Two things say: the checksum and its complement add to ``0xFFFF``, and the
    map mode byte's low bit is set for HiROM. A dump with a wrong checksum
    still has its mode byte, and a header-shaped run of bytes at the other
    location still has to disagree with one of the two.

    Which is why the mode byte only counts when it **is** one. A real map mode
    is ``0x2x`` or ``0x3x``, and the other location is usually padding: a zero
    there reads as "bit clear", so crediting it would let padding vote LoROM
    with exactly the weight of a genuine byte — and a HiROM whose checksum pair
    is broken would tie, which a tie hands to LoROM.
    """

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

    # A tie goes to LoROM: it is the commoner layout, and the answer is a
    # suggestion for a new block rather than something a read depends on.
    return "hirom" if score(0xFFC0, True) > score(0x7FC0, False) else "lorom"


def _snes_header_fields(rom: bytes) -> tuple[ContainerField, ...]:
    """The internal header both SNES containers report: where it is and what
    mapping that makes."""
    mapping = _snes_mapping(rom)
    at = 0xFFC0 if mapping == "hirom" else 0x7FC0
    title = "too short to hold one"
    if len(rom) >= at + 0x15:
        raw = rom[at : at + 0x15]
        title = "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in raw).strip()
    return (
        ContainerField(
            "Internal header",
            f"{at:#08x} ({title})",
            "The cartridge's own header, at 0x7FC0 in a LoROM image and "
            "0xFFC0 in a HiROM one - which is what tells the two apart.",
        ),
        ContainerField(
            "Mapping",
            mapping,
            "Suggested for a new block: the checksum pair and the map mode "
            "byte agree on this layout. Any mapping can be chosen instead.",
        ),
    )
