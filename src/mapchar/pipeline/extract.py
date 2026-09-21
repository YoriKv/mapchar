"""Run a block configuration over a buffer and cut it into strings."""

from __future__ import annotations

from bisect import bisect_right
from collections.abc import Container
from dataclasses import replace

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
    bits_digest,
    fill_reads_as_padding,
    grouped_strings,
)
from mapchar.core.fill import fill_bits, is_fill
from mapchar.core.notices import Notice
from mapchar.core.table import TableEntry, TableSet, TokenKind
from mapchar.core.tokens import (
    CodeRef,
    TextRun,
    Token,
    escape_text,
    parse_text,
    render,
)
from mapchar.engines.decode import (
    DecodeResult,
    DecodeRules,
    EndedBy,
    advance,
    decode,
    follow_skips,
)
from mapchar.pipeline.pointers import nested_records, read_pointers


def artificial(label: str, bit: int) -> Token:
    """A zero-width code token that stands for a boundary, not for bytes.

    It carries a line break like Cartographer's ``LINE CTRL\n``, so dumps of
    fixed strings read line by line.
    """
    text = f"[{label}]\\n" if label else "\\n"
    return Token("", bit, bit, TableEntry("", TokenKind.TEXT, text))


def strip_artificial(text: str, config: BlockConfig) -> list[str]:
    """The lines of a dumped string with the artificial codes taken out.

    A dump closes a fixed string with an artificial end code and breaks its
    fixed lines with artificial line codes; neither stands for bytes, so
    neither is encoded. Only a final end code is artificial -- one earlier in
    the text is the table's own end token and stays.
    """
    if config.show_end:
        text = _split_end_code(text, (config.end_label,))[0]
    drop = config.line_label if config.line_length else ""
    return [_without_code(line, drop) for line in text.split("\n")]


def _split_end_code(text: str, labels: Container[str]) -> tuple[str, str]:
    """``text`` cut in two at the bare end code that closes it: what stands in
    front of the code, and the code with whatever follows it.

    ``(text, "")`` for a text that ends in none of ``labels``. Trailing line
    breaks are not part of the code — they are looked past to find it, and then
    go with the half they stand in.
    """
    trimmed = text.rstrip("\n")
    items = parse_text(trimmed)
    last = items[-1] if items else None
    if not (isinstance(last, CodeRef) and not last.words and last.label in labels):
        return text, ""
    at = trimmed.rfind("[")
    return text[:at], text[at:]


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
        _seed_original(rec, bits)
    return ex


def _seed_original(rec: StringRecord, bits: Bits) -> None:
    """A string's original as its bytes say it now, with their digest."""
    rec.digest = bits_digest(bits.window(rec.start_bit, rec.end_bit - rec.start_bit))
    rec.original = rec.current_text()
    rec.original_digest = rec.digest


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
    old = dict(grouped_strings(config, strings))
    bits = Bits(data)
    part = _extract_pointers(bits, config, tables, source, registry, bases)
    new = dict(grouped_strings(config, part.strings))
    placed: dict[int, StringRecord] = {}
    for base in bases | set(new):
        before, after = old.get(base, []), new.get(base, [])
        if len(before) != len(after):
            return None
        for was, now in zip(before, after, strict=True):
            now.index = was.index
            _seed_original(now, bits)
            placed[was.index] = now
    return Extraction(
        [placed.get(rec.index, rec) for rec in strings],
        notices + [n for n in part.notices if n not in notices],
        part.inner_tables,
    )


def padding_bits(config: BlockConfig, tables: TableSet) -> str | None:
    """The block's fill pattern as bits when a run of it is padding
    (:func:`~mapchar.core.block.fill_reads_as_padding`, where the rule is), and
    ``None`` when the block reads the fill as text: what the reading passes
    over between strings, spelled the way :meth:`Bits.window` spells it."""
    if not fill_reads_as_padding(config, tables):
        return None
    return fill_bits(config.fill)


def pad_run(bits: Bits, pos: int, pad: str, limit: int) -> int:
    """The bits of whole fill patterns from ``pos``, reading no further than
    ``limit``: what a reading passes over between strings (:func:`padding_bits`).
    """
    width = len(pad)
    end = pos
    while end + width <= limit and bits.window(end, width) == pad:
        end += width
    return end - pos


def _without_padding(bits: Bits, start: int, limit: int, pad: str | None) -> int:
    """``limit`` pulled back over the run of whole fill patterns that ends
    there: :func:`pad_run` the other way about, for the tail of a string that
    owns its slot to the next pointer's target."""
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
    refs, targets, notices = read_pointers(bits.data, source, registry, bases)
    inner_tables: dict[int, int] = {}
    if isinstance(source, NestedPointerSource):
        # Each group is keyed by the base its pointers count from
        # (:func:`~mapchar.core.block.string_groups`); this is how that key
        # says which inner table reached it.
        records, _ = nested_records(bits.data, source, registry)
        inner_tables = {rec.base: rec.table for rec in records}
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
    nested = isinstance(source, NestedPointerSource)
    stops: dict[int | None, list[int]] = {}
    """The targets a run may stop at, in address order: the block's, or for a
    nested source those of the run's own group."""
    if config.reads_runs:
        for target in ordered:
            key = by_target[target][0].offset if nested else None
            stops.setdefault(key, []).append(target)
    for i, target in enumerate(ordered):
        start = target * 8
        if start >= bits.length:
            continue
        limit = stop_bit if stop_bit > start else bits.length
        if stops:
            key = by_target[target][0].offset if nested else None
            _read_run(
                bits,
                config,
                tables,
                start,
                limit,
                stops[key],
                by_target,
                strings,
                notices,
            )
            continue
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
    return Extraction(strings, notices, inner_tables)


def _read_run(
    bits: Bits,
    config: BlockConfig,
    tables: TableSet,
    start: int,
    stop_bit: int,
    targets: list[int],
    by_target: dict[int, list[PointerRef]],
    strings: list[StringRecord],
    notices: list[Notice],
) -> None:
    """The run of end-token strings the pointer to bit ``start`` reaches, added
    to ``strings``: the first carries the pointers, the rest none.

    A run ends at the string another of ``targets`` reaches, which is that
    pointer's; else after :attr:`~BlockConfig.strings_per_pointer` strings, or
    with :attr:`~BlockConfig.run_to_next` only once no target lies ahead — the
    last pointer's run. The next target is the first past where the string
    began, looked up string by string, since a skip range may have carried the
    reading anywhere. A target the run does not meet at the start of a string
    is a notice: the run was read some other way than the game reads it.
    """
    pos = start
    count = 0
    while pos < stop_bit:
        at = bisect_right(targets, pos // 8)
        nxt = targets[at] * 8 if at < len(targets) else None
        limit = stop_bit
        if config.run_to_next and nxt is not None:
            limit = min(limit, nxt)
        r = decode(bits, tables, pos, _rules(config, limit, True))
        if count and not r.tokens:
            break
        rec = StringRecord(
            len(strings),
            pos,
            r.end_bit,
            r.tokens,
            tuple(by_target[pos // 8]) if pos == start else (),
            notices=r.notices,
        )
        strings.append(rec)
        count += 1
        began, pos = pos, r.end_bit
        if r.ended_by is not EndedBy.END_TOKEN:
            if nxt is not None and pos == nxt == limit:
                notices.append(
                    Notice(
                        f"string #{rec.index} runs into the next pointer's "
                        f"target (${nxt // 8:X}) without an end token",
                        offset=began // 8,
                    )
                )
            break
        # Text packed in bits ends mid-byte and a pointer lands on a whole
        # one, so the bits up to the next byte are not a string's.
        reached = -(-pos // 8) * 8
        if nxt is not None and began < nxt <= reached:
            if pos > nxt:
                notices.append(
                    Notice(
                        f"the pointer target ${nxt // 8:X} lies inside string "
                        f"#{rec.index}, not at the start of one",
                        offset=began // 8,
                    )
                )
            break
        if pos % 8 == 0 and pos // 8 in by_target:
            break
        if not (config.run_to_next and nxt is not None):
            if count >= max(config.strings_per_pointer, 1):
                break


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
    """The prefix and the characters it counts are the string's bytes, so
    reading either steps over the block's skip ranges."""
    length_bits = st.width * 8
    skips = sorted((a * 8, b * 8) for a, b in config.skips)
    start = follow_skips(start, skips)
    chunk = bits.window(start, length_bits)
    if len(chunk) < length_bits:
        return (
            [],
            start,
            [Notice("Pascal length past the end of the data", offset=start // 8)],
        )
    raw = bits_to_bytes(chunk)
    n = int.from_bytes(raw, "big" if st.endian == "big" else "little")
    body = advance(start, length_bits, skips)
    if st.counts_tokens:
        return _decode_counted(bits, config, tables, body, stop_bit, n)
    # Just past the last byte counted: a skip starting right after it is the
    # next string's to follow, not this one's.
    end = advance(body, n * 8 - 8, skips) + 8 if n else body
    limit = min(end, stop_bit)
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
    skips = sorted((a * 8, b * 8) for a, b in config.skips)
    while pos < stop_bit:
        start = pos
        if pad is not None and strings and start % 8 == 0:
            start += pad_run(bits, start, pad, stop_bit)
            if start >= stop_bit:
                break
        # A string that begins on a skip range begins where it lands, and one
        # behind a record header begins past it — the header's own bytes step
        # over the skips between them.
        start = advance(start, config.record_header * 8, skips)
        if start >= stop_bit:
            break
        tokens, record_end, res_notices = decode_one(
            bits, config, tables, start, stop_bit
        )
        # A record of no bytes, or one that leaves the position where it was —
        # a header reading backwards — would be read again for ever.
        if record_end <= start or record_end <= pos:
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
    r = decode(bits, tables, start, _rules(config, stop_bit, True))
    return r.tokens, r.end_bit, r.notices


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
        body, after = _split_end_code(text, (config.end_label,))
    labels = {
        e.label
        for e in tables.start.entries.values()
        if e.kind is TokenKind.END and e.label
    }
    return _split_end_code(body, labels)[0] + after


def split_run_text(text: str, tables: TableSet) -> list[str]:
    """``text`` cut after each bare end code of ``tables`` in it: the strings
    of a run that a project from before version 3 kept as one, which read all
    of a pointer's run as a single string."""
    labels = {
        e.label
        for table in tables.tables.values()
        for e in table.entries.values()
        if e.kind is TokenKind.END and e.label
    }
    pieces: list[str] = []
    at = i = 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        close = text.find("]", i) if c == "[" else -1
        if close < 0:
            i += 1
            continue
        words = text[i + 1 : close].split()
        i = close + 1
        if len(words) == 1 and words[0] in labels:
            # The line break an end code renders with goes with its string.
            while text[i : i + 1] == "\n":
                i += 1
            pieces.append(text[at:i])
            at = i
    if at < len(text):
        pieces.append(text[at:])
    return pieces
