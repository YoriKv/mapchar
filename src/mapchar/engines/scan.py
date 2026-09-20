"""Text-likeness scan: where in a file a table decodes something readable."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from mapchar.core.bits import Bits
from mapchar.core.table import TableSet, TokenKind
from mapchar.core.text import fold
from mapchar.core.tokens import plain_text
from mapchar.engines.decode import DecodeRules, decode

WORDS = frozenset(
    """the and you are for with this that have not from your what will can all
    was one our out get has him his her she they them there their here where
    when who how why but who now way too very just into over than then only
    into been more some like time back good know come make take give find
    yes no thank thanks hello sorry please help king queen sword magic gold
    castle town shop item items level power attack defend heal cure save load
    start end game new continue exit""".split()
)

MAX_HEADER = 4
"""Bytes in front of a record's length a chain is looked for with, at most."""
MIN_RECORDS = 3
"""Records in a row before a chain counts as one: with fewer, a small byte
inside ordinary text reads as a length."""
MIN_CHAIN = 16
"""Bytes a chain covers before it counts as one."""


@dataclass(frozen=True)
class Records:
    """Strings cut by a length prefix: ``header`` bytes that are not text — a
    screen position, an id, flags — then a length in ``width`` bytes, then that
    many characters, over and over. The scan only ever finds one-byte
    prefixes."""

    header: int = 0
    width: int = 1


@dataclass
class Region:
    start: int
    end: int
    score: float
    terminator: int | None = None
    """The byte most often ending a text run in the region."""
    initial: int | None = None
    """The byte most often beginning a run after the terminator."""
    records: Records | None = None
    """How the region cuts its strings when they are a chain of length-prefixed
    records; ``None`` when they are terminated text."""

    @property
    def length(self) -> int:
        return self.end - self.start


def score_window(data: bytes, tables: TableSet) -> tuple[float, list]:
    """The text-likeness of ``data`` under ``tables`` and its tokens."""
    if not data:
        return 0.0, []
    bits = Bits(data)
    r = decode(bits, tables, 0, DecodeRules(end_terminated=False))
    text_bits = 0
    unmatched = 0
    chars: list[str] = []
    for t in r.tokens:
        if t.entry is None:
            unmatched += t.bit_end - t.bit_start
            chars.append(" ")
        elif t.entry.kind is TokenKind.TEXT:
            text_bits += t.bit_end - t.bit_start
            chars.append(plain_text(t.entry.text))
        else:
            chars.append(" ")
    fraction = text_bits / bits.length
    words = fold("".join(chars)).split()
    hits = sum(1 for w in words if w in WORDS)
    score = fraction + min(0.3, 0.05 * hits) - 0.5 * (unmatched / bits.length) ** 2
    return max(0.0, min(1.0, score)), r.tokens


def scan(
    data: bytes,
    tables: TableSet,
    *,
    window: int = 64,
    step: int = 32,
    threshold: float = 0.6,
    progress: Callable[[int, int], bool] | None = None,
) -> list[Region]:
    """Regions whose windows score at least ``threshold``, cut to what they
    hold and ranked.

    The windows say where to look; what comes back is what is there
    (:func:`_refine`): a record chain over its own ends, or terminated text
    over whole strings, never the window grid.
    """
    scores: list[tuple[int, float]] = []
    for at in range(0, max(len(data) - 1, 1), step):
        if (
            progress is not None
            and at % (step * 64) == 0
            and not progress(at, len(data))
        ):
            break
        score, _ = score_window(data[at : at + window], tables)
        scores.append((at, score))
    coarse: list[tuple[int, int]] = []
    merging = False
    for at, score in scores:
        if score < threshold:
            merging = False
            continue
        end = min(at + window, len(data))
        if merging and at <= coarse[-1][1]:
            coarse[-1] = (coarse[-1][0], end)
        else:
            coarse.append((at, end))
        merging = True
    if not coarse:
        return []
    text = _text_bytes(tables)
    runs = _text_runs(data, text)
    regions: list[Region] = []
    for start, end in coarse:
        regions.extend(_refine(data, tables, runs, text, start, end, window, threshold))
    regions = _uncontained(regions)
    for region in regions:
        if region.records is None:
            region.terminator, region.initial = _guess_terminator(
                data[region.start : region.end], tables
            )
    regions.sort(key=lambda r: (r.score, r.length), reverse=True)
    return regions


def _refine(
    data: bytes,
    tables: TableSet,
    runs: list[int],
    text: list[bool],
    start: int,
    end: int,
    window: int,
    threshold: float,
) -> list[Region]:
    """One coarse region as the readings it really holds.

    Every record chain it meets comes out over the chain's own ends, which a
    chain that began before the first window to score reaches by walking back;
    what the chains leave of it comes out as terminated text over whole
    strings. A chain is looked for a window either side, since the grid cuts
    both ends of one, and the text is only followed forward, since a window
    that scored already begins at or before its text.
    """
    reach = min(len(data), end + window)
    chains = [
        chain
        for chain in _record_chains(data, runs, text, max(0, start - window), reach)
        if chain[0] < end and chain[1] > start
    ]
    regions = [
        Region(
            a, b, score_window(_record_text(data, a, b, rec), tables)[0], records=rec
        )
        for a, b, rec in chains
    ]
    at = start
    for a, b in [(c[0], c[1]) for c in chains] + [(reach, reach)]:
        bounds = _string_bounds(data, tables, at, a) if a > at else None
        if bounds is not None and bounds[0] < end and bounds[1] > start:
            score = score_window(data[bounds[0] : bounds[1]], tables)[0]
            if score >= threshold:
                regions.append(Region(bounds[0], bounds[1], score))
        at = max(at, b)
    return regions


def _uncontained(regions: list[Region]) -> list[Region]:
    """Every region but those another already covers: two coarse regions that
    reach the same chain each find the whole of it."""
    kept: list[Region] = []
    for region in sorted(regions, key=lambda r: (r.start, -r.length)):
        if not any(k.start <= region.start and region.end <= k.end for k in kept):
            kept.append(region)
    return kept


def _text_bytes(tables: TableSet) -> list[bool]:
    """Which of the 256 bytes the start table reads as one text character."""
    out: list[bool] = []
    for b in range(256):
        entry = tables.start.match(format(b, "08b"))
        out.append(
            entry is not None and entry.kind is TokenKind.TEXT and len(entry.bits) == 8
        )
    return out


def _text_runs(data: bytes, text: list[bool]) -> list[int]:
    """How many text bytes run on from each offset, so that "the next ``n``
    bytes are all text" is one comparison."""
    runs = [0] * (len(data) + 1)
    for i in range(len(data) - 1, -1, -1):
        runs[i] = runs[i + 1] + 1 if text[data[i]] else 0
    return runs


def _record_chains(
    data: bytes, runs: list[int], text: list[bool], lo: int, hi: int
) -> list[tuple[int, int, Records]]:
    """Maximal chains of length-prefixed records meeting ``[lo, hi)``.

    A record is a header of 0 to :data:`MAX_HEADER` bytes, a one-byte length
    and that many bytes of text; a chain is :data:`MIN_RECORDS` of them in a
    row, back to back, all with the same header. At least one byte of a
    record's header and length has to be one the table does not read as text,
    or every run of text would count as a chain of its own.

    Chains are found a header at a time and the longest wins the bytes it
    covers, since one length byte is also another header's.
    """
    found: list[tuple[int, int, Records]] = []
    for header in range(MAX_HEADER + 1):
        step = header + 1

        def is_record(p: int, step: int = step, header: int = header) -> bool:
            if p < 0 or p + step > len(data):
                return False
            length = data[p + header]
            if not 0 < length <= runs[p + step]:
                return False
            return any(not text[b] for b in data[p : p + step])

        def walk(p: int, step: int = step, header: int = header) -> tuple[int, int]:
            count = 0
            while is_record(p):
                p += step + data[p + header]
                count += 1
            return count, p

        def back(p: int, step: int = step, header: int = header) -> int:
            """The start of the chain ``p`` continues: the record before it,
            for as long as there is one."""
            while True:
                for q in range(max(0, p - step - 0xFF), p - step + 1):
                    if is_record(q) and q + step + data[q + header] == p:
                        p = q
                        break
                else:
                    return p

        at = lo
        while at < hi:
            count, _ = walk(at)
            if count >= MIN_RECORDS:
                start = back(at)
                count, end = walk(start)
                if end - start >= MIN_CHAIN:
                    found.append((start, end, Records(header)))
                at = max(end, at + 1)
            else:
                at += 1
    taken: list[tuple[int, int]] = []
    chains: list[tuple[int, int, Records]] = []
    for start, end, rec in sorted(found, key=lambda c: (c[0] - c[1], c[2].header)):
        if not any(start < b and a < end for a, b in taken):
            taken.append((start, end))
            chains.append((start, end, rec))
    return sorted(chains, key=lambda c: c[0])


def _record_text(data: bytes, start: int, end: int, rec: Records) -> bytes:
    """The characters a chain's records hold, without their headers: what the
    chain is scored on, since its headers are nobody's text."""
    out = bytearray()
    at = start
    while at < end:
        length = int.from_bytes(data[at + rec.header :][: rec.width], "little")
        at += rec.header + rec.width
        out += data[at : at + length]
        at += length
    return bytes(out)


def _string_bounds(
    data: bytes, tables: TableSet, lo: int, hi: int
) -> tuple[int, int] | None:
    """The longest run of whole readable strings in ``[lo, hi)``.

    A string runs to an end token, and one holding anything the table does not
    read as text is no part of the run: either the region's edge cut it or the
    bytes are not text at all. The run begins at the first character after the
    last of those bytes, though — a string whose front the run-up ate is still
    a string — and ends at the last end token a run of characters reached.
    """
    if hi <= lo:
        return None
    _, tokens = score_window(data[lo:hi], tables)
    best: tuple[int, int] | None = None
    after_junk = lo
    run_start: int | None = None
    start: int | None = None
    clean = True
    seen = False
    for t in tokens:
        kind = t.entry.kind if t.entry is not None else None
        if start is None:
            start = lo + t.bit_start // 8
        if kind is TokenKind.TEXT:
            seen = True
        elif kind is not TokenKind.END:
            clean = False
            after_junk = lo + -(-t.bit_end // 8)
        if kind is TokenKind.END:
            stop = lo + -(-t.bit_end // 8)
            if clean and seen and start is not None:
                begin = run_start if run_start is not None else min(start, after_junk)
                run_start = begin
                if best is None or stop - begin > best[1] - best[0]:
                    best = (begin, stop)
            else:
                run_start = None
            start, clean, seen = None, True, False
    return best


def _guess_terminator(data: bytes, tables: TableSet) -> tuple[int | None, int | None]:
    """The unmatched or end byte most often followed by text, and what follows it."""
    _, tokens = score_window(data, tables)
    enders: Counter[int] = Counter()
    initials: Counter[int] = Counter()
    for a, b in zip(tokens, tokens[1:], strict=False):
        a_is_break = a.entry is None or a.entry.kind is TokenKind.END
        b_is_text = b.entry is not None and b.entry.kind is TokenKind.TEXT
        if a_is_break and b_is_text and a.bit_end - a.bit_start == 8:
            enders[int(a.bits, 2)] += 1
            initials[data[b.bit_start // 8]] += 1
    terminator = enders.most_common(1)[0][0] if enders else None
    initial = initials.most_common(1)[0][0] if initials else None
    return terminator, initial
