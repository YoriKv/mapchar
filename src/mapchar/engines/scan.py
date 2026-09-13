"""Text-likeness scan: where in a file a table decodes something readable."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from mapchar.core.bits import Bits
from mapchar.core.table import EntryKind, TableSet
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


@dataclass
class Region:
    start: int
    end: int
    score: float
    terminator: int | None = None
    """The byte most often ending a text run in the region."""
    initial: int | None = None
    """The byte most often beginning a run after the terminator."""

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
        elif t.entry.kind is EntryKind.TEXT:
            text_bits += t.bit_end - t.bit_start
            chars.append(plain_text(t.entry.text))
        else:
            chars.append(" ")
    fraction = text_bits / bits.length
    words = "".join(chars).lower().split()
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
    """Regions whose windows score at least ``threshold``, merged and ranked."""
    regions: list[Region] = []
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
    current: Region | None = None
    for at, score in scores:
        if score >= threshold:
            end = min(at + window, len(data))
            if current is not None and at <= current.end:
                current.end = end
                current.score = max(current.score, score)
            else:
                current = Region(at, end, score)
                regions.append(current)
        else:
            current = None
    for region in regions:
        region.terminator, region.initial = _guess_terminator(
            data[region.start : region.end], tables
        )
    regions.sort(key=lambda r: (r.score, r.length), reverse=True)
    return regions


def _guess_terminator(data: bytes, tables: TableSet) -> tuple[int | None, int | None]:
    """The unmatched or end byte most often followed by text, and what follows it."""
    _, tokens = score_window(data, tables)
    enders: Counter[int] = Counter()
    initials: Counter[int] = Counter()
    for a, b in zip(tokens, tokens[1:], strict=False):
        a_is_break = a.entry is None or a.entry.kind is EntryKind.END
        b_is_text = b.entry is not None and b.entry.kind is EntryKind.TEXT
        if a_is_break and b_is_text and a.bit_end - a.bit_start == 8:
            enders[int(a.bits, 2)] += 1
            initials[data[b.bit_start // 8]] += 1
    terminator = enders.most_common(1)[0][0] if enders else None
    initial = initials.most_common(1)[0][0] if initials else None
    return terminator, initial
