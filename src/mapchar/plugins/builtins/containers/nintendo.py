"""Nintendo's containers: iNES, the three SNES framings, Game Boy, GBA and N64.

Each strips whatever its format puts in front of the image — an iNES header and
trainer, a copier header, an interleave — and puts it back on the way out, so
the offsets the app quotes are offsets into the image the console sees.
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
    publish_hints,
    slot_write,
    splice,
)

NES_MAGIC = b"NES\x1a"
GB_LOGO = bytes.fromhex("CEED6666CC0D000B")
GBA_LOGO = bytes.fromhex("24FFAE51699AA221")


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

    def header_size(self, source: ReadSource | None = None) -> int:
        """16, plus 512 where a trainer is flagged; 0 for a file that is not iNES.

        Asked without bytes it answers the header every iNES image has.
        """
        if source is None:
            return INES_HEADER
        data = source.data
        if data[:4] != NES_MAGIC or len(data) < INES_HEADER:
            return 0
        return INES_HEADER + (INES_TRAINER if data[6] & 0x04 else 0)

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
        if source is not None and not self.header_size(source):
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

    def header_size(self, source: ReadSource | None = None) -> int:
        return COPIER_HEADER

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
        if source is None:
            return None
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

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
        if source is None:
            return None
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

    def header_size(self, source: ReadSource | None = None) -> int:
        if source is None:
            return 0
        return copier_header_size(len(source.data))

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
        # Interleaving is the HiROM copier layout; LoROM images never had one.
        return "hirom"

    def read(self, source: ReadSource, ctx: PipelineContext) -> bytes:
        header = self.header_size(source)
        body = source.data[header:]
        bank = 2 * self._HALF
        banks = len(body) // bank
        tail = len(body) - banks * bank
        if tail:
            ctx.note(
                f"Dropped {tail} trailing byte(s): not a whole 64 KiB bank",
                detail="The upper/lower split is defined across whole banks, so "
                "a partial one cannot be placed. A save leaves those bytes "
                "exactly as they are.",
                source=self.info.id,
            )
        if not banks:
            ctx.note(
                "No complete 64 KiB bank: nothing to show",
                detail="This file is smaller than one bank, so there is nothing "
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
        body = len(source.data) - header
        bank = 2 * self._HALF
        banks = body // bank
        tail = body - banks * bank
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
            ContainerField(
                "Trailing bytes",
                f"{tail} (dropped)" if tail else "none",
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

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
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

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
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

    def header_size(self, source: ReadSource | None = None) -> int:
        return 0

    def default_mapping(self, source: ReadSource | None = None) -> str | None:
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
                " ".join(f"{byte:02X}" for byte in head) or "file is empty",
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
