"""Run a block configuration over a buffer and cut it into strings."""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass, replace

from mapchar.core.bits import Bits, bits_to_bytes
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    Extraction,
    FixedLength,
    Lines,
    NestedPointerSource,
    NextPointer,
    Pascal,
    PointerListSource,
    PointerRef,
    PointerSource,
    PointerTableSource,
    RangeSource,
    StringRecord,
    is_fill,
)
from mapchar.core.mapping import read_pointer
from mapchar.core.notices import Notice
from mapchar.core.table import Entry, TableSet, TokenKind
from mapchar.core.tokens import (
    CodeRef,
    TextRun,
    Token,
    escape_text,
    parse_text,
    render,
)
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
    """Cut ``data`` into strings. Pointer sources need a ``registry`` for mappings.

    Every string's original is seeded from its bytes; a block the project
    already knows puts the originals it saved back over them.
    """
    bits = Bits(data)
    source = config.source
    if isinstance(source, RangeSource):
        ex = _extract_range(bits, config, tables, source)
    elif isinstance(
        source, PointerTableSource | PointerListSource | NestedPointerSource
    ):
        ex = _extract_pointers(bits, config, tables, source, registry)
    else:
        raise TypeError(f"unknown source {source!r}")
    for rec in ex.strings:
        rec.original = rec.current_text()
    return ex


def reextract(
    data: bytes,
    config: BlockConfig,
    tables: TableSet,
    strings: list[StringRecord],
    lo: int,
    hi: int,
    registry=None,
) -> Extraction | None:
    """The block read again after bytes ``lo`` to ``hi`` of ``data`` changed,
    reading only what those bytes can have changed; ``None`` when that takes
    reading the whole block (:func:`extract`).

    Only a nested source's reading comes apart: each group lies between its
    inner table and the next thing the outer table points at, so the groups a
    change reaches are read again and every other string is the record it was.
    A change to the outer table, or a group that now reads as another number
    of strings, has no such answer.
    """
    source = config.source
    if not isinstance(source, NestedPointerSource) or not strings:
        return None
    if lo < source.stop and source.start < hi:
        return None
    records, notices = nested_records(data, source, registry)
    marks = sorted({r.table for r in records} | {r.base for r in records})
    bases = set()
    for r in records:
        at = bisect_right(marks, r.base)
        end = marks[at] if at < len(marks) else len(data)
        if lo < end and r.table < hi:
            bases.add(r.base)
    old: dict[int, list[StringRecord]] = {}
    for rec in strings:
        if not rec.pointers:
            return None
        old.setdefault(rec.pointers[0].offset, []).append(rec)
    part = _extract_pointers(Bits(data), config, tables, source, registry, bases)
    new: dict[int, list[StringRecord]] = {}
    for rec in part.strings:
        new.setdefault(rec.pointers[0].offset, []).append(rec)
    placed: dict[int, StringRecord] = {}
    for base in bases | set(new):
        before, after = old.get(base, []), new.get(base, [])
        if len(before) != len(after):
            return None
        for was, now in zip(before, after, strict=True):
            now.index = was.index
            now.original = now.current_text()
            placed[was.index] = now
    return Extraction(
        [placed.get(rec.index, rec) for rec in strings],
        notices + [n for n in part.notices if n not in notices],
    )


@dataclass(frozen=True)
class NestedRecord:
    """One record of a nested source's outer table, both pointers mapped."""

    address: int
    """Where the record's first outer pointer sits."""
    table: int
    """Where its inner table starts."""
    base: int
    """What its inner pointers count from; where the inner table stops."""


def nested_records(
    data: bytes, source: NestedPointerSource, registry=None
) -> tuple[list[NestedRecord], list[Notice]]:
    """The records of a nested source's outer table that name an inner table,
    and what reading the rest had to say. A record holding the null value in
    either pointer names none and says nothing."""
    notices: list[Notice] = []
    mapping = mapping_for(source, registry)
    if mapping is None:
        return [], [Notice(f"unknown mapping {source.mapping_id!r}")]
    records: list[NestedRecord] = []
    size = source.size
    for address in range(source.start, source.stop, max(source.stride, 1)):
        values = [
            read_pointer(data, address + at, size, source.endian) for at in (0, size)
        ]
        if None in values:
            notices.append(Notice("pointer past the end of the data", offset=address))
            continue
        if source.null is not None and source.null in values:
            continue
        table, base = (
            pointer_target(mapping, source, v, address + at, len(data))
            for v, at in zip(values, (0, size), strict=True)
        )
        if table is None or base is None:
            notices.append(
                Notice("record's pointers map outside the data", offset=address)
            )
            continue
        if base < table:
            notices.append(
                Notice(f"inner table ${table:X} lies past its base", offset=address)
            )
            continue
        records.append(NestedRecord(address, table, base))
    return records, notices


def pointer_addresses(
    data: bytes, source: PointerSource, registry=None
) -> list[tuple[int, int]]:
    """Every pointer a source reads, as ``(address, size)``: a nested source's
    outer pointers and its inner tables' as well as a table's or list's own."""
    if isinstance(source, PointerTableSource):
        step = max(source.stride, 1)
        return [(a, source.size) for a in range(source.start, source.stop, step)]
    if isinstance(source, PointerListSource):
        return [(a, source.size) for a in source.addresses]
    records, _ = nested_records(data, source, registry)
    out = [
        (a + at, source.size)
        for a in range(source.start, source.stop, max(source.stride, 1))
        for at in (0, source.size)
    ]
    for rec in records:
        out += [
            (a, source.inner_size)
            for a in range(
                rec.table, rec.base - source.inner_size + 1, source.inner_size
            )
        ]
    return out


def _read_nested(
    data: bytes, source: NestedPointerSource, registry, bases=None
) -> tuple[list[PointerRef], list[int | None], list[Notice]]:
    """Every inner pointer of a nested source with its target offset: its value
    counted from its record's base. With ``bases``, only the records counting
    from one of those."""
    records, notices = nested_records(data, source, registry)
    if bases is not None:
        records = [r for r in records if r.base in bases]
    refs: list[PointerRef] = []
    targets: list[int | None] = []
    width = source.inner_size
    for rec in records:
        for address in range(rec.table, rec.base - width + 1, width):
            value = read_pointer(data, address, width, source.inner_endian)
            if value is None:
                continue
            if value == source.inner_null:
                continue
            target = rec.base + value
            if target >= len(data):
                notices.append(
                    Notice(f"pointer ${value:X} maps outside the data", offset=address)
                )
                target = None
            refs.append(
                PointerRef(
                    address, width, source.inner_endian, "linear", rec.base, value
                )
            )
            targets.append(target)
    return refs, targets, notices


def _read_pointers(
    data: bytes, source: PointerSource, registry, bases=None
) -> tuple[list[PointerRef], list[int | None], list[Notice]]:
    """Every pointer of the source with its target offset (None when unmapped).

    A pointer holding the source's null value reaches no string and is left
    out."""
    if isinstance(source, NestedPointerSource):
        return _read_nested(data, source, registry, bases)
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
        if value == source.null:
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


def pointer_target(mapping, source: PointerSource, value, address, size) -> int | None:
    """Where a pointer at ``address`` holding ``value`` points in data of
    ``size`` bytes: mapped, moved by the source's offset, and ``None`` when it
    lands outside."""
    target = mapping.to_offset(value, source.bank, address)
    if target is None:
        return None
    target += source.offset
    return target if 0 <= target < size else None


def padding_bits(config: BlockConfig, tables: TableSet) -> str | None:
    """The block's fill pattern as bits when a run of it is padding, else
    ``None``.

    A fill byte no entry of the start table can begin a token with is padding
    wherever it sits between strings: nothing in the block reads as it, so
    what a shorter replacement left behind is safe to pass over. A fill byte
    the table does map is text — a string may begin with it, or be nothing but
    it — and every byte of it is read, which is why a block's fill byte should
    be one no string begins with.
    """
    pad = "".join(format(b, "08b") for b in config.fill)
    for key in tables.start.entries:
        if key.startswith(pad) or pad.startswith(key):
            return None
    return pad


def _without_padding(bits: Bits, start: int, limit: int, pad: str | None) -> int:
    """``limit`` pulled back over the run of whole fill patterns that ends
    there."""
    if pad is None:
        return limit
    width = len(pad)
    while (
        limit - width >= start
        and limit % 8 == 0
        and bits.window(limit - width, width) == pad
    ):
        limit -= width
    return limit


def _extract_pointers(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    source: PointerSource,
    registry,
    bases=None,
) -> Extraction:
    refs, targets, notices = _read_pointers(bits.data, source, registry, bases)
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
    pad = padding_bits(config, tables) if isinstance(st, NextPointer) else None
    for i, target in enumerate(ordered):
        start = target * 8
        if start >= bits.length:
            continue
        limit = stop_bit if stop_bit > start else bits.length
        nxt = ordered[i + 1] * 8 if i + 1 < len(ordered) else None
        if (
            nxt is not None
            and isinstance(source, NestedPointerSource)
            and by_target[ordered[i + 1]][0].offset != by_target[target][0].offset
        ):
            # The next group's first string is not where this group's last ends.
            nxt = None
        if isinstance(st, NextPointer) and nxt is not None and nxt > start:
            # The string owns every bit up to the next pointer's target, and
            # keeps them whatever it says: the padding a shorter replacement
            # left at its end is not text, so it is not read, but the slot
            # stays whole so the string can grow back into it.
            limit = min(limit, nxt)
            read_to = _without_padding(bits, start, limit, pad)
            r = decode(bits, tables, start, _rules(config, read_to, False))
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
    """Consecutive strings from ``start`` to ``stop``.

    A string written shorter than the one it replaced leaves its slot padded
    with the block's fill byte, and the next string starts after the padding:
    a run of the fill byte between two strings is passed over when it can only
    be padding (:func:`padding_bits`) — a fill byte the table maps is text and
    is read like any other. A fixed-length string keeps its whole slot, so
    nothing is skipped.
    """
    strings: list[StringRecord] = []
    notices: list[Notice] = []
    stop_bit = min(source.stop * 8, bits.length)
    pos = source.start * 8
    if isinstance(config.string_type, NextPointer):
        notices.append(
            Notice("'next pointer' needs a pointer source; reading to end tokens")
        )
    pad = padding_bits(config, tables) if config.fixed_length is None else None
    width = len(pad) if pad is not None else 0
    while pos < stop_bit:
        start = pos
        if pad is not None and strings and start % 8 == 0:
            while start + width <= stop_bit and bits.window(start, width) == pad:
                start += width
            if start >= stop_bit:
                break
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
    ended_at: int | None = None
    """Where the end token the string stopped at ends."""
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
                ended_at = r.end_bit
                break
            pos = piece_limit
    else:
        r = decode(bits, tables, start, _rules(config, limit, stop_at_end))
        tokens.extend(r.tokens)
        notices.extend(r.notices)
        if stop_at_end and r.ended_by is EndedBy.END_TOKEN:
            ended_at = r.end_bit
    if ended_at is not None and tokens and tokens[-1].is_end:
        _after_end(bits, config, tables, tokens, ended_at, limit)
    if config.show_end:
        tokens.append(artificial(config.end_label, limit))
    return tokens, limit, notices


def _after_end(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    tokens: list[Token],
    end_bit: int,
    limit: int,
) -> None:
    """A fixed string that stopped at an end token: the end token and the fill
    after it are not text, so the end token is kept hidden
    (:func:`is_hidden_end`) and the string reads as what it says. A tail that is
    anything but fill is text the string must write back as it is, so the end
    token stays in view and the tail is read after it, fill and all."""
    tail = bits.window(end_bit, limit - end_bit)
    if (
        end_bit % 8 == 0
        and len(tail) % 8 == 0
        and is_fill(bits_to_bytes(tail), config.fill)
    ):
        tokens[-1] = replace(tokens[-1], fallback=True)
        return
    if limit > end_bit:
        rules = replace(_rules(config, limit, False), max_lines=0)
        tokens.extend(decode(bits, tables, end_bit, rules).tokens)


def is_hidden_end(token: Token) -> bool:
    """Whether ``token`` is a fixed string's end token kept out of its text: its
    bits are there, and it renders as nothing."""
    return token.fallback and token.is_end


def legacy_fixed_text(rec: StringRecord, config: BlockConfig) -> str:
    """The text a fixed string that stops at an end token was shown as before
    its end token and fill were hidden: through its end token, and nothing of
    what follows it but a final artificial end code. What a project saved by
    that build holds as the string's original."""
    out: list[Token] = []
    for token in rec.tokens:
        if token.is_end:
            out.append(replace(token, fallback=False))
            if config.show_end and rec.tokens[-1] is not token:
                out.append(rec.tokens[-1])
            return render(out)
        out.append(token)
    return render(out)


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


def respell_fixed_end(
    text: str, rec: StringRecord, config: BlockConfig, tables: TableSet
) -> str:
    """A text saved for ``rec`` by a build that showed a fixed string's end
    token (:func:`legacy_fixed_text`), spelled as the string reads today.

    The text that build read from the bytes as they are is today's reading of
    them; any other — a translation, or an original the bytes no longer hold —
    loses the end token it closes with, which the string now writes on its
    own.
    """
    st = config.string_type
    if not (isinstance(st, FixedLength) and st.stop_at_end):
        return text
    if text == legacy_fixed_text(rec, config):
        return rec.current_text()
    body, after = text, ""
    if config.show_end:
        trimmed = text.rstrip("\n")
        items = parse_text(trimmed)
        if items and _is_code(items[-1], config.end_label):
            at = trimmed.rfind("[")
            body, after = trimmed[:at], text[at:]
    labels = {
        e.label
        for e in tables.start.entries.values()
        if e.kind is TokenKind.END and e.label
    }
    trimmed = body.rstrip("\n")
    items = parse_text(trimmed)
    last = items[-1] if items else None
    if isinstance(last, CodeRef) and not last.words and last.label in labels:
        body = trimmed[: trimmed.rfind("[")]
    return body + after
