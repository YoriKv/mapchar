"""Reading a stretch of bytes the way the top bar says: what the Hex and Text
tabs show.

A view reads from wherever it starts, not from where a block would, so it
leaves out what only makes sense at a block's addresses — skip ranges,
realignment, the artificial codes of fixed lines. It does keep the reading's
string type, and cuts by it in step with the strings the block reads: a range's
fixed length runs from its start, so a view that starts part-way through a
string shows the rest of that one and whole ones after it. Read as pointers, it
is the pointers the view holds, each with the address it reaches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from mapchar.core.bits import Bits, align_up
from mapchar.core.block import (
    BlockConfig,
    FixedLength,
    NestedPointerSource,
    Pascal,
    PointerSource,
    PointerTableSource,
    RangeSource,
)
from mapchar.core.mapping import read_pointer
from mapchar.core.table import TableSet
from mapchar.core.tokens import Token
from mapchar.engines.decode import DecodeRules, RunResult, decode_run
from mapchar.pipeline.extract import (
    decode_one,
    pad_run,
    padding_bits,
    string_at,
)
from mapchar.pipeline.pointers import (
    nested_records,
    pointer_addresses,
    pointer_target,
)
from mapchar.plugins.registry import mapping_for


def decode_strings(
    data: bytes, config: BlockConfig | None, tables: TableSet, offset: int = 0
) -> RunResult:
    """``data`` read as one string after another, cut by ``config``'s string
    type; to end tokens when it cuts by none of its own.

    ``offset`` is the byte ``data`` begins at, which a fixed length cuts in
    step with (:func:`_head`): without it a view moved by a line lands inside a
    string and reads every string after it out of step. A record header is
    stepped over before each string, as the block steps over it, and its bytes
    are shown as the bytes they are.

    So is the padding a string written shorter than its slot left behind it
    (:func:`~mapchar.pipeline.extract.padding_bits`), which stands in front of
    the next record's header: read as text — an ``FF`` read as a length of 255
    — it would put every string after it on the wrong byte. The block passes
    none in front of its first string, having none before it; a view past the
    range's start may begin on a run instead, and passes that one too, which is
    what puts its first string where the block's is.
    """
    bits = Bits(data)
    cut = _view_cut(config)
    if cut is None:
        return decode_run(
            bits,
            tables,
            rules=DecodeRules(end_terminated=True),
            runs=None,
            ends_only=False,
        )
    pad = _view_padding(cut, tables)
    header, head = _head(cut, offset)
    tokens: list[Token] = []
    starts: list[int] = []
    pos = 0
    while pos < bits.length:
        if pad is not None and pos % 8 == 0 and (starts or offset > cut.source.start):
            run = pad_run(bits, pos, pad, bits.length)
            tokens += _raw_tokens(bits, pos, run)
            pos += run
            if pos >= bits.length:
                break
        if header:
            tokens += _raw_tokens(bits, pos, header)
            pos = min(pos + header, bits.length)
            if pos >= bits.length:
                break
        one = cut if head is None else replace(cut, string_type=head)
        found, end, _ = decode_one(bits, one, tables, pos, bits.length)
        starts.append(pos)
        tokens.extend(found)
        if end <= pos:
            break
        pos = end
        header, head = cut.record_header * 8, None
    return RunResult(tokens, pos, starts)


def _raw_tokens(bits: Bits, pos: int, count: int) -> list[Token]:
    """``count`` bits from ``pos``, a byte to a token: a record header or the
    padding behind a string is no string's text, and the view shows it as
    ``[$xx]``."""
    end = min(pos + count, bits.length)
    tokens: list[Token] = []
    for at in range(pos, end, 8):
        chunk = bits.window(at, min(8, end - at))
        tokens.append(Token(chunk, at, at + len(chunk)))
    return tokens


def _view_padding(cut: BlockConfig, tables: TableSet) -> str | None:
    """The fill pattern a view passes over between strings, or ``None`` where
    none is padding — on the same terms as
    :func:`~mapchar.pipeline.extract._extract_range`: over a range, where the
    strings are not all of one length, and where the fill begins no token."""
    if not isinstance(cut.source, RangeSource) or cut.fixed_length is not None:
        return None
    return padding_bits(cut, tables)


def _head(cut: BlockConfig, offset: int) -> tuple[int, FixedLength | None]:
    """Where a view from ``offset`` starts within a record: the header bits
    still in front of its first string, and that string's length where the view
    starts inside one — ``None`` where it starts on one already.

    Only a range of fixed strings has a grid to be in step with, the header and
    the length together: a Pascal count is read from the data, and a view that
    starts inside one of those cannot find the count that says how long it is;
    a pointer source's strings are each at their own target, which no phase
    gives — and no header either.
    """
    header = cut.record_header
    string_type = cut.string_type
    if not isinstance(string_type, FixedLength) or string_type.length <= 0:
        return header * 8, None
    if not isinstance(cut.source, RangeSource):
        return header * 8, None
    phase = (offset - cut.source.start) % (header + string_type.length)
    if phase < header:
        return (header - phase) * 8, None
    phase -= header
    return 0, replace(string_type, length=string_type.length - phase) if phase else None


def cuts_at_end_tokens(config: BlockConfig | None) -> bool:
    """Whether a view reads ``config`` from end token to end token, so any token
    boundary on a byte is a fresh start."""
    return _view_cut(config) is None


def align_before(
    data: bytes,
    first: int,
    start: int,
    offset: int,
    decode: Callable[[bytes, int], RunResult],
    *,
    tries: int,
    lookahead: int,
) -> tuple[int, list[Token], list[int]]:
    """The tokens from about ``start`` up to ``offset``, and the bit each of
    their strings begins at, decoded from whichever of the few bytes back from
    ``start`` reads best.

    Best is the fewest tokens left unmatched — a start inside a character leaves
    a trail of them — and then a token starting at ``offset`` itself, so the text
    above is in step with the text in view whenever that is in step with the
    file. A start that reads with nothing unmatched and in step is as good as one
    gets, and the tries stop there. ``first`` is as far back as the view reaches,
    and ``lookahead`` how far past ``offset`` is decoded to see whether a token
    starts there.

    A string beginning at ``offset`` itself is kept among the starts, though no
    token of the text above is part of it: it is what ends the last string above
    with a line break, so the text above does not run into the view's first
    line.
    """
    best: tuple[tuple[int, bool], int, list[Token], list[int]] | None = None
    start = max(first, start)
    for at in range(start, max(first - 1, start - tries), -1):
        rel = (offset - at) * 8
        run = decode(data[at : offset + lookahead], at)
        before = [t for t in run.tokens if t.bit_start < rel]
        unmatched = sum(t.entry is None and not t.fallback for t in before)
        score = (unmatched, not any(t.bit_start == rel for t in run.tokens))
        if best is None or score < best[0]:
            best = (score, at, before, [s for s in run.starts if s <= rel])
        if score == (0, False):
            break
    return best[1], best[2], best[3]


def _view_cut(config: BlockConfig | None) -> BlockConfig | None:
    """The reading a view cuts by, or ``None`` for plain end tokens.

    The header is kept, since it stands between the strings; a header behind
    the position, which no control sets, is none, so the view always advances.

    A string that ends at an end token needs no cut of its own, the view
    reading to end tokens as the block does — unless a record header stands in
    front of each of them, which only a cut steps over: read as text, its bytes
    show as whatever the table maps them to and take the string's start with
    them.
    """
    if config is None:
        return None
    string_type = config.string_type
    if not isinstance(string_type, FixedLength | Pascal) and not config.record_header:
        return None
    return replace(
        config,
        string_type=string_type,
        skips=(),
        header=max(config.record_header, 0),
        realign=(0, 0),
        line_length=0,
        show_end=False,
        bound=None,
    )


@dataclass(frozen=True)
class PointerCell:
    """One pointer in view."""

    address: int
    size: int
    value: int
    target: int | None
    """The byte it reaches, or ``None`` when it maps outside the data."""
    null: bool = False
    """It holds the source's null value: it reaches nothing."""
    role: str | None = None
    """What a nested source's outer pointer is, since it reaches structure
    rather than text: ``"table"`` for the one at its record's inner pointer
    table, ``"base"`` for the one at the base those pointers count from.
    ``None`` for every pointer that reaches a string."""


def _cell_target(
    mapping, source: PointerSource, value: int, address: int, data: bytes, null: bool
) -> int | None:
    """Where a cell's pointer reaches, or ``None``: a view shows a pointer
    whatever it holds, so one that is null or read through a mapping the build
    has not got reaches nothing rather than being left out."""
    if mapping is None or null:
        return None
    return pointer_target(mapping, source, value, address, len(data))


def pointer_cells(
    data: bytes, source: PointerSource, lo: int, hi: int, registry=None
) -> list[PointerCell]:
    """The pointers of ``source`` that start in bytes ``lo`` to ``hi``.

    A pointer table's are every ``stride`` bytes from its start, and a list's
    are its addresses; one cut short by the end of the data is left out. A
    nested source's are its outer table's pointers and its inner tables'.
    """
    if isinstance(source, NestedPointerSource):
        return _nested_cells(data, source, lo, hi, registry)
    mapping = mapping_for(source, registry)
    if isinstance(source, PointerTableSource):
        stride = max(source.stride, 1)
        first = align_up(lo, stride, source.start)
        addresses = range(first, min(source.stop, hi), stride)
    else:
        addresses = sorted(a for a in source.addresses if lo <= a < hi)
    cells: list[PointerCell] = []
    for address in addresses:
        value = read_pointer(data, address, source.size, source.endian)
        if value is None:
            break
        null = value == source.null
        target = _cell_target(mapping, source, value, address, data, null)
        cells.append(PointerCell(address, source.size, value, target, null))
    return cells


def _nested_cells(
    data: bytes, source: NestedPointerSource, lo: int, hi: int, registry
) -> list[PointerCell]:
    """A nested source's pointers in bytes ``lo`` to ``hi``: each record's two
    outer pointers, mapped and marked for which of the two they are, and every
    inner pointer, counted from its base."""
    mapping = mapping_for(source, registry)
    cells: list[PointerCell] = []
    stride = max(source.stride, 1)
    for record in range(
        align_up(lo - 2 * source.size, stride, source.start),
        min(source.stop, hi),
        stride,
    ):
        for address, role in ((record, "table"), (record + source.size, "base")):
            if not lo <= address < hi:
                continue
            value = read_pointer(data, address, source.size, source.endian)
            if value is None:
                continue
            null = value == source.null
            target = _cell_target(mapping, source, value, address, data, null)
            cells.append(PointerCell(address, source.size, value, target, null, role))
    width = source.inner_size
    records, _ = nested_records(data, source, registry)
    for rec in records:
        if rec.base <= lo or rec.table >= hi:
            continue
        first = align_up(max(lo, rec.table), width, rec.table)
        for address in range(first, min(rec.base - width + 1, hi), width):
            value = read_pointer(data, address, width, source.inner_endian)
            if value is None:
                break
            null = value == source.inner_null
            target = None if null or rec.base + value >= len(data) else rec.base + value
            cells.append(PointerCell(address, width, value, target, null))
    return sorted(cells, key=lambda c: c.address)


def pointer_window(
    source: PointerSource, lo: int, count: int, data: bytes = b"", registry=None
) -> int | None:
    """The byte after the ``count``-th pointer of ``source`` at or past ``lo``:
    how far a view from ``lo`` reads to show that many. ``None`` when the source
    has fewer. A nested source's inner tables are read from ``data``."""
    if isinstance(source, NestedPointerSource):
        found = sorted(
            (a, size)
            for a, size in pointer_addresses(data, source, registry)
            if a >= lo
        )
        if len(found) < count:
            return None
        address, size = found[count - 1]
        return address + size
    if isinstance(source, PointerTableSource):
        stride = max(source.stride, 1)
        first = align_up(lo, stride, source.start)
        last = first + (count - 1) * stride
        return last + source.size if last < source.stop else None
    addresses = sorted(a for a in source.addresses if a >= lo)
    if len(addresses) < count:
        return None
    return addresses[count - 1] + source.size


PREVIEW_BYTES = 256
"""How far past its target a pointer's string is read for a preview. A pointer
into anything but text reads to the next end token, wherever that is — the end
of the file, on a table that has none — and a view holds hundreds of pointers."""


def target_string(
    bits: Bits, config: BlockConfig, tables: TableSet, target: int
) -> tuple[list[Token], bool]:
    """The string a pointer reaches, read by ``config``'s string rules for at
    most :data:`PREVIEW_BYTES`, and whether that cut it short."""
    tokens = string_at(bits, config, tables, target * 8, PREVIEW_BYTES * 8)
    last = tokens[-1] if tokens else None
    cut = (
        last is not None
        and last.bit_end >= (target + PREVIEW_BYTES) * 8
        and not last.is_end
    )
    return tokens, cut
