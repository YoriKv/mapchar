"""Reading a stretch of bytes the way the top bar says: what the Hex and Text
tabs show.

A view reads from wherever it starts, not from where a block would, so it cuts
the bytes by the reading's string type from its own first byte and leaves out
what only makes sense at a block's addresses — skip ranges, realignment, the
artificial codes of fixed lines. Read as pointers, it is the pointers the view
holds, each with the address it reaches.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from mapchar.core.bits import Bits
from mapchar.core.block import (
    BlockConfig,
    FixedLength,
    FixedSource,
    Pascal,
    PointerListSource,
    PointerTableSource,
)
from mapchar.core.mapping import mapping_for, read_pointer
from mapchar.core.table import TableSet
from mapchar.core.tokens import Token
from mapchar.engines.decode import DecodeRules, RunResult, decode_run
from mapchar.pipeline.extract import decode_one, pointer_target, string_at

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


def _view_cut(config: BlockConfig | None) -> BlockConfig | None:
    """The reading a view cuts by, or ``None`` for plain end tokens."""
    if config is None:
        return None
    string_type = config.string_type
    if isinstance(config.source, FixedSource):
        stop = isinstance(string_type, FixedLength) and string_type.stop_at_end
        string_type = FixedLength(config.source.length, stop)
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
        first = source.start + max(0, -(-(lo - source.start) // stride)) * stride
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


def target_string(
    data: bytes, config: BlockConfig, tables: TableSet, target: int
) -> list[Token]:
    """The string a pointer reaches, read by ``config``'s string rules."""
    return string_at(Bits(data), config, tables, target * 8)
