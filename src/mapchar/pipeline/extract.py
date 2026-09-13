"""Run a block configuration over a buffer and cut it into strings."""

from __future__ import annotations

from mapchar.core.bits import Bits
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    Extraction,
    FixedLength,
    FixedSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerRef,
    PointerTableSource,
    RangeSource,
    StringRecord,
)
from mapchar.core.notices import Notice
from mapchar.core.table import Entry, EntryKind, TableSet
from mapchar.core.tokens import Token
from mapchar.engines.decode import DecodeResult, DecodeRules, EndedBy, decode


def artificial(label: str, bit: int) -> Token:
    """A zero-width code token that stands for a boundary, not for bytes.

    It carries a line break like Cartographer's ``LINE CTRL\n``, so dumps of
    fixed strings read line by line.
    """
    return Token("", bit, bit, Entry("", EntryKind.TEXT, f"[{label}]\\n"))


def extract(
    data: bytes, config: BlockConfig, tables: TableSet, registry=None
) -> Extraction:
    """Cut ``data`` into strings. Pointer sources need a ``registry`` for mappings."""
    bits = Bits(data)
    source = config.source
    if isinstance(source, RangeSource):
        return _extract_range(bits, config, tables, source)
    if isinstance(source, FixedSource):
        return _extract_fixed(bits, config, tables, source)
    if isinstance(source, PointerTableSource | PointerListSource):
        return _extract_pointers(bits, config, tables, source, registry)
    raise TypeError(f"unknown source {source!r}")


def read_pointers(
    data: bytes, source: PointerTableSource | PointerListSource, registry
) -> tuple[list[PointerRef], list[int | None], list[Notice]]:
    """Every pointer of the source with its target offset (None when unmapped)."""
    from mapchar.core.mapping import read_pointer, resolve_mapping

    if registry is None:
        from mapchar.plugins.registry import default_registry

        registry = default_registry()
    mapping = resolve_mapping(registry, source.mapping_id)
    notices: list[Notice] = []
    if mapping is None:
        notices.append(Notice(f"unknown mapping {source.mapping_id!r}"))
        return [], [], notices
    if isinstance(source, PointerTableSource):
        addresses = list(range(source.start, source.stop, max(source.stride, 1)))
    else:
        addresses = list(source.addresses)
    refs: list[PointerRef] = []
    targets: list[int | None] = []
    for address in addresses:
        value = read_pointer(data, address, source.size, source.endian)
        if value is None:
            notices.append(Notice("pointer past the end of the data", offset=address))
            continue
        target = mapping.to_offset(value, source.bank, address)
        if target is not None:
            target += source.offset
            if not (0 <= target < len(data)):
                target = None
        if target is None:
            notices.append(
                Notice(f"pointer ${value:X} maps outside the data", offset=address)
            )
        refs.append(
            PointerRef(
                address,
                source.size,
                source.endian,
                source.mapping_id,
                source.offset,
                value,
            )
        )
        targets.append(target)
    return refs, targets, notices


def _extract_pointers(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    source: PointerTableSource | PointerListSource,
    registry,
) -> Extraction:
    refs, targets, notices = read_pointers(bits.data, source, registry)
    # One string per distinct target, in address order, with every pointer.
    by_target: dict[int, list[PointerRef]] = {}
    for ref, target in zip(refs, targets, strict=True):
        if target is not None:
            by_target.setdefault(target, []).append(ref)
    ordered = sorted(by_target)
    stop_bit = (config.bound * 8) if config.bound is not None else bits.length
    stop_bit = min(stop_bit, bits.length)
    strings: list[StringRecord] = []
    st = config.string_type
    for i, target in enumerate(ordered):
        start = target * 8
        if start >= bits.length:
            continue
        limit = stop_bit if stop_bit > start else bits.length
        if isinstance(st, NextPointer):
            nxt = ordered[i + 1] * 8 if i + 1 < len(ordered) else None
            if nxt is not None and nxt > start:
                limit = min(limit, nxt)
                r = decode(bits, tables, start, _rules(config, limit, False))
                tokens, end, res_notices = r.tokens, limit, r.notices
            else:
                tokens, end, res_notices = _decode_terminated(
                    bits, config, tables, start, limit
                )
        elif isinstance(st, FixedLength):
            piece_limit = min(start + st.length * 8, limit)
            tokens, end, res_notices = _decode_fixed(
                bits, config, tables, start, piece_limit
            )
            if st.stop_at_end:
                # Pointer methods stop early at an end token; the record keeps
                # the fixed extent so its slot stays whole.
                pass
        elif isinstance(st, Pascal):
            tokens, end, res_notices = _decode_pascal(
                bits, config, tables, start, limit, st
            )
        else:
            tokens, end, res_notices = _decode_terminated(
                bits, config, tables, start, limit
            )
        strings.append(
            StringRecord(
                len(strings),
                start,
                end,
                tokens,
                tuple(by_target[target]),
                notices=res_notices,
            )
        )
    return Extraction(strings, notices)


def _decode_pascal(bits, config, tables, start, stop_bit, st: Pascal):
    length_bits = st.width * 8
    chunk = bits.window(start, length_bits)
    if len(chunk) < length_bits:
        return (
            [],
            start,
            [Notice("Pascal length past the end of the data", offset=start // 8)],
        )
    raw = int(chunk, 2).to_bytes(st.width, "big")
    n = int.from_bytes(raw, "big" if st.endian == "big" else "little")
    body = start + length_bits
    if st.counts_tokens:
        return _decode_counted(bits, config, tables, body, stop_bit, n)
    limit = min(body + n * 8, stop_bit)
    r = decode(bits, tables, body, _rules(config, limit, False))
    return r.tokens, limit, r.notices


def _rules(config: BlockConfig, limit_bit: int, end_terminated: bool) -> DecodeRules:
    m, o = config.realign
    return DecodeRules(
        end_terminated=end_terminated,
        limit_bit=limit_bit,
        skips=tuple((a * 8, b * 8) for a, b in config.skips),
        realign=(m * 8, o * 8),
    )


def _extract_range(
    bits: Bits, config: BlockConfig, tables: TableSet, source: RangeSource
) -> Extraction:
    strings: list[StringRecord] = []
    notices: list[Notice] = []
    stop_bit = min(source.stop * 8, bits.length)
    pos = source.start * 8
    st = config.string_type
    if isinstance(st, NextPointer):
        notices.append(
            Notice("'next pointer' needs a pointer source; reading to end tokens")
        )
        st = EndToken()
    while pos < stop_bit:
        start = pos
        if isinstance(st, FixedLength):
            limit = min(start + st.length * 8, stop_bit)
            tokens, end, res_notices = _decode_fixed(bits, config, tables, start, limit)
            record_end = limit
        elif isinstance(st, Pascal):
            tokens, end, res_notices = _decode_pascal(
                bits, config, tables, start, stop_bit, st
            )
            record_end = end
        else:
            tokens, end, res_notices = _decode_terminated(
                bits, config, tables, start, stop_bit
            )
            record_end = end
        if record_end <= start:
            break
        strings.append(
            StringRecord(len(strings), start, record_end, tokens, notices=res_notices)
        )
        pos = record_end
    return Extraction(strings, notices)


def _extract_fixed(
    bits: Bits, config: BlockConfig, tables: TableSet, source: FixedSource
) -> Extraction:
    strings: list[StringRecord] = []
    notices: list[Notice] = []
    for i in range(source.count):
        start = (source.start + i * source.length) * 8
        if start >= bits.length:
            notices.append(
                Notice(f"only {i} of {source.count} strings fit in the data")
            )
            break
        limit = min(start + source.length * 8, bits.length)
        tokens, _, res_notices = _decode_fixed(bits, config, tables, start, limit)
        strings.append(StringRecord(i, start, limit, tokens, notices=res_notices))
    return Extraction(strings, notices)


def _decode_terminated(
    bits: Bits, config: BlockConfig, tables: TableSet, start: int, stop_bit: int
) -> tuple[list[Token], int, list[Notice]]:
    """One string of ``strings_per_pointer`` end-token runs."""
    tokens: list[Token] = []
    notices: list[Notice] = []
    pos = start
    for _ in range(max(config.strings_per_pointer, 1)):
        r = decode(bits, tables, pos, _rules(config, stop_bit, True))
        tokens.extend(r.tokens)
        notices.extend(r.notices)
        pos = r.end_bit
        if r.ended_by is not EndedBy.END_TOKEN:
            break
    return tokens, pos, notices


def _decode_fixed(
    bits: Bits, config: BlockConfig, tables: TableSet, start: int, limit: int
) -> tuple[list[Token], int, list[Notice]]:
    """A fixed string, optionally cut into fixed lines, with artificial codes."""
    st = config.string_type
    stop_at_end = isinstance(st, FixedLength) and st.stop_at_end
    tokens: list[Token] = []
    notices: list[Notice] = []
    if config.line_length > 0:
        pos = start
        first = True
        while pos < limit:
            piece_limit = min(pos + config.line_length * 8, limit)
            r = decode(bits, tables, pos, _rules(config, piece_limit, stop_at_end))
            if not first:
                tokens.append(artificial(config.line_label, pos))
            first = False
            tokens.extend(r.tokens)
            notices.extend(r.notices)
            if stop_at_end and r.ended_by is EndedBy.END_TOKEN:
                break
            pos = piece_limit
    else:
        r = decode(bits, tables, start, _rules(config, limit, stop_at_end))
        tokens.extend(r.tokens)
        notices.extend(r.notices)
    if config.show_end:
        tokens.append(artificial(config.end_label, limit))
    return tokens, limit, notices


def _decode_counted(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    start: int,
    stop_bit: int,
    count: int,
) -> tuple[list[Token], int, list[Notice]]:
    """Decode until ``count`` token weights are used up (Pascal token strings)."""
    r: DecodeResult = decode(bits, tables, start, _rules(config, stop_bit, False))
    used = 0
    tokens: list[Token] = []
    end = start
    for token in r.tokens:
        if used >= count:
            break
        tokens.append(token)
        used += token.weight if token.entry is not None else 1
        end = token.bit_end
    return tokens, end, r.notices
