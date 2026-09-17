"""Lay a block's strings out in place.

Encodes every string that carries a replacement text and keeps the bytes of
every other, places the results in *slotted* or *packed* mode, and refuses the
whole block when anything crosses its bound. The output is byte splices over
the decompressed buffer; nothing is written here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mapchar.core.bits import Bits, align_up
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    Pascal,
    StringRecord,
    WriteMode,
    block_bound,
)
from mapchar.core.errors import EncodeError
from mapchar.core.mapping import pointer_bytes
from mapchar.core.table import TableSet
from mapchar.engines.decode import DecodeRules, decode
from mapchar.engines.encode import encode
from mapchar.pipeline.extract import strip_artificial
from mapchar.plugins.registry import mapping_for


@dataclass(frozen=True)
class Splice:
    offset: int
    data: bytes

    @property
    def end(self) -> int:
        return self.offset + len(self.data)


@dataclass
class Problem:
    index: int
    message: str
    over: int = 0
    """Bytes over the available room, when that is the problem."""


@dataclass
class Encoded:
    index: int
    data: bytes
    """The string's bytes, Pascal prefix included, before padding."""
    problem: Problem | None = None
    new_start: int | None = None
    """Where the layout put these bytes; ``None`` until one has.

    Set by :func:`layout_block` as it packs, and read back by the pointer
    rewrite: a pointer to a string that moved is the offset the layout chose,
    which nothing else knows.
    """


@dataclass
class LayoutResult:
    splices: list[Splice] = field(default_factory=list)
    problems: list[Problem] = field(default_factory=list)
    encoded: dict[int, Encoded] = field(default_factory=dict)
    used: int = 0
    """Bytes the packed layout occupies, or the sum of slot use when slotted."""
    available: int = 0

    @property
    def ok(self) -> bool:
        return not self.problems


def encode_string(
    rec: StringRecord, config: BlockConfig, tables: TableSet, data: bytes
) -> Encoded:
    """One string's bytes: the bytes as they are, or its replacement encoded.

    An encoding that ends part-way through a byte is padded with zero bits to
    the byte, as Atlas pads and as the ROMs the bit-level tables describe are
    padded: the next string, or the pointer to it, begins on a byte.
    """
    if rec.replacement is None:
        return Encoded(
            rec.index, b"".join(data[a:b] for a, b in rec.pieces(config.skips))
        )
    st = config.string_type
    fixed = config.fixed_length is not None
    text = rec.replacement
    try:
        if fixed:
            body = _encode_fixed(text, config, tables)
        elif isinstance(st, Pascal):
            payload = encode(text, tables, end_terminated=False)
            body = _pascal(payload.data, st, payload, text, tables)
        elif isinstance(st, Lines):
            body = _encode_lines(text, config, tables, st)
        else:
            result = encode(
                text, tables, end_terminated=True, ends=config.strings_per_pointer
            )
            if not result.ends_with_end and isinstance(st, EndToken):
                raise EncodeError("the text must end with an end token")
            body = result.data
    except EncodeError as exc:
        return Encoded(rec.index, b"", Problem(rec.index, str(exc)))
    return Encoded(rec.index, body)


def _encode_lines(text: str, config: BlockConfig, tables: TableSet, st: Lines) -> bytes:
    """A string of ``st.count`` line codes: the game reads that many lines, so
    the translation must hold exactly as many, whatever else it holds."""
    r = encode(text, tables, end_terminated=False)
    rules = DecodeRules(
        end_terminated=False, limit_bit=len(r.bits), line_label=config.line_label
    )
    lines = sum(t.newline for t in decode(Bits(r.data), tables, 0, rules).tokens)
    if lines != st.count:
        raise EncodeError(
            f"the text holds {lines} [{config.line_label}] code(s); "
            f"the block reads {st.count} per string"
        )
    return r.data


def _encode_fixed(text: str, config: BlockConfig, tables: TableSet) -> bytes:
    """A fixed-length string; fixed-line codes are dump formatting and go."""
    st = config.string_type
    length = config.fixed_length or 0
    stop_at_end = isinstance(st, FixedLength) and st.stop_at_end
    body = "".join(strip_artificial(text, config))
    r = encode(body, tables, end_terminated=stop_at_end)
    if len(r.data) > length:
        raise EncodeError(f"{len(r.data) - length} byte(s) too long for {length}")
    return r.data


def _pascal(payload: bytes, st: Pascal, result, text: str, tables: TableSet) -> bytes:
    if st.counts_tokens:
        r = decode(Bits(payload), tables, 0, DecodeRules(end_terminated=False))
        n = sum(t.pascal_weight for t in r.tokens)
    else:
        n = len(payload)
    if n >= 1 << (st.width * 8):
        raise EncodeError(f"length {n} does not fit a {st.width}-byte prefix")
    prefix = n.to_bytes(st.width, "big" if st.endian == "big" else "little")
    return prefix + payload


def slot_ends(
    strings: list[StringRecord],
    bound: int,
    data: bytes | None = None,
    fill: int | None = None,
) -> dict[int, int]:
    """Where each string's slot ends, by index.

    A slot is the bytes the string itself holds plus the run of the block's
    fill byte directly after them — the padding a shorter replacement left
    behind, which the next edit may use again — and stops at the next string
    in address order, at ``bound``, or at the end of ``data``, whichever comes
    first. Bytes between two strings that are not that padding belong to no
    slot: nothing the block writes may touch them.

    Without ``data`` and ``fill`` there is no telling padding from anything
    else, and a slot is the whole gap to the next string — the room a block
    whose strings sit end to end has, and what a surface with no bytes to hand
    reports; the layout, which has them, is the one that decides.
    """
    ordered = sorted(strings, key=lambda s: s.start)
    ends: dict[int, int] = {}
    limit = bound if data is None else min(bound, len(data))
    for i, rec in enumerate(ordered):
        stop = ordered[i + 1].start if i + 1 < len(ordered) else limit
        if data is None or fill is None:
            ends[rec.index] = max(rec.end, stop)
            continue
        end = rec.end
        while end < stop and end < len(data) and data[end] == fill:
            end += 1
        ends[rec.index] = end
    return ends


def layout_block(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    registry=None,
) -> LayoutResult:
    result = LayoutResult()
    if not strings:
        return result
    fill = bytes([config.fill])
    for rec in strings:
        enc = encode_string(rec, config, tables, data)
        result.encoded[rec.index] = enc
        if enc.problem is not None:
            result.problems.append(enc.problem)
    mode = config.effective_write_mode
    fixed_len = config.fixed_length

    if mode is WriteMode.SLOTTED:
        out = bytearray()
        first = min(s.start for s in strings)
        ends = slot_ends(strings, block_bound(config, strings), data, config.fill)
        last = min(max(max(s.end for s in strings), max(ends.values())), len(data))
        out[:] = data[first:last]
        used = 0
        for rec in strings:
            enc = result.encoded[rec.index]
            if enc.problem is not None:
                continue
            if _crosses_skip(rec, config):
                if rec.replacement is not None:
                    result.problems.append(
                        Problem(rec.index, "spans a skip range and cannot be rewritten")
                    )
                continue
            # Never past the slot the block gives the string (:func:`slot_ends`
            # — its own bytes and the padding after them, and nothing else),
            # whatever the fixed length says: ``out`` is a bytearray, and a
            # slice assignment longer than the slot would grow the buffer
            # rather than stop, writing over — or past — the string that
            # follows. Padding the chunk out to the slot writes the fill byte
            # where the fill byte already is, so an unedited string's bytes,
            # and every byte outside a slot, stay as they are.
            extent = ends[rec.index] - rec.start
            room = extent if fixed_len is None else min(fixed_len, extent)
            if fixed_len is not None and fixed_len > extent:
                result.problems.append(
                    Problem(
                        rec.index,
                        f"its slot holds {extent} byte(s), "
                        f"{fixed_len - extent} short of the fixed length",
                    )
                )
                continue
            if len(enc.data) > room:
                result.problems.append(
                    Problem(
                        rec.index,
                        f"{len(enc.data) - room} byte(s) too long for its slot",
                        len(enc.data) - room,
                    )
                )
                continue
            used += len(enc.data)
            chunk = enc.data + fill * (room - len(enc.data))
            at = rec.start - first
            out[at : at + room] = chunk
        result.used = used
        result.available = sum(
            (fixed_len if fixed_len is not None else ends[s.index] - s.start)
            for s in strings
        )
        if result.ok:
            result.splices.append(Splice(first, bytes(out)))
        return result

    # Packed: end to end from the first string, up to the bound.
    first = strings[0].start
    bound = block_bound(config, strings)
    pos = first
    out = bytearray()
    m, o = config.realign
    for rec in strings:
        enc = result.encoded[rec.index]
        if enc.problem is not None:
            continue
        aligned = align_up(pos, m, o)
        out += fill * (aligned - pos)
        pos = aligned
        if fixed_len is not None:
            chunk = enc.data + fill * (fixed_len - len(enc.data))
        else:
            chunk = enc.data
        if pos + len(chunk) > bound:
            over = pos + len(chunk) - bound
            result.problems.append(
                Problem(rec.index, f"crosses the block bound by {over} byte(s)", over)
            )
            pos += len(chunk)
            continue
        enc.new_start = pos
        out += chunk
        pos += len(chunk)
    result.used = pos - first
    result.available = bound - first
    if result.ok:
        out += fill * (bound - first - len(out))
        result.splices.append(Splice(first, bytes(out)))
        result.splices.extend(_pointer_splices(config, strings, result, registry))
    return result


def _pointer_splices(config, strings, result: LayoutResult, registry) -> list[Splice]:
    """Rewrite every pointer of a packed block to its string's new position."""
    if not config.has_pointers:
        return []
    source = config.source
    mapping = mapping_for(source, registry)
    if mapping is None:
        result.problems.append(Problem(-1, f"unknown mapping {source.mapping_id!r}"))
        return []
    splices = []
    for rec in strings:
        enc = result.encoded.get(rec.index)
        new_start = enc.new_start
        if new_start is None:
            continue
        for ref in rec.pointers:
            value = mapping.to_value(new_start - ref.offset, source.bank, ref.address)
            if value < 0 or value >= 1 << (ref.size * 8):
                result.problems.append(
                    Problem(
                        rec.index, f"pointer at ${ref.address:X} cannot hold ${value:X}"
                    )
                )
                continue
            splices.append(
                Splice(ref.address, pointer_bytes(value, ref.size, ref.endian))
            )
    return splices


def _crosses_skip(rec: StringRecord, config: BlockConfig) -> bool:
    """A string read across a skip range has no single byte extent."""
    return len(rec.pieces(config.skips)) > 1


def apply_splices(data: bytes, splices: list[Splice]) -> bytes:
    out = bytearray(data)
    for s in splices:
        out[s.offset : s.end] = s.data
    return bytes(out)


def room_for(
    rec: StringRecord,
    config: BlockConfig | None,
    bound: int,
    ends: dict[int, int] | None = None,
) -> int:
    """How many bytes ``rec`` may take: its slot (:func:`slot_ends`, which
    ``ends`` carries when the caller has them), the slot a fixed length gives
    it, or everything up to the block's ``bound``
    (:func:`~mapchar.core.block.block_bound`) when it is packed."""
    if config is None:
        return rec.length
    if isinstance(config.string_type, FixedLength):
        return config.string_type.length
    if config.effective_write_mode is WriteMode.PACKED:
        return max(bound - rec.start, 0)
    if _crosses_skip(rec, config):
        return rec.byte_length(config.skips)
    if ends is not None and rec.index in ends:
        return ends[rec.index] - rec.start
    return rec.byte_length(config.skips)


__all__ = [
    "LayoutResult",
    "Problem",
    "Splice",
    "apply_splices",
    "block_bound",
    "layout_block",
    "room_for",
    "slot_ends",
]
