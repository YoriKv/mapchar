"""Pointer discovery: which bytes of the file point at these strings."""

from __future__ import annotations

import sys
import time
from array import array
from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import repeat
from operator import add, and_, rshift
from typing import Any

from mapchar.core.block import PointerRef, PointerTableSource
from mapchar.core.mapping import (
    holds,
    is_signed,
    pointer_bytes,
    pointer_offset,
    read_pointer,
)


def common_stride(addrs: list[int]) -> tuple[int, float]:
    """The most common distance between consecutive addresses, and its share.

    Fewer than two addresses have no stride at all: ``(0, 0.0)``.
    """
    if len(addrs) < 2:
        return 0, 0.0
    diffs = Counter(b - a for a, b in zip(addrs, addrs[1:], strict=False))
    stride, seen = diffs.most_common(1)[0]
    return stride, seen / (len(addrs) - 1)


RUN_GAP = 4
"""How many strides two hits of one table may sit apart."""

TABLE_RUN = 3
"""How many addresses, reaching how many different strings, a run needs before
it reads as a table of its own rather than as the same value turning up twice
by chance."""

STRAY_HITS = 16
"""How many addresses a string's value may be found at and still say what the
stride is. A value found at more than this is a common byte pair — a stretch of
fill is nothing but — and a hit at every other address of one would vote the
stride down to the distance between two of them.
"""


def _runs(addrs: list[int], stride: int) -> list[tuple[int, int]]:
    """Index ranges over ``addrs``: each run is a stretch whose addresses sit a
    whole number of strides apart and no more than :data:`RUN_GAP` of them — the
    room a null or an unlisted string takes. Without a stride there is one run,
    the whole of them.
    """
    if not stride or not addrs:
        return [(0, len(addrs) - 1)]
    out = []
    first = 0
    for i, (a, b) in enumerate(zip(addrs, addrs[1:], strict=False), start=1):
        gap = b - a
        if gap % stride or gap > stride * RUN_GAP:
            out.append((first, i - 1))
            first = i
    out.append((first, len(addrs) - 1))
    return out


def _runs_touched(
    addrs: list[int], runs: list[tuple[int, int]], starts: list[int], found: list[int]
) -> Iterator[int]:
    """Which of ``runs`` the ascending addresses ``found`` fall in, once each.

    A run at a time rather than an address at a time: a string whose value is a
    common byte pair holds a hit at every address of a stretch of fill, and they
    are all the same answer.
    """
    at = 0
    while at < len(found):
        run = bisect_right(starts, found[at]) - 1
        yield run
        last = runs[run][1]
        at = bisect_right(found, addrs[last], at)


def _within(found: list[int], run: list[int]) -> bool:
    """Whether any of the ascending addresses ``found`` sits inside ``run``."""
    if not run:
        return False
    at = bisect_left(found, run[0])
    return at < len(found) and found[at] <= run[-1]


@dataclass(frozen=True)
class Reading:
    """What one walk of a candidate's addresses works out.

    Kept together because the walk is the whole cost of a candidate on a file of
    fill, and the ranking, the results dialog and :meth:`Candidate.refs` each ask
    for another part of it.
    """

    addresses: list[int]
    run: list[int]
    """The run that reads as the table."""
    reached: frozenset[int]
    """The strings :attr:`run` reaches."""
    tabular: frozenset[int]
    """Every address inside a run that reads as a table, this one or another."""


@dataclass
class Candidate:
    mapping_id: str
    size: int
    endian: str
    offset: int
    hits: dict[int, list[int]] = field(default_factory=dict)
    """String start offset to the addresses holding a pointer to it."""
    values: dict[int, int] = field(default_factory=dict)
    """String start offset to the pointer value that reaches it.

    Kept beside :attr:`hits` because a found address is only half of what a
    pointer is: **Attach** puts the reading back on the string, and the value
    read is what a write-back re-derives and compares against.
    """
    relative: bool = False
    """Read through a ``relative`` mapping: every address read, not one value
    per string searched for, which ranks it after the others on a full tie."""
    address_values: dict[int, int] = field(default_factory=dict)
    """Address to the value it holds, where that is not its string's one value
    in :attr:`values`: a relative pointer counts from where it sits, so each
    pointer to a string holds a value of its own."""
    banks: dict[int, int] = field(default_factory=dict)
    """String start offset to the bank its pointer is read in.

    A pointer too short to carry the bank says only where in the window its
    target sits, so which bank that is comes from the string's own offset —
    and the strings of one block need not all sit in the same bank.
    """
    banked: set[int] = field(default_factory=set)
    """The strings whose pointer only reaches them in the bank it is read in.

    A pointer into a bank that is always mapped — the Game Boy's low window —
    reads the same wherever the table sits, so it says nothing about the bank
    and does not vote on it.
    """
    bank_of: Callable[[int], int] | None = None
    """The mapping's own ``bank_of``, when it has one: which bank an address
    sits in, for deciding the table's by where the table itself is."""
    stride: int = 0
    """The most common distance between consecutive hit addresses."""
    regularity: float = 0.0
    """That stride's share of those distances, which the ranking asks last."""
    _reading: Reading | None = field(default=None, repr=False, compare=False)
    """The last :class:`Reading`, kept because every question about the run asks
    for it again."""
    _read_from: tuple[int, int, int] | None = field(
        default=None, repr=False, compare=False
    )
    """The stride and the hit counts :attr:`_reading` was read from: a candidate
    is filled in field by field, stride last, and a reading from before the
    stride would be wrong."""

    @property
    def explained(self) -> int:
        return len(self.hits)

    @property
    def addresses(self) -> list[int]:
        """Every address a hit sits at, once each: two strings a bank apart
        share a pointer value, and the same address counted twice would make
        the table look twice as long and its stride zero.
        """
        return self.reading().addresses

    def reading(self) -> Reading:
        """The walk of the hit addresses, worked out once per candidate."""
        key = (self.stride, len(self.hits), sum(map(len, self.hits.values())))
        reading = self._reading
        if reading is None or self._read_from != key:
            reading = self._read()
            self._reading, self._read_from = reading, key
        return reading

    def _read(self) -> Reading:
        addrs = sorted({a for addrs in self.hits.values() for a in addrs})
        if not addrs:
            return Reading([], [], frozenset(), frozenset())
        runs = _runs(addrs, self.stride)
        starts = [addrs[first] for first, _ in runs]
        reach: Counter[int] = Counter()
        for found in self.hits.values():
            reach.update(_runs_touched(addrs, runs, starts, found))
        best = 0
        for i, (first, last) in enumerate(runs):
            if (reach[i], last - first) > (
                reach[best],
                runs[best][1] - runs[best][0],
            ):
                best = i
        run = addrs[runs[best][0] : runs[best][1] + 1]
        tabular = set(run)
        for i, (first, last) in enumerate(runs):
            if last - first + 1 >= TABLE_RUN and reach[i] >= TABLE_RUN:
                tabular.update(addrs[first : last + 1])
        reached = frozenset(
            start for start, found in self.hits.items() if _within(found, run)
        )
        return Reading(addrs, run, reached, frozenset(tabular))

    def table_run(self) -> list[int]:
        """The run of hit addresses that reads as the table: each a whole number
        of strides after the last, and no more than :data:`RUN_GAP` of them —
        the room a null or an unlisted string takes.

        A short pointer value turns up elsewhere in a file by chance, and those
        strays are hits like any other; the run is what leaves them out. Of the
        runs, the table is the one reaching the most different strings rather
        than the one holding the most addresses, since one string whose value is
        a common byte pair fills a run of its own out of a stretch of fill.
        Length, and then position, settle a tie.
        """
        return self.reading().run

    def run_explained(self) -> int:
        """How many strings :meth:`table_run` alone reaches.

        What the ranking asks first: a two-byte value turns up all over a file
        by chance, so a count of strings explained counts coincidences as
        readily as pointers, while a run of them is a table or nothing.
        """
        return len(self.reading().reached)

    def bank(self) -> int:
        """The one bank the table is read in: the commonest among the strings
        its run reaches whose pointer needs a bank at all, since a source
        carries a single bank and a table whose strings straddle a bank boundary
        is the rare case. A tie goes to the bank the table itself sits in.
        """
        reading = self.reading()
        reached = sorted(reading.reached) or sorted(self.hits)
        voters = [s for s in reached if s in self.banked] or reached
        counts = Counter(self.banks[s] for s in voters if s in self.banks)
        if not counts:
            return 0
        top = max(counts.values())
        tied = sorted(b for b, n in counts.items() if n == top)
        if len(tied) > 1 and self.bank_of is not None and reading.run:
            own = self.bank_of(reading.run[0])
            if own in tied:
                return own
        return tied[0]

    def refs(self) -> dict[int, tuple[PointerRef, ...]]:
        """Per string start, the pointers this candidate found reaching it.

        What **Attach** puts on the strings: each ref names where the value sits
        and the whole reading that found it, so nothing else has to be carried
        alongside for the pointer to be written back. A packed write rewrites
        every one of them, so a coincidence left in here is a byte pair
        corrupted wherever in the file it happened to sit.

        A string the table run reaches therefore keeps the hits inside any run
        that reads as a table — a file that holds the same table twice has a
        second copy to keep in step — and drops the strays between them, the
        same short value turning up in code. A string the run does not reach
        keeps all of its hits, since pointers scattered rather than tabulated
        are what **Attach** is for. Where two strings a bank apart share an
        address, it goes to the one in the table's own bank.
        """
        reading = self.reading()
        kept = {
            start: [a for a in addrs if a in reading.tabular]
            if start in reading.reached
            else list(addrs)
            for start, addrs in self.hits.items()
        }
        if self.bank_of is not None:
            kept = self._one_bank(kept)
        return {
            start: tuple(
                PointerRef(
                    address,
                    self.size,
                    self.endian,
                    self.mapping_id,
                    self.offset,
                    self.address_values.get(address, self.values[start]),
                )
                for address in addrs
            )
            for start, addrs in kept.items()
            if addrs
        }

    def _one_bank(self, kept: dict[int, list[int]]) -> dict[int, list[int]]:
        """An address claimed by strings in two banks belongs to the string in
        the table's bank, a pointer being read in one bank only."""
        bank = self.bank()
        claims = Counter(a for addrs in kept.values() for a in addrs)
        taken = {
            a
            for start, addrs in kept.items()
            if self.banks.get(start) == bank
            for a in addrs
        }
        return {
            start: [
                a
                for a in addrs
                if claims[a] == 1 or a not in taken or self.banks.get(start) == bank
            ]
            for start, addrs in kept.items()
        }

    def source(self) -> PointerTableSource:
        addrs = self.table_run()
        return PointerTableSource(
            addrs[0],
            addrs[-1] + self.size,
            self.size,
            self.stride or self.size,
            self.endian,
            self.mapping_id,
            self.offset,
            self.bank(),
        )


def _voters(cand: Candidate) -> list[int]:
    """The addresses that say what a candidate's stride is: those of the strings
    whose value is not found all over the file (:data:`STRAY_HITS`), or all of
    them when every string's is.
    """
    votes = sorted(
        {a for found in cand.hits.values() if len(found) <= STRAY_HITS for a in found}
    )
    return votes if len(votes) > 1 else cand.addresses


def _find_all(data: bytes, needle: bytes) -> list[int]:
    out = []
    at = data.find(needle)
    while at >= 0:
        out.append(at)
        at = data.find(needle, at + 1)
    return out


_WORDS: dict[int, str] = {
    width: next(code for code in "BHILQ" if array(code).itemsize == width)
    for width in (1, 2, 4)
}
"""The array type code of an unsigned word of each width a value is read as."""

TICK = 0.1
"""Seconds a step of the search runs between two reports of its progress."""

SLICE = 1 << 20
"""How many addresses the word-array scan reads between two reports."""


class _Stopped(Exception):
    """The search's ``progress`` asked it to stop in the middle of a step."""


class _MappingFault(Exception):
    """A mapping raised: its combination is lost, and nothing else is."""


def _ask(call: Callable[..., Any], *args: Any) -> Any:
    """``call(*args)``, a call into a mapping, with whatever it raises turned
    into :class:`_MappingFault`. Only a mapping's calls go through here, so a
    fault in the search's own code is not taken for a plugin's."""
    try:
        return call(*args)
    except Exception as exc:  # noqa: BLE001 - a mapping is a plugin, and may raise
        raise _MappingFault from exc


def _ask_number(call: Callable[..., Any], *args: Any) -> int:
    """:func:`_ask` for a call that answers a number: a mapping that answers
    anything else has failed as surely as one that raises."""
    value = _ask(call, *args)
    if not isinstance(value, int):
        raise _MappingFault
    return value


class _Ticker:
    """A search's ``progress``, called at the start of each step and, inside
    a long one, no more often than every :data:`TICK` seconds, so a Stop is
    heard while one step runs. Inside a step a stop raises :class:`_Stopped`.
    """

    def __init__(self, progress: Callable[[int, int], bool] | None, total: int):
        self.progress = progress
        self.total = total
        self.step = 0
        self.last = time.monotonic()

    def at(self, step: int) -> bool:
        """Report the start of step ``step``; whether to go on."""
        self.step, self.last = step, time.monotonic()
        return self.progress is None or bool(self.progress(step, self.total))

    def __call__(self) -> None:
        if self.progress is None:
            return
        now = time.monotonic()
        if now - self.last < TICK:
            return
        self.last = now
        if not self.progress(self.step, self.total):
            raise _Stopped


Spans = dict[int, list[tuple[int, int]]]
"""String start to the byte ranges its record is made of: header and text,
two ranges of text for a string read across a skip range."""


def _overlaps(address: int, size: int, start: int, spans: Spans) -> bool:
    """Whether a pointer of ``size`` bytes at ``address`` lies in the bytes of
    the string at ``start`` — at least its first byte, where its bytes are not
    known. No pointer sits inside the string it reaches: a value of zero, or
    a few bytes of the string's own text read as a distance, only look like
    one. A skip range inside the string is not its bytes."""
    return any(address < hi and address + size > lo for lo, hi in _pieces(start, spans))


def _pieces(start: int, spans: Spans) -> list[tuple[int, int]]:
    """The byte ranges of the string at ``start``: its first byte where
    ``spans`` does not say."""
    return spans.get(start) or [(start, start + 1)]


def _reaches(value: int, address: int, size: int, targets: set[int]) -> bool:
    """Whether the ``size``-byte ``value`` at ``address``, read as a distance
    from it, unsigned or in two's complement, lands on one of ``targets``."""
    full = 1 << (size * 8)
    reach = address + value
    return reach in targets or (value >= full >> 1 and reach - full in targets)


def _windows(targets: set[int], size: int, last: int) -> list[tuple[int, int]]:
    """The address ranges, inclusive and merged, a ``size``-byte relative
    pointer to one of ``targets`` can sit in, read either way: up to a whole
    unsigned range before a target, half of it after."""
    full = 1 << (size * 8)
    out: list[tuple[int, int]] = []
    for t in sorted(targets):
        lo, hi = max(0, t - full + 1), min(last, t + (full >> 1))
        if lo > hi:
            continue
        if out and lo <= out[-1][1] + 1:
            out[-1] = (out[-1][0], max(out[-1][1], hi))
        else:
            out.append((lo, hi))
    return out


def _scan_words(
    data: bytes, size: int, endian: str, wide: set[int], lo: int, hi: int, tick
) -> list[int]:
    """The addresses ``lo`` to ``hi`` whose ``size``-byte value, added to the
    address, is in ``wide``: every address read, a word array at a time — one
    per alignment, a three-byte value read as the word it starts and cut
    down — rather than one value at a time in Python."""
    width = 4 if size == 3 else size
    found: list[int] = []
    for first in range(lo, hi + 1, SLICE):
        last = min(hi, first + SLICE - 1)
        piece = data[first : last + width]
        piece += bytes(last + width - first - len(piece))
        for phase in range(min(width, last - first + 1)):
            count = (last - first - phase) // width + 1
            words = array(_WORDS[width], piece[phase : phase + count * width])
            if endian != sys.byteorder:
                words.byteswap()
            values: Iterator[int] | array = words
            if size == 3:
                values = (
                    map(and_, words, repeat(0xFFFFFF))
                    if endian == "little"
                    else map(rshift, words, repeat(8))
                )
            addresses = range(first + phase, first + phase + count * width, width)
            found += [
                a
                for a, t in zip(addresses, map(add, values, addresses), strict=True)
                if t in wide
            ]
        tick()
    return found


COMMON_TOP = 64
"""How many times a top may turn up in a page before the page is read a word
at a time instead: a top of zeroes, which a target close by asks for, turns
up at every address of a stretch of zero fill."""


def _scan_tops(
    data: bytes,
    size: int,
    endian: str,
    targets: set[int],
    wide: set[int],
    lo: int,
    hi: int,
    tick,
) -> list[int]:
    """The addresses ``lo`` to ``hi`` whose ``size``-byte value could reach
    one of ``targets``, found by the value's top two bytes: over a page of
    addresses that share every bit above the bottom ``size - 2`` bytes, a
    target asks for one of two tops, and the bytes are searched for those. A
    page where one turns up more than :data:`COMMON_TOP` times is read as
    :func:`_scan_words` reads, ``wide`` being what it looks for."""
    shift = 8 * (size - 2)
    tops = sorted({t >> shift for t in targets})
    at = 0 if endian == "big" else size - 2
    found: list[int] = []
    for page in range(lo >> shift, (hi >> shift) + 1):
        first = max(lo, page << shift)
        last = min(hi, ((page + 1) << shift) - 1)
        needles = {
            pointer_bytes(top - page - d, 2, endian) for top in tops for d in (0, 1)
        }
        stop = last + at + 2
        if any(data.count(n, first + at, stop) > COMMON_TOP for n in needles):
            found += _scan_words(data, size, endian, wide, first, last, tick)
            continue
        for needle in needles:
            q = data.find(needle, first + at, stop)
            while q >= 0:
                found.append(q - at)
                q = data.find(needle, q + 1, stop)
        tick()
    return found


WORD_COST = 60e-9
"""Seconds the word-array scan spends on an address, roughly."""

FIND_COST = (5e-7, 3e-10)
"""Seconds a search for one top spends per page, and per byte of it."""


def _relative_candidates(
    data: bytes,
    size: int,
    endian: str,
    targets: set[int],
    tick=lambda: None,
    by_tops: bool | None = None,
) -> list[int]:
    """Every address a ``size``-byte ``endian`` relative pointer to one of
    ``targets`` could sit at — its value, read unsigned or in two's
    complement, is the distance to the target — in address order.

    Read once for every relative mapping and every offset tried, the targets
    being all of theirs. A three- or four-byte pointer can reach a target from
    anywhere in most files, so rather than reading every address, the value's
    top two bytes are searched for (:func:`_scan_tops`) while there are few
    enough targets for that to cost less; ``by_tops`` forces either way.
    """
    last = len(data) - size
    windows = _windows(targets, size, last)
    if not windows:
        return []
    if size < 3:
        by_tops = False
    elif by_tops is None:
        span = sum(hi - lo + 1 for lo, hi in windows)
        shift = 8 * (size - 2)
        tops = len({t >> shift for t in targets})
        pages = sum((hi >> shift) - (lo >> shift) + 1 for lo, hi in windows)
        by_tops = (
            pages * 2 * tops * (FIND_COST[0] + FIND_COST[1] * (1 << shift))
            < span * WORD_COST
        )
    full = 1 << (size * 8)
    wide = targets | {t + full for t in targets}
    found: list[int] = []
    for lo, hi in windows:
        if by_tops:
            found += _scan_tops(data, size, endian, targets, wide, lo, hi, tick)
        else:
            found += _scan_words(data, size, endian, wide, lo, hi, tick)
    kept = []
    for a in sorted(set(found)):
        tick()
        value = read_pointer(data, a, size, endian)
        if value is not None and _reaches(value, a, size, targets):
            kept.append(a)
    return kept


def _find_values(
    cand: Candidate,
    data: bytes,
    mapping: Any,
    starts: list[int],
    spans: Spans,
    bank: int,
    tick,
) -> None:
    """Fill ``cand`` with the addresses holding each string's one value: the
    mapping's for the string, searched for as bytes."""
    size, off = cand.size, cand.offset
    limit = 1 << (size * 8)
    bank_of = getattr(mapping, "bank_of", None)
    needs_bank = bool(getattr(mapping, "needs_bank", True))
    signed = is_signed(mapping)
    placed = bank_of is not None or not needs_bank
    for start in starts:
        tick()
        target = start - off
        if target < 0:
            continue
        at_bank = _ask_number(bank_of, target) if bank_of is not None else bank
        value = _ask_number(mapping.to_value, target, at_bank)
        if needs_bank and not signed and value >= limit:
            # A pointer too narrow to carry the bank holds only the address
            # inside the window; the bank is what it leaves out.
            value &= limit - 1
        if not holds(value, size, signed):
            continue
        value &= limit - 1
        if placed:
            if _ask(pointer_offset, mapping, value, size, at_bank) != target:
                continue
            if _ask(pointer_offset, mapping, value, size, at_bank ^ 1) != target:
                cand.banked.add(start)
        found = _find_all(data, pointer_bytes(value, size, cand.endian))
        for lo, hi in _pieces(start, spans):
            # The addresses whose bytes overlap the string's (:func:`_overlaps`),
            # cut out of the ascending list rather than tested one by one.
            del found[bisect_right(found, lo - size) : bisect_left(found, hi)]
        if found:
            cand.hits[start] = found
            cand.values[start] = value
            cand.banks[start] = at_bank


FILL_PERIODS = 8
"""The longest repeat, in bytes, a stretch of fill is recognised by."""

FILL_REPEATS = 4
"""How many times a stretch repeats its pattern before it reads as fill."""

FILL_BYTES = 8
"""The fewest bytes a stretch of fill runs to, however short its pattern."""


def _stretches(data: bytes, address: int, size: int, period: int) -> list[range]:
    """The stretches of ``data`` that repeat every ``period`` bytes —
    ``data[i] == data[i + period]`` throughout — for at least
    :data:`FILL_REPEATS` periods and :data:`FILL_BYTES` bytes, and hold all
    ``size`` bytes at ``address``. Read only as far either side as it takes
    to know, never across the whole of a fill."""
    need = max(FILL_BYTES, FILL_REPEATS * period)
    first = max(0, address - need)
    last = min(len(data) - period, address + size + need)
    out: list[range] = []
    run = None
    for i in range(first, last + 1):
        if i < last and data[i] == data[i + period]:
            run = i if run is None else run
            continue
        if run is not None:
            # Matches over [run, i) make [run, i + period) repeat; clipped to
            # the bytes read, a stretch long enough there is long enough.
            lo, hi = run, i + period
            if hi - lo >= need and lo <= address and address + size <= hi:
                out.append(range(lo, hi))
            run = None
    return out


def _is_fill(data: bytes, address: int, size: int, reaches) -> bool:
    """Whether the ``size`` bytes at ``address`` lie in a stretch of fill
    rather than being a relative pointer: for some period up to
    :data:`FILL_PERIODS`, a stretch repeating it (:func:`_stretches`) holds
    them, and the same word one period away inside it reaches none of the
    strings (``reaches(address)`` false). Read as a distance, fill reaches
    whatever lies that far past every address of it, so a run of strings
    after a stretch of it looks like a table at the strings' own spacing.

    What it misses: fill shorter than :data:`FILL_BYTES` bytes or
    :data:`FILL_REPEATS` repeats, or of a longer pattern, and fill whose
    every word has a string one period either side of the one it reaches. What
    it costs a real table, in a run of three or more neighbouring pointers of
    one value: the pointer beside one whose string is not searched, the
    pointer beside a word outside the table holding the same value, and the
    whole run when the value's bytes repeat at a shorter period (``$4040``)."""
    word = data[address : address + size]
    for period in range(1, FILL_PERIODS + 1):
        near = [
            at
            for at in (address - period, address + period)
            if 0 <= at and data[at : at + size] == word
        ]
        if not near:
            # A stretch long enough holds the word one period away on one
            # side or the other.
            continue
        for stretch in _stretches(data, address, size, period):
            if any(
                at in stretch and at + size <= stretch.stop and not reaches(at)
                for at in near
            ):
                return True
    return False


def _find_relative(
    cand: Candidate,
    data: bytes,
    mapping: Any,
    starts: list[int],
    spans: Spans,
    bank: int,
    addresses: list[int],
    tick,
) -> None:
    """Fill ``cand`` for a ``relative`` mapping from the ``addresses`` a
    relative pointer to one of the strings could sit at
    (:func:`_relative_candidates`): each one's own value, checked back
    through the mapping, since no one value reaches a string. Neither a
    pointer inside its own string (:func:`_overlaps`) nor a word of fill
    (:func:`_is_fill`) counts."""
    size = cand.size
    targets = {start - cand.offset: start for start in starts if start >= cand.offset}

    def string_at(address: int) -> int | None:
        value = read_pointer(data, address, size, cand.endian)
        if value is None:
            return None
        reached = _ask(pointer_offset, mapping, value, size, bank, address)
        return targets.get(reached) if reached is not None else None

    for address in addresses:
        tick()
        start = string_at(address)
        if start is None or _overlaps(address, size, start, spans):
            continue
        if _is_fill(data, address, size, lambda at: string_at(at) is not None):
            continue
        value = read_pointer(data, address, size, cand.endian) or 0
        cand.hits.setdefault(start, []).append(address)
        cand.values.setdefault(start, value)
        cand.address_values[address] = value
        cand.banks[start] = bank


def _applies(mapping, data: bytes) -> bool:
    """Whether a mapping can hold for ``data`` at all, by its optional
    ``applies``: one it rules out would only tie with a plainer mapping."""
    applies = getattr(mapping, "applies", None)
    if applies is None:
        return True
    try:
        return bool(applies(data))
    except Exception:  # noqa: BLE001 - a mapping is a plugin, and may raise
        return True


def discover(
    data: bytes,
    starts: list[int],
    mappings: dict[str, object],
    *,
    sizes: tuple[int, ...] = (2, 3, 4),
    endians: tuple[str, ...] = ("little", "big"),
    offsets: tuple[int, ...] = (0,),
    bank: int = 0,
    progress: Callable[[int, int], bool] | None = None,
    spans: Spans | None = None,
) -> list[Candidate]:
    """Rank ``(mapping, size, endian, offset)`` combinations by the strings the
    table they make explains, then by how many they explain at all and how
    regular their addresses are.

    A mapping that needs a bank takes each string's own from its offset
    (``bank_of``) rather than from ``bank``, which is the fallback for one that
    does not say: the strings of a block can sit in different banks, and a
    banked table is not found at all when the bank is guessed wrong. A mapping
    that needs a bank and has no ``bank_of`` is left with that fallback, which
    is a guess, so its values are taken as found rather than checked back
    against it. A mapping that raises loses its own combination and no more.
    A mapping whose ``applies`` says it cannot hold for ``data`` is not tried.

    A ``relative`` mapping has no one value per string to search for, since a
    pointer's value is the distance from where it sits: the addresses whose
    value lands on a string are read out of the file instead, once per size
    and byte order for every relative mapping and offset
    (:func:`_relative_candidates`). A ``signed`` value is read and searched
    for as two's complement at the size tried.

    ``spans`` gives the bytes each string's record is made of, by its start.
    A pointer found inside the string it reaches is none (:func:`_overlaps`),
    whichever the mapping; one inside another of the strings is kept, as a
    value found anywhere else is, since a block read over its own table holds
    the table in its strings. A relative pointer in a stretch of one repeated
    word is fill (:func:`_is_fill`). On a full tie a relative candidate ranks
    after the others: one that reads every address finds coincidences a
    search for one value does not. ``progress`` is also called inside a long
    step, so a stop is heard within :data:`TICK` seconds or so.
    """
    combos = [
        (mid, m, size, endian, off)
        for mid, m in mappings.items()
        if _applies(m, data)
        for size in sizes
        if size in getattr(m, "sizes", sizes)
        for endian in endians
        for off in offsets
    ]
    results: list[Candidate] = []
    spans = spans or {}
    ticker = _Ticker(progress, len(combos))
    reach = {start - off for start in starts for off in offsets if start >= off}
    scans: dict[tuple[int, str], list[int]] = {}
    for i, (mid, mapping, size, endian, off) in enumerate(combos):
        if not ticker.at(i):
            break
        relative = bool(getattr(mapping, "relative", False))
        cand = Candidate(
            mid,
            size,
            endian,
            off,
            bank_of=getattr(mapping, "bank_of", None),
            relative=relative,
        )
        if relative and (size, endian) not in scans:
            try:
                scans[size, endian] = _relative_candidates(
                    data, size, endian, reach, ticker
                )
            except _Stopped:
                break
        try:
            if relative:
                found = scans[size, endian]
                _find_relative(cand, data, mapping, starts, spans, bank, found, ticker)
            else:
                _find_values(cand, data, mapping, starts, spans, bank, ticker)
        except _Stopped:
            break
        except _MappingFault:
            continue
        if not cand.hits:
            continue
        cand.stride, cand.regularity = common_stride(_voters(cand))
        results.append(cand)
    results.sort(
        key=lambda c: (c.run_explained(), c.explained, c.regularity, not c.relative),
        reverse=True,
    )
    return results
