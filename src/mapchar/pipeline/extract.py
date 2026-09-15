"""Run a block configuration over a buffer and cut it into strings."""

from __future__ import annotations

from dataclasses import replace

from mapchar.core.bits import Bits, bits_to_bytes
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    Extraction,
    FixedLength,
    Lines,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerRef,
    PointerTableSource,
    RangeSource,
    StringRecord,
)
from mapchar.core.mapping import read_pointer
from mapchar.core.notices import Notice
from mapchar.core.table import Entry, TableSet, TokenKind
from mapchar.core.tokens import CodeRef, TextRun, Token, escape_text, parse_text
from mapchar.engines.decode import DecodeResult, DecodeRules, EndedBy, decode
from mapchar.plugins.registry import mapping_for


def artificial(label: str, bit: int) -> Token:
    """A zero-width code token that stands for a boundary, not for bytes.

    It carries a line break like Cartographer's ``LINE CTRL\n``, so dumps of
    fixed strings read line by line.
    """
    text = f"[{label}]\\n" if label else "\\n"
    return Token("", bit, bit, Entry("", TokenKind.TEXT, text))


def strip_artificial(text: str, config: BlockConfig) -> list[str]:
    """The lines of a dumped string with the artificial codes taken out.

    A dump closes a fixed string with an artificial end code and breaks its
    fixed lines with artificial line codes; neither stands for bytes, so
    neither is encoded. Only a final end code is artificial -- one earlier in
    the text is the table's own end token and stays.
    """
    if config.show_end:
        body = text.rstrip("\n")
        items = parse_text(body)
        if items and _is_code(items[-1], config.end_label):
            text = body[: body.rfind("[")]
    drop = config.line_label if config.line_length else ""
    return [_without_code(line, drop) for line in text.split("\n")]


def _is_code(item: TextRun | CodeRef, label: str) -> bool:
    """Whether ``item`` is the bare code ``[label]``."""
    return isinstance(item, CodeRef) and item.label == label and not item.words


def _without_code(line: str, label: str) -> str:
    """``line`` re-rendered without the bare ``[label]`` codes in it."""
    out: list[str] = []
    for item in parse_text(line):
        if isinstance(item, TextRun):
            out.append(escape_text(item.text))
        elif not (label and _is_code(item, label)):
            words = " ".join(item.words)
            out.append(f"[{item.label}{' ' + words if words else ''}]")
    return "".join(out)


def extract(
    data: bytes, config: BlockConfig, tables: TableSet, registry=None
) -> Extraction:
    """Cut ``data`` into strings. Pointer sources need a ``registry`` for mappings."""
    bits = Bits(data)
    source = config.source
    if isinstance(source, RangeSource):
        return _extract_range(bits, config, tables, source)
    if isinstance(source, PointerTableSource | PointerListSource):
        return _extract_pointers(bits, config, tables, source, registry)
    raise TypeError(f"unknown source {source!r}")


def _read_pointers(
    data: bytes, source: PointerTableSource | PointerListSource, registry
) -> tuple[list[PointerRef], list[int | None], list[Notice]]:
    """Every pointer of the source with its target offset (None when unmapped)."""
    mapping = mapping_for(source, registry)
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
        target = pointer_target(mapping, source, value, address, len(data))
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


def pointer_target(
    mapping, source: PointerTableSource | PointerListSource, value, address, size
) -> int | None:
    """Where a pointer at ``address`` holding ``value`` points in data of
    ``size`` bytes: mapped, moved by the source's offset, and ``None`` when it
    lands outside."""
    target = mapping.to_offset(value, source.bank, address)
    if target is None:
        return None
    target += source.offset
    return target if 0 <= target < size else None


def _extract_pointers(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    source: PointerTableSource | PointerListSource,
    registry,
) -> Extraction:
    refs, targets, notices = _read_pointers(bits.data, source, registry)
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
        nxt = ordered[i + 1] * 8 if i + 1 < len(ordered) else None
        if isinstance(st, NextPointer) and nxt is not None and nxt > start:
            # The string owns every bit up to the next pointer's target.
            limit = min(limit, nxt)
            r = decode(bits, tables, start, _rules(config, limit, False))
            tokens, end, res_notices = r.tokens, limit, r.notices
        else:
            # A fixed string keeps its whole extent even when it stops early
            # at an end token, so its slot stays whole.
            tokens, end, res_notices = decode_one(bits, config, tables, start, limit)
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


def string_at(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    start_bit: int,
    limit: int | None = None,
) -> list[Token]:
    """The one string of the block's string type that starts at ``start_bit``,
    reading no further than ``limit`` bits past it.

    What a pointer's target reads as, on its own: a string that ends at the
    next pointer's target has no next pointer here, so it reads to an end token.
    """
    if isinstance(config.string_type, NextPointer):
        config = replace(config, string_type=EndToken())
    stop_bit = bits.length if config.bound is None else config.bound * 8
    if stop_bit <= start_bit:
        stop_bit = bits.length
    if limit is not None:
        stop_bit = min(stop_bit, start_bit + limit)
    return decode_one(bits, config, tables, start_bit, stop_bit)[0]


def _decode_pascal(bits, config, tables, start, stop_bit, st: Pascal):
    length_bits = st.width * 8
    chunk = bits.window(start, length_bits)
    if len(chunk) < length_bits:
        return (
            [],
            start,
            [Notice("Pascal length past the end of the data", offset=start // 8)],
        )
    raw = bits_to_bytes(chunk)
    n = int.from_bytes(raw, "big" if st.endian == "big" else "little")
    body = start + length_bits
    if st.counts_tokens:
        return _decode_counted(bits, config, tables, body, stop_bit, n)
    limit = min(body + n * 8, stop_bit)
    r = decode(bits, tables, body, _rules(config, limit, False))
    return r.tokens, limit, r.notices


def _rules(config: BlockConfig, limit_bit: int, end_terminated: bool) -> DecodeRules:
    m, o = config.realign
    st = config.string_type
    return DecodeRules(
        end_terminated=end_terminated,
        limit_bit=limit_bit,
        skips=tuple((a * 8, b * 8) for a, b in config.skips),
        realign=(m * 8, o * 8),
        line_label=config.line_label,
        max_lines=st.count if isinstance(st, Lines) else 0,
    )


def _extract_range(
    bits: Bits, config: BlockConfig, tables: TableSet, source: RangeSource
) -> Extraction:
    strings: list[StringRecord] = []
    notices: list[Notice] = []
    stop_bit = min(source.stop * 8, bits.length)
    pos = source.start * 8
    if isinstance(config.string_type, NextPointer):
        notices.append(
            Notice("'next pointer' needs a pointer source; reading to end tokens")
        )
    while pos < stop_bit:
        start = pos
        tokens, record_end, res_notices = decode_one(
            bits, config, tables, start, stop_bit
        )
        if record_end <= start:
            break
        strings.append(
            StringRecord(len(strings), start, record_end, tokens, notices=res_notices)
        )
        pos = record_end
    return Extraction(strings, notices)


def decode_one(
    bits: Bits, config: BlockConfig, tables: TableSet, start: int, stop_bit: int
) -> tuple[list[Token], int, list[Notice]]:
    """One string of the block's string type, read within ``stop_bit``."""
    st = config.string_type
    if isinstance(st, FixedLength):
        limit = min(start + st.length * 8, stop_bit)
        return _decode_fixed(bits, config, tables, start, limit)
    if isinstance(st, Pascal):
        return _decode_pascal(bits, config, tables, start, stop_bit, st)
    if isinstance(st, Lines):
        r = decode(bits, tables, start, _rules(config, stop_bit, True))
        return r.tokens, r.end_bit, r.notices
    return _decode_terminated(bits, config, tables, start, stop_bit)


def _decode_terminated(
    bits: Bits, config: BlockConfig, tables: TableSet, start: int, stop_bit: int
) -> tuple[list[Token], int, list[Notice]]:
    """One string of ``strings_per_pointer`` end-token runs.

    Not :func:`~mapchar.engines.decode.decode_run`: a backwards skip range
    moves a run's end behind its start, which stops that function early.
    """
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
        used += token.pascal_weight
        end = token.bit_end
    return tokens, end, r.notices
