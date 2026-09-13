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


def extract(data: bytes, config: BlockConfig, tables: TableSet) -> Extraction:
    bits = Bits(data)
    source = config.source
    if isinstance(source, RangeSource):
        return _extract_range(bits, config, tables, source)
    if isinstance(source, FixedSource):
        return _extract_fixed(bits, config, tables, source)
    if isinstance(source, PointerTableSource | PointerListSource):
        raise NotImplementedError("pointer sources arrive in phase 3")
    raise TypeError(f"unknown source {source!r}")


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
            length_bits = st.width * 8
            chunk = bits.window(start, length_bits)
            if len(chunk) < length_bits:
                break
            raw = int(chunk, 2).to_bytes(st.width, "big")
            n = int.from_bytes(raw, "big" if st.endian == "big" else "little")
            body = start + length_bits
            if st.counts_tokens:
                tokens, end, res_notices = _decode_counted(
                    bits, config, tables, body, stop_bit, n
                )
            else:
                limit = min(body + n * 8, stop_bit)
                r = decode(bits, tables, body, _rules(config, limit, False))
                tokens, end, res_notices = r.tokens, limit, r.notices
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
