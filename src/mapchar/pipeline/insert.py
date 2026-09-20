"""Lay a block's strings out in place.

Encodes every string that carries a replacement text and keeps the bytes of
every other, places the results in *slotted* or *packed* mode, and refuses the
whole block when anything crosses its bound. The output is byte splices over
the decompressed buffer; nothing is written here.
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from mapchar.core.bits import Bits, align_up
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NestedPointerSource,
    Pascal,
    StringRecord,
    WriteMode,
    block_bound,
    fill_run,
    string_groups,
)
from mapchar.core.errors import EncodeError
from mapchar.core.mapping import pointer_bytes
from mapchar.core.table import TableSet, TokenKind
from mapchar.engines.decode import DecodeRules, decode
from mapchar.engines.encode import encode
from mapchar.pipeline.extract import nested_records, strip_artificial
from mapchar.plugins.registry import default_registry, resolve_mapping


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
    body = "".join(strip_artificial(text, config))
    if isinstance(st, FixedLength) and st.stop_at_end:
        data = _encode_stopping(body, tables, length)
    else:
        data = encode(body, tables, end_terminated=False).data
    if len(data) > length:
        raise EncodeError(
            f"the text encodes to {len(data)} byte(s); the block's fixed "
            f"length is {length}, so {len(data) - length} do not fit"
        )
    return data


def _encode_stopping(body: str, tables: TableSet, length: int) -> bytes:
    """A fixed string that stops at an end token: the text, then an end token
    where there is room for one — the fill after it is the layout's.

    The end token is not text (:func:`~mapchar.pipeline.extract.is_hidden_end`),
    so the text does not spell it; one that does is written as it stands, and
    so is a text with more after its end token — a tail that is not fill, which
    the reading shows after a visible end token.
    """
    try:
        r = encode(body, tables, end_terminated=True)
    except EncodeError:
        return encode(body, tables, end_terminated=False).data
    if r.ends_with_end or len(r.data) >= length:
        return r.data
    for entry in tables.start.entries.values():
        if entry.kind is not TokenKind.END or not entry.text:
            continue
        try:
            ended = encode(body + entry.text, tables, end_terminated=True)
        except EncodeError:
            continue
        if ended.ends_with_end and len(ended.data) <= length:
            return ended.data
    return r.data


def _pascal(payload: bytes, st: Pascal, result, text: str, tables: TableSet) -> bytes:
    if st.counts_tokens:
        r = decode(Bits(payload), tables, 0, DecodeRules(end_terminated=False))
        n = sum(t.pascal_weight for t in r.tokens)
    else:
        n = len(payload)
    if n >= 1 << (st.width * 8):
        unit = "token" if st.counts_tokens else "byte"
        raise EncodeError(
            f"the text encodes to {n} {unit}(s); this block's {st.width}-byte "
            f"length prefix counts up to {(1 << (st.width * 8)) - 1}"
        )
    prefix = n.to_bytes(st.width, "big" if st.endian == "big" else "little")
    return prefix + payload


def slot_ends(
    strings: list[StringRecord],
    bound: int,
    data: bytes | None = None,
    fill: bytes | None = None,
) -> dict[int, int]:
    """Where each string's slot ends, by index.

    A slot is the bytes the string itself holds plus the run of whole fill
    patterns directly after them — the padding a shorter replacement left
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
        if data is None or not fill:
            ends[rec.index] = max(rec.end, stop)
            continue
        end = rec.end
        width = len(fill)
        while end + width <= min(stop, len(data)) and data[end : end + width] == fill:
            end += width
        ends[rec.index] = end
    return ends


def packed_ends(strings: list[StringRecord], bound: int, skips=()) -> dict[int, int]:
    """Where each string's room ends, by index, when the group is packed.

    A packed group is laid out afresh from its first string, so no string has
    a place of its own: its room is the bytes it holds now plus the group's
    **spare** — everything between what its strings hold altogether and the
    bound, whether it lies in the gaps a shortened string left or after the
    last of them. The spare is in every string's room and in no two at once:
    one string may take all of it, and taking it leaves the rest with none.

    The measure, not an address: the layout moves the strings, so where a
    string's room ends is how far it may grow, counted from where it starts.
    """
    if not strings:
        return {}
    first = min(rec.start for rec in strings)
    held = sum(rec.byte_length(skips) for rec in strings)
    spare = max(bound - first - held, 0)
    return {rec.index: rec.start + rec.byte_length(skips) + spare for rec in strings}


def group_bounds(
    data: bytes,
    config: BlockConfig,
    groups: list[list[StringRecord]],
    registry=None,
) -> list[int]:
    """The exclusive end each group of a nested block may be written up to.

    A group's own last byte, and past it the run of whole fill patterns a
    shorter layout left — so room given up is room to take back — but never as
    far as the next thing the outer table points at (another group's inner
    table or text), nor past the block's ``bound``.
    """
    source = config.source
    assert isinstance(source, NestedPointerSource)
    records, _ = nested_records(data, source, registry)
    marks = sorted({r.table for r in records} | {r.base for r in records})
    fill = config.fill
    bounds = []
    for group in groups:
        first = min(r.start for r in group)
        own = max(r.end for r in group)
        at = bisect_right(marks, first)
        cap = marks[at] if at < len(marks) else len(data)
        if config.bound is not None:
            cap = min(cap, config.bound)
        end = own
        while fill and end + len(fill) <= cap and data[end : end + len(fill)] == fill:
            end += len(fill)
        bounds.append(max(own, min(end, cap)))
    return bounds


def string_ends(
    data: bytes, config: BlockConfig, strings: list[StringRecord], registry=None
) -> dict[int, int]:
    """Where each string's room ends, by index: its slot's end
    (:func:`slot_ends`) when the block is slotted, and its own bytes plus its
    group's spare (:func:`packed_ends`) when it is packed. What
    :func:`room_for` reads a string's room from."""
    slotted = config.effective_write_mode is WriteMode.SLOTTED
    if not isinstance(config.source, NestedPointerSource):
        bound = block_bound(config, strings)
        if slotted:
            return slot_ends(strings, bound, data, config.fill)
        return packed_ends(strings, bound, config.skips)
    groups = string_groups(config, strings)
    ends: dict[int, int] = {}
    for group, bound in zip(
        groups, group_bounds(data, config, groups, registry), strict=True
    ):
        if slotted:
            ends |= slot_ends(group, bound, data, config.fill)
        else:
            ends |= packed_ends(group, bound, config.skips)
    return ends


def layout_block(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    registry=None,
) -> LayoutResult:
    """Lay the block out with every replacement in place.

    Group by group (:func:`~mapchar.core.block.string_groups`): a nested
    block's groups each lay out over their own text, and only those holding a
    replacement are laid out at all — the rest stay as they are — so an edit
    costs its group, not the block.
    """
    result = LayoutResult()
    if not strings:
        return result
    groups = string_groups(config, strings)
    if isinstance(config.source, NestedPointerSource):
        groups = [
            g for g in groups if any(r.replacement is not None for r in g)
        ] or groups
        bounds = group_bounds(data, config, groups, registry)
    else:
        bounds = [block_bound(config, strings)]
    slotted = config.effective_write_mode is WriteMode.SLOTTED
    for group, bound in zip(groups, bounds, strict=True):
        for rec in group:
            enc = encode_string(rec, config, tables, data)
            result.encoded[rec.index] = enc
            if enc.problem is not None:
                result.problems.append(enc.problem)
        if slotted:
            _layout_slotted(data, config, group, bound, result)
        else:
            _layout_packed(config, group, bound, result, registry)
    if not result.ok:
        result.splices.clear()
    return result


def _layout_slotted(
    data: bytes,
    config: BlockConfig,
    strings: list[StringRecord],
    bound: int,
    result: LayoutResult,
) -> None:
    """Every string at its own address, within its slot."""
    fixed_len = config.fixed_length
    out = bytearray()
    first = min(s.start for s in strings)
    ends = slot_ends(strings, bound, data, config.fill)
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
        # follows. Padding the chunk out to the slot writes the fill
        # where the fill already is, so an unedited string's bytes, and
        # every byte outside a slot, stay as they are.
        extent = ends[rec.index] - rec.start
        room = extent if fixed_len is None else min(fixed_len, extent)
        if fixed_len is not None and fixed_len > extent:
            result.problems.append(
                Problem(
                    rec.index,
                    f"its slot holds {extent} byte(s), "
                    f"{fixed_len - extent} short of the fixed length "
                    f"of {fixed_len}",
                )
            )
            continue
        if len(enc.data) > room:
            result.problems.append(
                Problem(
                    rec.index,
                    f"the text encodes to {len(enc.data)} byte(s); its slot "
                    f"holds {room}, so {len(enc.data) - room} do not fit",
                    len(enc.data) - room,
                )
            )
            continue
        used += len(enc.data)
        chunk = enc.data + fill_run(config.fill, room - len(enc.data))
        at = rec.start - first
        out[at : at + room] = chunk
    result.used += used
    result.available += sum(
        (fixed_len if fixed_len is not None else ends[s.index] - s.start)
        for s in strings
    )
    if result.ok:
        result.splices.append(Splice(first, bytes(out)))


def _layout_packed(
    config: BlockConfig,
    strings: list[StringRecord],
    bound: int,
    result: LayoutResult,
    registry,
) -> None:
    """End to end from the first string, up to ``bound``, pointers rewritten.

    A string that lies inside the string laid out before it — the last page
    of a message, with pointers of its own — is not written twice while its
    bytes still end that string's: its pointers reach into that string where
    its bytes are now. Only pointers reach into a string, and no two strings
    may start at one address, which would make them one.
    """
    fill = config.fill
    fixed_len = config.fixed_length
    first = strings[0].start
    pos = first
    out = bytearray()
    m, o = config.realign
    container: tuple[StringRecord, bytes, int] | None = None
    """The last string laid out whole, its bytes, and where they now end."""
    tails: set[int] = set()
    shares = config.has_pointers and fixed_len is None and not m
    for rec in strings:
        enc = result.encoded[rec.index]
        if enc.problem is not None:
            continue
        if (
            shares
            and container is not None
            and _is_tail(rec, enc.data, *container)
            and container[2] - len(enc.data) not in tails
        ):
            at = container[2] - len(enc.data)
            enc.new_start = at
            tails.add(at)
            continue
        aligned = align_up(pos, m, o)
        out += fill_run(fill, aligned - pos)
        pos = aligned
        if fixed_len is not None:
            chunk = enc.data + fill_run(fill, fixed_len - len(enc.data))
        else:
            chunk = enc.data
        if pos + len(chunk) > bound:
            over = pos + len(chunk) - bound
            left = max(bound - pos, 0)
            result.problems.append(
                Problem(
                    rec.index,
                    f"the text encodes to {len(chunk)} byte(s) and only {left} "
                    f"are left before the block's bound (${bound:X}), so it "
                    f"crosses the bound by {over}",
                    over,
                )
            )
            pos += len(chunk)
            continue
        enc.new_start = pos
        out += chunk
        pos += len(chunk)
        container = (rec, enc.data, pos)
    result.used += pos - first
    result.available += bound - first
    if result.ok:
        out += fill_run(fill, bound - first - len(out))
        result.splices.append(Splice(first, bytes(out)))
        result.splices.extend(_pointer_splices(config, strings, result, registry))


def _is_tail(
    rec: StringRecord, data: bytes, whole: StringRecord, bytes_of: bytes, _end
) -> bool:
    """Whether ``rec``, now ``data``, lies inside ``whole``, now ``bytes_of``,
    and can still be read as its end (:func:`_layout_packed`)."""
    return (
        whole.start < rec.start < whole.end
        and 0 < len(data) < len(bytes_of)
        and bytes_of.endswith(data)
    )


def _pointer_splices(config, strings, result: LayoutResult, registry) -> list[Splice]:
    """Rewrite every pointer of a packed block to its string's new position,
    each through its own mapping and from its own offset — a nested source's
    inner pointers count from their group's base."""
    if not config.has_pointers:
        return []
    source = config.source
    registry = registry if registry is not None else default_registry()
    mappings: dict[str, Any] = {}
    splices = []
    for rec in strings:
        enc = result.encoded.get(rec.index)
        new_start = enc.new_start
        if new_start is None:
            continue
        for ref in rec.pointers:
            if ref.mapping_id not in mappings:
                mappings[ref.mapping_id] = resolve_mapping(registry, ref.mapping_id)
            mapping = mappings[ref.mapping_id]
            if mapping is None:
                result.problems.append(
                    Problem(-1, f"unknown mapping {ref.mapping_id!r}")
                )
                return []
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
    """How many bytes ``rec`` may take: the length a fixed-length block gives
    every string, its slot when the block is slotted (:func:`slot_ends`), or
    its own bytes plus its group's spare when it is packed
    (:func:`packed_ends`) — the room :func:`string_ends` works out, which
    ``ends`` carries when the caller has it.

    A caller with no ``ends`` to hand is told the bytes the string holds now,
    which is the room nothing has to be worked out to know; the layout, which
    has them, is the one that refuses.
    """
    if config is None:
        return rec.length
    if isinstance(config.string_type, FixedLength):
        return config.string_type.length
    if _crosses_skip(rec, config):
        # No single extent, and the layout will not rewrite it either.
        return rec.byte_length(config.skips)
    if ends is not None and rec.index in ends:
        return max(ends[rec.index] - rec.start, 0)
    return rec.byte_length(config.skips)


__all__ = [
    "LayoutResult",
    "Problem",
    "Splice",
    "apply_splices",
    "block_bound",
    "group_bounds",
    "layout_block",
    "packed_ends",
    "room_for",
    "slot_ends",
    "string_ends",
]
