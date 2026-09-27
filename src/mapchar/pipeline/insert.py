"""Lay a block's strings out in place.

Encodes every string that carries a replacement text and keeps the bytes of
every other, places the results in *slotted* or *packed* mode, and refuses the
whole block when anything crosses its bound. The output is byte splices over
the decompressed buffer; nothing is written here.
"""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, NamedTuple

from mapchar.core.bits import Bits, align_up, bits_to_bytes, bytes_to_bits
from mapchar.core.block import (
    Align,
    BlockConfig,
    ChainMode,
    EndToken,
    Extraction,
    FixedLength,
    Lines,
    NestedPointerSource,
    Pascal,
    StringRecord,
    WriteMode,
    block_bound,
    string_groups,
)
from mapchar.core.errors import EncodeError, MapcharError
from mapchar.core.fill import fill_end, fill_run, format_fill
from mapchar.core.mapping import pointer_bytes
from mapchar.core.table import TableSet, TokenKind
from mapchar.core.text import same_text
from mapchar.core.tokens import render
from mapchar.engines.decode import DecodeRules, decode
from mapchar.engines.encode import encode
from mapchar.pipeline.extract import (
    end_code_split,
    extract,
    reextract,
    strip_artificial,
)
from mapchar.pipeline.pointers import nested_records
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
            result = encode(text, tables, end_terminated=True)
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
    header: int = 0,
    skips=(),
    realign: tuple[int, int] = (0, 0),
) -> dict[int, int]:
    """Where each string's slot ends, by index.

    A slot is the bytes the string itself holds plus the run of fill directly
    after them (:func:`~mapchar.core.fill.fill_end`) — the padding a shorter
    replacement left behind, which the next edit may use again — and stops at
    the next string in address order, at ``bound``, or at the end of ``data``,
    whichever comes first. Bytes between two strings that are not that padding
    belong to no slot: nothing the block writes may touch them. The ``header``
    bytes in front of the next string are its record's, fill-valued or not,
    and no slot reaches into them — counted back over the ``skips`` the
    reading stepped over on its way through them (:func:`_before`). Where the
    block realigns, the bytes up to the next aligned position are the string's
    too, whatever they hold: the reading never looks at them.

    Without ``data`` and ``fill`` there is no telling padding from anything
    else, and a slot is the whole gap to the next string — the room a block
    whose strings sit end to end has, and what a surface with no bytes to hand
    reports; the layout, which has them, is the one that decides.
    """
    ordered = sorted(strings, key=lambda s: s.start)
    ends: dict[int, int] = {}
    limit = bound if data is None else min(bound, len(data))
    for i, rec in enumerate(ordered):
        nxt = ordered[i + 1].start if i + 1 < len(ordered) else None
        stop = _before(nxt, header, skips) if nxt is not None else limit
        if data is None or not fill:
            ends[rec.index] = max(rec.end, stop)
            continue
        aligned = min(align_up(rec.end, *realign), stop)
        ends[rec.index] = max(fill_end(data, rec.end, fill, stop), aligned)
    return ends


def _before(pos: int, n: int, skips) -> int:
    """``n`` bytes back from ``pos``, over the skip ranges reading crossed on
    the way: where a record whose string starts at ``pos`` begins, the other
    way round from the walk the reading makes forward over its header
    (:func:`~mapchar.engines.decode.advance`)."""
    if not skips or n <= 0:
        return pos - n
    landings: dict[int, int] = {}
    for start, stop in skips:
        if start < stop:
            # Two skips landing on one byte: the earlier start, which is the
            # further back a slot may be asked to stop.
            landings[stop] = min(start, landings.get(stop, start))
    left = n
    while left > 0:
        if pos in landings:
            # Reading arrived here by jumping: the bytes before it are the
            # ones in front of where the skip began.
            pos = landings[pos]
            continue
        pos -= 1
        left -= 1
    return pos


def record_start(rec: StringRecord, config: BlockConfig) -> int:
    """Where the record of ``rec`` begins: its string, behind the block's
    record header (:attr:`~mapchar.core.block.BlockConfig.record_header`) —
    which is where a pointer that reaches it points."""
    return _before(rec.start, config.record_header, config.skips)


def packed_ends(
    strings: list[StringRecord], bound: int, skips=(), header: int = 0
) -> dict[int, int]:
    """Where each string's room ends, by index, when the group is packed.

    A packed group is laid out afresh from its first string, so no string has
    a place of its own: its room is the bytes it holds now plus the group's
    **spare** — everything between what its strings hold altogether and the
    bound, whether it lies in the gaps a shortened string left or after the
    last of them. The spare is in every string's room and in no two at once:
    one string may take all of it, and taking it leaves the rest with none.

    The measure, not an address: the layout moves the strings, so where a
    string's room ends is how far it may grow, counted from where it starts.
    A chain packed with its record headers (:func:`_layout_chain_pack`) counts
    each ``header`` as held too, from the first string's.
    """
    if not strings:
        return {}
    first = min(rec.start for rec in strings) - header
    held = sum(rec.byte_length(skips) + header for rec in strings)
    spare = max(bound - first - held, 0)
    return {rec.index: rec.start + rec.byte_length(skips) + spare for rec in strings}


def group_bounds(
    data: bytes,
    config: BlockConfig,
    groups: list[list[StringRecord]],
    registry=None,
) -> list[int]:
    """The exclusive end each group of a nested block may be written up to.

    A group's own last byte, and past it the run of fill a shorter layout left
    (:func:`~mapchar.core.fill.fill_end`) — so room given up is room to take
    back — but never as far as the next thing the outer table points at
    (another group's inner table or text), nor past the block's ``bound``.

    That cap is what makes the run the group's, so unlike a block's own bound
    (:func:`~mapchar.core.block.block_bound`) it does not ask whether the
    table reads the fill as padding: the outer table has already said the
    stretch holds nothing else of the block's, and a table whose codes begin
    with the fill byte — a 16-bit script whose end token is ``FFFF`` — would
    otherwise never take back a byte it gave up.
    """
    source = config.source
    assert isinstance(source, NestedPointerSource)
    records, _ = nested_records(data, source, registry)
    marks = sorted({r.table for r in records} | {r.base for r in records})
    bounds = []
    for group in groups:
        first = min(r.start for r in group)
        own = max(r.end for r in group)
        at = bisect_right(marks, first)
        cap = marks[at] if at < len(marks) else len(data)
        if config.bound is not None:
            cap = min(cap, config.bound)
        bounds.append(max(own, fill_end(data, own, config.fill, cap)))
    return bounds


def string_ends(
    data: bytes,
    config: BlockConfig,
    strings: list[StringRecord],
    registry=None,
    room: int | None = None,
) -> dict[int, int]:
    """Where each string's room ends, by index: its slot's end
    (:func:`slot_ends`) when the block is slotted, and its own bytes plus its
    group's spare (:func:`packed_ends`) when it is packed. What
    :func:`room_for` reads a string's room from, and ``room`` is what the block
    remembers giving up (:func:`~mapchar.core.block.block_bound`)."""
    slotted = config.effective_write_mode is WriteMode.SLOTTED
    if config.chained:
        bound = block_bound(config, strings, room)
        if config.chain is ChainMode.PACK:
            return packed_ends(strings, bound, header=config.record_header)
        return chain_ends(data, config, strings, bound)
    if not isinstance(config.source, NestedPointerSource):
        bound = block_bound(config, strings, room)
        if slotted:
            return slot_ends(
                strings,
                bound,
                data,
                config.fill,
                config.record_header,
                config.skips,
                config.realign,
            )
        return packed_ends(strings, bound, config.skips)
    groups = string_groups(config, strings)
    ends: dict[int, int] = {}
    for group, bound in zip(
        groups, group_bounds(data, config, groups, registry), strict=True
    ):
        if slotted:
            ends |= slot_ends(
                group,
                bound,
                data,
                config.fill,
                config.record_header,
                config.skips,
                config.realign,
            )
        else:
            ends |= packed_ends(group, bound, config.skips)
    return ends


def layout_block(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    registry=None,
    room: int | None = None,
) -> LayoutResult:
    """Lay the block out with every replacement in place.

    Group by group (:func:`~mapchar.core.block.string_groups`): a nested
    block's groups each lay out over their own text, and only those holding a
    replacement are laid out at all — the rest stay as they are — so an edit
    costs its group, not the block. ``room`` is what the block remembers
    giving up, which its bound takes back
    (:func:`~mapchar.core.block.block_bound`).
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
        bounds = [block_bound(config, strings, room)]
    slotted = config.effective_write_mode is WriteMode.SLOTTED
    for group, bound in zip(groups, bounds, strict=True):
        if config.chained and config.chain is ChainMode.PAD:
            _layout_chain_pad(data, config, tables, group, bound, result)
            continue
        for rec in group:
            enc = encode_string(rec, config, tables, data)
            result.encoded[rec.index] = enc
            if enc.problem is not None:
                result.problems.append(enc.problem)
        if config.chained:
            _layout_chain_pack(data, config, group, bound, result, registry)
        elif slotted:
            _layout_slotted(data, config, group, bound, result)
        else:
            _layout_packed(data, config, group, bound, result, registry)
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
    ends = slot_ends(
        strings,
        bound,
        data,
        config.fill,
        config.record_header,
        config.skips,
        config.realign,
    )
    # The part of each slot the fill already holds: a string padded out to
    # it leaves everything past it -- the rest of a realigned slot -- alone.
    filled = slot_ends(
        strings, bound, data, config.fill, config.record_header, config.skips
    )
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
        # follows. Padding the chunk out to the string's old bytes and the
        # fill after them writes the fill where the fill already is, so an
        # unedited string's bytes, the rest of a realigned slot, and every
        # byte outside a slot stay as they are.
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
        pad = min(room, filled[rec.index] - rec.start) - len(enc.data)
        chunk = enc.data + fill_run(config.fill, max(pad, 0))
        at = rec.start - first
        out[at : at + len(chunk)] = chunk
    result.used += used
    result.available += sum(
        (fixed_len if fixed_len is not None else ends[s.index] - s.start)
        for s in strings
    )
    if result.ok:
        result.splices.append(Splice(first, bytes(out)))


def _layout_packed(
    data: bytes,
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
        _fill_tail(data, fill, strings, first, pos, bound, out)
        result.splices.append(Splice(first, bytes(out)))
        result.splices.extend(_pointer_splices(config, strings, result, registry))


def _fill_tail(
    data: bytes,
    fill: bytes,
    strings: list[StringRecord],
    first: int,
    pos: int,
    bound: int,
    out: bytearray,
) -> None:
    """Pad a packed layout, ``out`` from ``first`` with its text ending at
    ``pos``, with the fill towards ``bound``.

    The fill goes as far as the bound, but the splice need not: past both the
    new text and the old, bytes that are already the fill the write would lay
    there are left alone, and the spare of a block whose bound is the fill run
    behind it can be a megabyte of free space. The pattern is laid from
    ``pos``, so what stands has to carry it on at its own phase — a multi-byte
    fill starting over mid-pattern is not one run, and the next reading would
    not read it as padding.
    """
    end = min(max(pos, max(rec.end for rec in strings)), bound)
    carried = _rotated(fill, end - pos)
    if end < bound and fill_end(data, end, carried, bound) < bound:
        end = bound
    out += fill_run(fill, end - first - len(out))


def _rotated(fill: bytes, by: int) -> bytes:
    """``fill`` as it reads ``by`` bytes into a run of it: the pattern a write
    carrying on from there would lay."""
    if not fill:
        return fill
    at = by % len(fill)
    return fill[at:] + fill[:at]


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
    """Rewrite every pointer the block's strings carry to its string's new
    position, each through its own mapping and from its own offset — a nested
    source's inner pointers count from their group's base, and the pointers
    **Attach** put on the strings of a range source are rewritten like a
    pointer source's own.

    A pointer is written only where it reads back as the string it reaches:
    the bank is the source's where it has one and the mapping's
    ``bank_of(target)`` otherwise, and a value that lands somewhere else in
    that bank — a string packed out of the bank a short pointer reads in —
    refuses the write rather than leave the pointer pointing at nothing.
    """
    registry = registry if registry is not None else default_registry()
    mappings: dict[str, Any] = {}
    splices = []
    for rec in strings:
        enc = result.encoded.get(rec.index)
        if enc.new_start is None:
            continue
        # A range's pointer reaches its string's record, header and all.
        new_start = enc.new_start - config.record_header
        for ref in rec.pointers:
            if ref.mapping_id not in mappings:
                mappings[ref.mapping_id] = resolve_mapping(registry, ref.mapping_id)
            mapping = mappings[ref.mapping_id]
            if mapping is None:
                result.problems.append(
                    Problem(-1, f"unknown mapping {ref.mapping_id!r}")
                )
                return []
            target = new_start - ref.offset
            bank = _pointer_bank(config.source, mapping, target)
            value = mapping.to_value(target, bank, ref.address)
            short = value & ((1 << (ref.size * 8)) - 1)
            if (
                short != value
                and getattr(mapping, "needs_bank", True)
                and mapping.to_offset(short, bank, ref.address) == target
            ):
                # A pointer too short for the bank leaves it to the block's:
                # the address alone still reaches the string from there.
                # Absent, ``needs_bank`` is read as set, as everywhere else;
                # the value still has to read back as the string.
                value = short
            if value < 0 or value >= 1 << (ref.size * 8):
                result.problems.append(
                    Problem(
                        rec.index, f"pointer at ${ref.address:X} cannot hold ${value:X}"
                    )
                )
                continue
            if mapping.to_offset(value, bank, ref.address) != target:
                result.problems.append(
                    Problem(
                        rec.index,
                        f"pointer at ${ref.address:X} cannot reach ${target:X} "
                        f"in bank {bank}",
                    )
                )
                continue
            splices.append(
                Splice(ref.address, pointer_bytes(value, ref.size, ref.endian))
            )
    return splices


def _pointer_bank(source, mapping, target: int) -> int:
    """The bank a pointer at ``target`` is written in: the source's where it
    has one — a pointer source names the bank its table is read in — else the
    bank the mapping puts the target in, and 0 where neither says."""
    bank: int | None = getattr(source, "bank", None)
    if bank is not None:
        return bank
    bank_of = getattr(mapping, "bank_of", None)
    return bank_of(target) if bank_of is not None else 0


def _crosses_skip(rec: StringRecord, config: BlockConfig) -> bool:
    """A string read across a skip range has no single byte extent."""
    return len(rec.pieces(config.skips)) > 1


# -- chains -------------------------------------------------------------------


def pad_unit(config: BlockConfig, tables: TableSet) -> tuple[bytes, str]:
    """What pads a chained string, as bytes and as the text they read as: the
    block's pad, else the start table's space. Refused with an
    :class:`EncodeError` when the table has no space, or the pad does not read
    as text in it."""
    pad = config.pad if config.pad is not None else _table_space(tables)
    if not pad:
        raise EncodeError(
            f"table @{tables.start.id} has no space to pad with; set the block's pad"
        )
    rules = DecodeRules(end_terminated=False, limit_bit=len(pad) * 8)
    tokens = decode(Bits(pad), tables, 0, rules).tokens
    if (
        not tokens
        or tokens[-1].bit_end != len(pad) * 8
        or any(
            t.fallback or t.entry is None or t.entry.kind is not TokenKind.TEXT
            for t in tokens
        )
    ):
        raise EncodeError(
            f"the pad {format_fill(pad)} is not text in table @{tables.start.id}"
        )
    return pad, render(tokens)


def _table_space(tables: TableSet) -> bytes | None:
    """The start table's shortest whole-byte entry for a space."""
    keys = [
        bits
        for bits, entry in tables.start.entries.items()
        if entry.kind is TokenKind.TEXT and entry.text == " " and len(bits) % 8 == 0
    ]
    return bits_to_bytes(min(keys, key=len)) if keys else None


def chain_links(
    config: BlockConfig, strings: list[StringRecord]
) -> list[tuple[StringRecord, StringRecord]]:
    """Every string of a chained block with the one the game reads straight
    after it: the next in address order, unless that one begins a chain."""
    ordered = sorted(strings, key=lambda r: r.start)
    starts = set(config.chain_starts(len(strings)))
    return [
        (a, b)
        for a, b in zip(ordered, ordered[1:], strict=False)
        if b.index not in starts
    ]


def chain_gaps(config: BlockConfig, strings: list[StringRecord]) -> dict[int, int]:
    """The strings of a chained block that stand past fill rather than where
    the game reads them, each by the offset the gap begins at: what
    :attr:`~mapchar.core.block.Extraction.chain_gaps` says of the bytes,
    worked out from the strings."""
    return {
        b.index: a.end
        for a, b in chain_links(config, strings)
        if b.start - config.record_header > a.end
    }


def chain_ends(
    data: bytes, config: BlockConfig, strings: list[StringRecord], bound: int
) -> dict[int, int]:
    """Where each chained string's room ends, by index, when the block pads
    inside its strings: a string the next one follows in its chain has up to
    that one's record, so nothing moves; the last of a chain has its slot —
    its own bytes and the fill after them (:func:`slot_ends`)."""
    ends = slot_ends(strings, bound, data, config.fill, config.record_header)
    for rec, nxt in chain_links(config, strings):
        ends[rec.index] = nxt.start - config.record_header
    return ends


def _padded_parts(
    text: str, config: BlockConfig, tables: TableSet, pad: bytes, align: Align
) -> tuple[bytes, bytes]:
    """``text`` encoded for a chained string, as the body the pad goes around
    and the end token after it (none for a length prefix).

    The pad a text already holds at its edges — read back from an earlier
    write — is taken off, so the write lays it afresh: the trailing pad
    always, and the leading pad unless the string is left-aligned, where
    leading room is the translator's own.
    """
    prefixed = isinstance(config.string_type, Pascal)
    r = encode(text, tables, end_terminated=not prefixed)
    if not prefixed and not r.ends_with_end:
        raise EncodeError("the text must end with an end token")
    rules = DecodeRules(end_terminated=False, limit_bit=len(r.bits))
    tokens = decode(Bits(r.data), tables, 0, rules).tokens
    end = b""
    if not prefixed:
        cut = tokens.pop().bit_start
        if cut % 8:
            raise EncodeError("the end token does not begin on a byte")
        end = r.data[cut // 8 :]
    unit = bytes_to_bits(pad)
    lo, hi = 0, len(tokens)
    while hi > lo and tokens[hi - 1].encoded_bits() == unit:
        hi -= 1
    while align is not Align.LEFT and lo < hi and tokens[lo].encoded_bits() == unit:
        lo += 1
    if lo == hi:
        return b"", end
    a, b = tokens[lo].bit_start, tokens[hi - 1].bit_end
    if a % 8 or b % 8:
        raise EncodeError("the text does not end on a byte, so no pad can follow it")
    return r.data[a // 8 : b // 8], end


def _encode_padded(
    rec: StringRecord,
    config: BlockConfig,
    tables: TableSet,
    room: int,
    tied: bool,
) -> Encoded:
    """A chained string's replacement, padded inside itself.

    ``room`` is how many bytes it may take. A string ``tied`` to the next one
    of its chain takes all of them, since the next begins where it ends; the
    last of a chain takes at least the bytes it holds now, so the text keeps
    the place the old one had, which is what aligning it works within. The
    pad goes after the text, in front of it, or both, as the string's
    alignment says, and a length prefix counts it.
    """
    st = config.string_type
    width = st.width if isinstance(st, Pascal) else 0
    align = rec.align or config.align
    try:
        pad, _ = pad_unit(config, tables)
        body, end = _padded_parts(rec.replacement or "", config, tables, pad, align)
    except EncodeError as exc:
        return Encoded(rec.index, b"", Problem(rec.index, str(exc)))
    size = width + len(body) + len(end)
    if size > room:
        over = size - room
        return Encoded(
            rec.index,
            b"",
            Problem(
                rec.index,
                f"the text encodes to {size - width} byte(s); its place in the "
                f"chain holds {room - width}, so {over} do not fit",
                over,
            ),
        )
    spare = (room if tied else max(size, rec.length)) - size
    if spare % len(pad):
        return Encoded(
            rec.index,
            b"",
            Problem(
                rec.index,
                f"{spare} byte(s) are left to pad and the pad {format_fill(pad)} "
                f"is {len(pad)}, so the string cannot end where the next begins",
            ),
        )
    n = spare // len(pad)
    lead = {Align.LEFT: 0, Align.CENTRE: n // 2, Align.RIGHT: n}[align]
    payload = pad * lead + body + pad * (n - lead)
    if not isinstance(st, Pascal):
        return Encoded(rec.index, payload + end)
    try:
        return Encoded(rec.index, _pascal(payload, st, None, "", tables))
    except EncodeError as exc:
        return Encoded(rec.index, b"", Problem(rec.index, str(exc)))


def _layout_chain_pad(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    bound: int,
    result: LayoutResult,
) -> None:
    """Every chained string in its own place, a shorter text padded inside it
    (:func:`_encode_padded`), so each still ends where the next begins and the
    fill is never written inside a chain."""
    ends = chain_ends(data, config, strings, bound)
    tied = {a.index for a, _ in chain_links(config, strings)}
    first = min(r.start for r in strings)
    last = min(max(max(r.end for r in strings), max(ends.values())), len(data))
    out = bytearray(data[first:last])
    for rec in strings:
        room = ends[rec.index] - rec.start
        result.available += room
        if rec.replacement is None:
            enc = encode_string(rec, config, tables, data)
        else:
            enc = _encode_padded(rec, config, tables, room, rec.index in tied)
        result.encoded[rec.index] = enc
        if enc.problem is not None:
            result.problems.append(enc.problem)
            continue
        result.used += len(enc.data)
        if rec.replacement is not None:
            at = rec.start - first
            out[at : at + len(enc.data)] = enc.data
    if result.ok:
        result.splices.append(Splice(first, bytes(out)))


def _layout_chain_pack(
    data: bytes,
    config: BlockConfig,
    strings: list[StringRecord],
    bound: int,
    result: LayoutResult,
    registry,
) -> None:
    """The chains back to back from the block's first record, each record's
    header carried verbatim with its string, the fill after the last up to
    ``bound``, and every pointer rewritten.

    A string that begins a chain is one the game reaches by a pointer or a
    code operand, so one that moves must carry the pointer that reaches it
    (**Attach**); one that has none refuses the write, since the game would go
    on reaching it where it no longer is. A string inside a chain is reached
    by reading the one before, and needs none.
    """
    header = config.record_header
    ordered = sorted(strings, key=lambda r: r.start)
    first = ordered[0].start - header
    pos = first
    out = bytearray()
    for rec in ordered:
        enc = result.encoded[rec.index]
        if enc.problem is not None:
            continue
        chunk = data[rec.start - header : rec.start] + enc.data
        if pos + len(chunk) > bound:
            over = pos + len(chunk) - bound
            result.problems.append(
                Problem(
                    rec.index,
                    f"the record takes {len(chunk)} byte(s) and only "
                    f"{max(bound - pos, 0)} are left before the block's bound "
                    f"(${bound:X}), so it crosses the bound by {over}",
                    over,
                )
            )
            pos += len(chunk)
            continue
        enc.new_start = pos + header
        out += chunk
        pos += len(chunk)
    starts = set(config.chain_starts(len(strings)))
    for rec in ordered:
        moved = result.encoded[rec.index].new_start
        if rec.index in starts and not rec.pointers and moved not in (None, rec.start):
            result.problems.append(
                Problem(
                    rec.index,
                    f"moves from ${rec.start - header:X} to ${moved - header:X} and "
                    "has no pointer; if the game reaches it directly, it will break",
                )
            )
    result.used += pos - first
    result.available += bound - first
    if result.ok:
        _fill_tail(data, config.fill, strings, first, pos, bound, out)
        result.splices.append(Splice(first, bytes(out)))
        result.splices.extend(_pointer_splices(config, strings, result, registry))


def repair_chains(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    gaps: dict[int, int],
) -> tuple[list[Splice], list[Problem]]:
    """The splices that close every gap of ``gaps``
    (:attr:`~mapchar.core.block.Extraction.chain_gaps`), and what stands in
    the way of any.

    The string in front of each gap is extended over the fill with the pad —
    its length prefix raised, or its end token moved past the pad — so the
    game reads the next string where it stands. Raises
    :class:`~mapchar.core.errors.EncodeError` when the block has no pad.
    """
    pad, _ = pad_unit(config, tables)
    by_index = {r.index: r for r in strings}
    st = config.string_type
    splices: list[Splice] = []
    problems: list[Problem] = []
    for index in sorted(gaps):
        prev, nxt = by_index.get(index - 1), by_index.get(index)
        if prev is None or nxt is None:
            continue
        n = nxt.start - config.record_header - prev.end
        if n <= 0:
            continue
        if n % len(pad):
            problems.append(
                Problem(
                    prev.index,
                    f"the {n}-byte gap at ${prev.end:X} is no whole number of "
                    f"the {len(pad)}-byte pad",
                )
            )
            continue
        run = pad * (n // len(pad))
        if isinstance(st, Pascal):
            order = "big" if st.endian == "big" else "little"
            count = int.from_bytes(data[prev.start : prev.start + st.width], order)
            count += n // len(pad) * _pad_weight(pad, tables) if st.counts_tokens else n
            if count >= 1 << (st.width * 8):
                problems.append(
                    Problem(
                        prev.index,
                        f"its {st.width}-byte length prefix cannot count {count}",
                    )
                )
                continue
            splices.append(Splice(prev.start, count.to_bytes(st.width, order)))
            splices.append(Splice(prev.end, run))
            continue
        last = prev.tokens[-1] if prev.tokens else None
        if last is None or not last.is_end or last.bit_start % 8 or prev.end_bit % 8:
            problems.append(
                Problem(prev.index, "does not end with an end token on a byte")
            )
            continue
        at = last.bit_start // 8
        splices.append(Splice(at, run + data[at : prev.end]))
    return splices, problems


def _pad_weight(pad: bytes, tables: TableSet) -> int:
    """What the pad counts for in a prefix that counts tokens."""
    rules = DecodeRules(end_terminated=False, limit_bit=len(pad) * 8)
    return sum(t.pascal_weight for t in decode(Bits(pad), tables, 0, rules).tokens)


def padded_view(text: str, pad: str, mark: str, tables: TableSet) -> str:
    """``text`` with each unit of ``pad`` at its edges — in front of its end
    code, if it closes with one — shown as ``mark``: the room a chained
    string still has."""
    body, end = end_code_split(text, tables)
    lead = trail = 0
    while pad and body.endswith(pad):
        body, trail = body[: -len(pad)], trail + 1
    while pad and body.startswith(pad):
        body, lead = body[len(pad) :], lead + 1
    unit = mark * len(pad)
    return unit * lead + body + unit * trail + end


def _unpadder(config: BlockConfig, tables: TableSet) -> Callable[[str], str]:
    """How a chained block that pads compares texts: without the pad at
    either edge, which a write lays afresh as the room and the alignment say
    (:func:`_padded_parts`)."""
    if not (config.chained and config.chain is ChainMode.PAD):
        return lambda text: text
    try:
        _, pad = pad_unit(config, tables)
    except EncodeError:
        return lambda text: text

    def unpad(text: str) -> str:
        body, end = end_code_split(text, tables)
        while body.endswith(pad):
            body = body[: -len(pad)]
        while body.startswith(pad):
            body = body[len(pad) :]
        return body + end

    return unpad


def apply_splices(data: bytes, splices: list[Splice]) -> bytes:
    out = bytearray(data)
    for s in splices:
        out[s.offset : s.end] = s.data
    return bytes(out)


def room_for(
    rec: StringRecord,
    config: BlockConfig | None,
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


def room_note(used: int, room: int, config: BlockConfig | None) -> str:
    """What a string's room is made of, in words.

    Two numbers do not say where the second comes from, and where it comes
    from is what tells a translator whether the room is theirs: a slotted
    string's is its own and a packed block's spare is every string's, so the
    first string to take it takes it from all the rest.
    """
    if config is None:
        return ""
    if config.fixed_length is not None:
        return f"{used} of {room} byte(s): the block's fixed length"
    if config.chained and config.chain is ChainMode.PAD:
        return (
            f"{used} of {room} byte(s): its place in the chain, padded inside "
            "when the text is shorter"
        )
    if config.chained or config.effective_write_mode is WriteMode.PACKED:
        return (
            f"{used} of {room} byte(s): its own plus the block's "
            f"{room - used} spare, shared by every string of the block"
        )
    return (
        f"{used} of {room} byte(s): its own plus the fill after them, "
        f"kept for this string whether used or not"
    )


class ReadBack(NamedTuple):
    """Why a laid-out buffer may not stand for an edit, and what it reads as."""

    block: str | None
    """Why the block as a whole refuses it: it will not read at all, or not as
    the same strings."""
    string: str | None
    """The first string that would not read as it must, named by its index."""
    extraction: Extraction | None
    """The reading, when nothing is against it — the one the re-read after the
    edit lands takes, rather than reading the same bytes a second time."""


def reads_back(
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    data: bytes,
    edits: dict[int, str],
    registry=None,
    span: tuple[int, int] | None = None,
) -> ReadBack:
    """Whether ``data`` may stand for ``edits`` over ``strings``, on the one
    set of terms.

    The bytes are the translation, so they must say what the translator said:
    the block must still read as the same strings, each edited string must read
    back as its text, and every other one must read as it does now — an edit
    that changes how the bytes after it are cut has changed strings nobody
    asked to change. Both landings, the undo step and the undo-free one a
    project load makes, refuse on these terms and no other.

    ``span`` is the stretch the edit changed: where the block's reading comes
    apart (:func:`~mapchar.pipeline.extract.reextract`), only what it reaches
    is read again.
    """
    try:
        check = None
        if span is not None:
            check = reextract(data, config, tables, strings, *span, registry)
        if check is None:
            check = extract(data, config, tables, registry)
    except MapcharError as exc:
        return ReadBack(str(exc), None, None)
    if len(check.strings) != len(strings):
        return ReadBack(
            f"the block would read as {len(check.strings)} strings instead of "
            f"{len(strings)}",
            None,
            None,
        )
    if config.chained:
        broken = set(check.chain_gaps) - set(chain_gaps(config, strings))
        if broken:
            first = min(broken)
            return ReadBack(
                f"string #{first} would start in fill at "
                f"${check.chain_gaps[first]:X}: the game reads the chain back to "
                "back",
                None,
                None,
            )
    unpad = _unpadder(config, tables)
    read = {r.index: r for r in check.strings}
    for i, t in edits.items():
        back = read[i].current_text()
        if not same_text(unpad(back), unpad(t)):
            return ReadBack(None, f"#{i}: reads back as {back!r}", None)
    for rec in strings:
        if rec.index in edits or read[rec.index] is rec:
            # A record a partial reading kept is the bytes it was.
            continue
        back = read[rec.index].current_text()
        if not same_text(back, rec.current_text()):
            return ReadBack(None, f"#{rec.index}: would change to {back!r}", None)
    return ReadBack(None, None, check)


__all__ = [
    "LayoutResult",
    "chain_ends",
    "chain_gaps",
    "chain_links",
    "pad_unit",
    "padded_view",
    "record_start",
    "repair_chains",
    "Problem",
    "ReadBack",
    "Splice",
    "apply_splices",
    "group_bounds",
    "layout_block",
    "packed_ends",
    "reads_back",
    "room_for",
    "room_note",
    "slot_ends",
    "string_ends",
]
