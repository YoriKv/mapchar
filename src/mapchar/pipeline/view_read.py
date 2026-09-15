"""Reading a stretch of bytes the way the top bar says: what the Hex and Text
tabs show.

A view reads from wherever it starts, not from where a block would, so it cuts
the bytes by the reading's string type from its own first byte and leaves out
what only makes sense at a block's addresses — skip ranges, realignment, the
artificial codes of fixed lines. Read as pointers, it is the pointers the view
holds, each with the address it reaches.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace

from mapchar.core.bits import Bits, align_up
from mapchar.core.block import (
    BlockConfig,
    FixedLength,
    Pascal,
    PointerListSource,
    PointerTableSource,
)
from mapchar.core.mapping import read_pointer
from mapchar.core.table import TableSet
from mapchar.core.tokens import Token
from mapchar.engines.decode import DecodeRules, RunResult, decode_run
from mapchar.pipeline.extract import decode_one, pointer_target, string_at
from mapchar.plugins.registry import mapping_for

PointerSource = PointerTableSource | PointerListSource


def decode_strings(
    data: bytes, config: BlockConfig | None, tables: TableSet
) -> RunResult:
    """``data`` read as one string after another, cut by ``config``'s string
    type; to end tokens when it cuts by none of its own."""
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
    tokens: list[Token] = []
    starts: list[int] = []
    pos = 0
    while pos < bits.length:
        found, end, _ = decode_one(bits, cut, tables, pos, bits.length)
        starts.append(pos)
        tokens.extend(found)
        if end <= pos:
            break
        pos = end
    return RunResult(tokens, pos, starts)


def cuts_at_end_tokens(config: BlockConfig | None) -> bool:
    """Whether a view reads ``config`` from end token to end token, so any token
    boundary on a byte is a fresh start."""
    return _view_cut(config) is None


def align_before(
    data: bytes,
    first: int,
    start: int,
    offset: int,
    decode: Callable[[bytes], list[Token]],
    *,
    tries: int,
    lookahead: int,
) -> tuple[int, list[Token]]:
    """The tokens from about ``start`` up to ``offset``, decoded from whichever
    of the few bytes back from ``start`` reads best.

    Best is the fewest tokens left unmatched — a start inside a character leaves
    a trail of them — and then a token starting at ``offset`` itself, so the text
    above is in step with the text in view whenever that is in step with the
    file. A start that reads with nothing unmatched and in step is as good as one
    gets, and the tries stop there. ``first`` is as far back as the view reaches,
    and ``lookahead`` how far past ``offset`` is decoded to see whether a token
    starts there.
    """
    best: tuple[tuple[int, bool], int, list[Token]] | None = None
    start = max(first, start)
    for at in range(start, max(first - 1, start - tries), -1):
        rel = (offset - at) * 8
        tokens = decode(data[at : offset + lookahead])
        before = [t for t in tokens if t.bit_start < rel]
        unmatched = sum(t.entry is None and not t.fallback for t in before)
        score = (unmatched, not any(t.bit_start == rel for t in tokens))
        if best is None or score < best[0]:
            best = (score, at, before)
        if score == (0, False):
            break
    return best[1], best[2]


def _view_cut(config: BlockConfig | None) -> BlockConfig | None:
    """The reading a view cuts by, or ``None`` for plain end tokens."""
    if config is None:
        return None
    string_type = config.string_type
    if not isinstance(string_type, FixedLength | Pascal):
        return None
    return replace(
        config,
        string_type=string_type,
        skips=(),
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


def pointer_cells(
    data: bytes, source: PointerSource, lo: int, hi: int, registry=None
) -> list[PointerCell]:
    """The pointers of ``source`` that start in bytes ``lo`` to ``hi``.

    A pointer table's are every ``stride`` bytes from its start, and a list's
    are its addresses; one cut short by the end of the data is left out.
    """
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
        target = (
            pointer_target(mapping, source, value, address, len(data))
            if mapping is not None
            else None
        )
        cells.append(PointerCell(address, source.size, value, target))
    return cells


def pointer_window(source: PointerSource, lo: int, count: int) -> int | None:
    """The byte after the ``count``-th pointer of ``source`` at or past ``lo``:
    how far a view from ``lo`` reads to show that many. ``None`` when the source
    has fewer."""
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
