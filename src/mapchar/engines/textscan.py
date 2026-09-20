"""Text-likeness scan: where in a file a table decodes something readable."""

from __future__ import annotations

from bisect import bisect_right, insort
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
"""Records with characters in a row before a chain counts as one: with fewer,
a small byte inside ordinary text reads as a length. Terminated text has no
such rule — an intro or an ending is one long string."""
MIN_LENGTH = 16
"""Bytes a region covers before it counts as one, chain or terminated text."""
MIN_MEAN = 4
"""Characters a chain's records hold on average before it counts as one: a
header with a byte behind it is a table of positions, not of strings."""


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
    over whole strings, never the window grid. Both halves of the work report
    through ``progress`` and stop when it says to, so a stopped scan ranks
    what it had — and a scan stopped over the windows still cuts what scored,
    since Stop stays pressed and the cutting is the cheap half.
    """
    total = 2 * len(data)
    scores: list[tuple[int, float]] = []
    stopped = False
    for at in range(0, max(len(data) - 1, 1), step):
        if progress is not None and at % (step * 64) == 0 and not progress(at, total):
            stopped = True
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
    chains = _Chains(data, tables)
    regions: list[Region] = []
    for lo, hi in _spans(coarse, len(data), window, step):
        # Stop stays pressed, so a scan already stopped over the windows is
        # not asked again: what scored is cut all the same.
        if not stopped and progress is not None and not progress(len(data) + lo, total):
            break
        regions.extend(_refine(data, tables, chains, lo, hi, threshold))
    regions = _uncontained(_reached(regions, coarse))
    for region in regions:
        if region.records is None:
            region.terminator, region.initial = _guess_terminator(
                data[region.start : region.end], tables
            )
    regions.sort(key=lambda r: (r.score, r.length), reverse=True)
    return regions


def _spans(
    coarse: list[tuple[int, int]], size: int, window: int, step: int
) -> list[tuple[int, int]]:
    """Where the coarse regions are searched: each widened to what a reading of
    it may reach, and those that then meet merged into one.

    The grid cuts a chain at both ends, and a window that scored begins after
    its text as easily as before it — by up to a window or a step, whichever is
    the coarser. Merging what overlaps is what keeps one run of strings one
    region instead of two that cross.
    """
    spans: list[tuple[int, int]] = []
    for start, end in coarse:
        lo, hi = max(0, start - max(window, step)), min(size, end + window)
        if spans and lo <= spans[-1][1]:
            spans[-1] = (spans[-1][0], max(spans[-1][1], hi))
        else:
            spans.append((lo, hi))
    return spans


def _refine(
    data: bytes,
    tables: TableSet,
    chains: _Chains,
    lo: int,
    hi: int,
    threshold: float,
) -> list[Region]:
    """One span as the readings it really holds.

    Every record chain it meets comes out over the chain's own ends; what the
    chains leave of it comes out as terminated text over whole strings. Both
    have to reach ``threshold``, a chain on a score that discounts the bytes of
    it that are not characters, so a run of one-byte records is no string
    table and does not head the ranking either.
    """
    regions = [r for r in chains.meeting(lo, hi) if r.score >= threshold]
    at = lo
    for start, end in [(r.start, r.end) for r in regions] + [(hi, hi)]:
        if start > at:
            regions.extend(_string_runs(data, tables, at, min(start, hi), threshold))
        at = max(at, end)
    return regions


def _reached(regions: list[Region], coarse: list[tuple[int, int]]) -> list[Region]:
    """Only the regions a window that scored reaches: the spans are widened
    beyond the coarse regions, and what lies wholly outside them was never
    found, only passed over."""
    return [
        r
        for r in regions
        if any(start < r.end and r.start < end for start, end in coarse)
    ]


def _uncontained(regions: list[Region]) -> list[Region]:
    """Every region but those another already covers: two spans that reach the
    same chain each find the whole of it."""
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


def _has_end(tables: TableSet) -> bool:
    """Whether anything in the set ends a string. A table fresh from a relative
    search has no end token, and then a break between two runs of characters is
    all there is to cut strings on."""
    return any(
        entry.kind is TokenKind.END
        for table in tables.tables.values()
        for entry in table.entries.values()
    )


class _Chains:
    """Chains of length-prefixed records, found once each over the whole file.

    A record is a header of 0 to :data:`MAX_HEADER` bytes, a one-byte length
    and that many bytes of text; a chain is :data:`MIN_RECORDS` of them with
    characters in a row, back to back, all with the same header, covering
    :data:`MIN_LENGTH` bytes and holding :data:`MIN_MEAN` characters a record.
    At least one byte of a record's header and length has to be one the table
    does not read as text, or every run of text would count as a chain of its
    own. An empty record is crossed but never counted, and two in a row are no
    chain: a table may hold a blank slot, a run of zeroes is not a string
    table.

    Chains are found a header size at a time and the longest wins the bytes it
    covers, since one length byte is also another header's. Each span asks for
    the chains meeting it; a chain already found is never walked or scored
    again, which is what keeps a file whose chains are longer than its windows
    linear.
    """

    def __init__(self, data: bytes, tables: TableSet):
        self._data = data
        self._tables = tables
        self._text = _text_bytes(tables)
        self._runs = _text_runs(data, self._text)
        self._found: list[list[tuple[int, int]]] = [[] for _ in range(MAX_HEADER + 1)]
        self._regions: dict[tuple[int, int, int], Region] = {}

    def meeting(self, lo: int, hi: int) -> list[Region]:
        """The chains meeting ``[lo, hi)`` as regions, in order; the longest
        claims the bytes it covers, since one length byte is also another
        header's."""
        found: list[tuple[int, int, int]] = []
        for header in range(MAX_HEADER + 1):
            self._sweep(header, lo, hi)
            found += [
                (start, end, header)
                for start, end in self._found[header]
                if start < hi and end > lo
            ]
        taken: list[tuple[int, int, int]] = []
        for chain in sorted(found, key=lambda c: (c[0] - c[1], c[2])):
            if not any(chain[0] < b and a < chain[1] for a, b, _ in taken):
                taken.append(chain)
        return [self._region(*chain) for chain in sorted(taken)]

    def _region(self, start: int, end: int, header: int) -> Region:
        """A chain as a region, scored on its characters and on how little of
        its span is anything else.

        A chain's characters are text by construction, so reading them alone
        says nothing a table of screen positions would not say too; what tells
        the two apart is how much of the span the characters are, and squaring
        what they are not leaves an ordinary header alone while it costs a
        chain that is mostly header.
        """
        key = (start, end, header)
        region = self._regions.get(key)
        if region is None:
            rec = Records(header)
            text = _record_text(self._data, start, end, rec)
            waste = 1 - len(text) / max(end - start, 1)
            score = score_window(text, self._tables)[0] * (1 - waste**2)
            region = self._regions[key] = Region(start, end, score, records=rec)
        return region

    def _sweep(self, header: int, lo: int, hi: int) -> None:
        """Find every chain of this header size beginning in ``[lo, hi)``."""
        found = self._found[header]
        at = lo
        while at < hi:
            known = self._covering(found, at)
            if known is not None:
                at = max(known, at + 1)
                continue
            count, _, _ = self._walk(header, at)
            if count < MIN_RECORDS:
                at += 1
                continue
            start = self._back(header, at)
            count, end, chars = self._walk(header, start)
            if (
                count >= MIN_RECORDS
                and end - start >= MIN_LENGTH
                and chars >= MIN_MEAN * count
            ):
                insort(found, (start, end))
            at = max(end, at + 1)

    @staticmethod
    def _covering(found: list[tuple[int, int]], at: int) -> int | None:
        """The end of the chain already found over ``at``, if there is one."""
        i = bisect_right(found, (at, float("inf")))
        return found[i - 1][1] if i and found[i - 1][1] > at else None

    def _length(self, header: int, p: int) -> int | None:
        """The length of the record at ``p``, or ``None`` if there is none."""
        data, step = self._data, header + 1
        if p < 0 or p + step > len(data):
            return None
        length = data[p + header]
        if length > self._runs[p + step]:
            return None
        if all(self._text[b] for b in data[p : p + step]):
            return None
        return length

    def _walk(self, header: int, p: int) -> tuple[int, int, int]:
        """How many records with characters run on from ``p``, where the last
        of them ends, and how many characters they hold between them."""
        step, count, end, chars, empty = header + 1, 0, p, 0, False
        while True:
            length = self._length(header, p)
            if length is None or (length == 0 and empty):
                return count, end, chars
            p += step + length
            empty = length == 0
            if length:
                count, end, chars = count + 1, p, chars + length

    def _back(self, header: int, p: int) -> int:
        """The start of the chain ``p`` continues: the first record with
        characters of the run of records that reaches it."""
        data, start, empty = self._data, p, False
        while True:
            q = self._previous(header, p, empty)
            if q is None:
                return start
            p, empty = q, not data[q + header]
            if not empty:
                start = q

    def _previous(self, header: int, p: int, empty: bool) -> int | None:
        """The record ending at ``p``, if one does. The length a record at
        ``q`` would need is ``p - q`` less its header and length byte, so the
        byte that would hold it is one lookup."""
        data, step = self._data, header + 1
        for q in range(p - step, max(-1, p - step - 0x100), -1):
            length = p - q - step
            if data[q + header] != length or (length == 0 and empty):
                continue
            if self._length(header, q) is not None:
                return q
        return None


def _text_runs(data: bytes, text: list[bool]) -> list[int]:
    """How many text bytes run on from each offset, so that "the next ``n``
    bytes are all text" is one comparison."""
    runs = [0] * (len(data) + 1)
    for i in range(len(data) - 1, -1, -1):
        runs[i] = runs[i + 1] + 1 if text[data[i]] else 0
    return runs


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


def _string_runs(
    data: bytes, tables: TableSet, lo: int, hi: int, threshold: float
) -> list[Region]:
    """Every run of whole readable strings in ``[lo, hi)`` that is a region:
    one whole string over :data:`MIN_LENGTH` bytes, scoring at least
    ``threshold``. One long string is a region — an intro or an ending is
    exactly that.

    A string runs to an end token — or, under a table set that has none, to
    the byte :func:`_guess_terminator` picks out of the gap, since a table
    fresh from a relative search has nothing else to cut strings on. Codes are
    the text's own and cut nothing; anything else the table cannot read is not
    text at all, so it ends the run, which begins again at the next thing that
    is read. A run ends at the last end token a string reached, so what comes
    back is whole strings.
    """
    if hi <= lo:
        return []
    chunk = data[lo:hi]
    _, tokens = score_window(chunk, tables)
    stopper = None if _has_end(tables) else _breaks(tokens, chunk)[0]
    regions: list[Region] = []
    start: int | None = None  # the run's first byte, once something readable began it
    stop: int | None = None  # the end of the last whole string in it
    seen = False

    def flush() -> None:
        nonlocal start, stop, seen
        if start is not None and stop is not None and stop - start >= MIN_LENGTH:
            score = score_window(data[start:stop], tables)[0]
            if score >= threshold:
                regions.append(Region(start, stop, score))
        start, stop, seen = None, None, False

    for t in tokens:
        at = lo + t.bit_start // 8
        after = lo + -(-t.bit_end // 8)
        kind = t.entry.kind if t.entry is not None else None
        unreadable = kind is None
        terminates = after - at == 1 and chunk[at - lo] == stopper
        if kind is TokenKind.END or (unreadable and terminates):
            if seen:
                stop, seen = after, False
            else:
                flush()  # an end with no characters in front of it ends the run
            continue
        if unreadable:
            flush()
            continue
        if start is None:
            start = at
        seen = seen or kind is TokenKind.TEXT
    flush()
    return regions


def _guess_terminator(data: bytes, tables: TableSet) -> tuple[int | None, int | None]:
    """The unmatched or end byte most often followed by text, and what follows it."""
    return _breaks(score_window(data, tables)[1], data)


def _breaks(tokens: list, data: bytes) -> tuple[int | None, int | None]:
    """The same, of an already decoded ``data``."""
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
