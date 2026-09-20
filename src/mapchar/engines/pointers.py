"""Pointer discovery: which bytes of the file point at these strings."""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import Counter
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from mapchar.core.block import PointerRef, PointerTableSource
from mapchar.core.mapping import pointer_bytes


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
                    self.values[start],
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
    """
    combos = [
        (mid, m, size, endian, off)
        for mid, m in mappings.items()
        for size in sizes
        if size in getattr(m, "sizes", sizes)
        for endian in endians
        for off in offsets
    ]
    results: list[Candidate] = []
    for i, (mid, mapping, size, endian, off) in enumerate(combos):
        if progress is not None and not progress(i, len(combos)):
            break
        limit = 1 << (size * 8)
        bank_of = getattr(mapping, "bank_of", None)
        needs_bank = bool(getattr(mapping, "needs_bank", True))
        placed = bank_of is not None or not needs_bank
        cand = Candidate(mid, size, endian, off, bank_of=bank_of)
        try:
            for start in starts:
                target = start - off
                if target < 0:
                    continue
                at_bank = bank_of(target) if bank_of is not None else bank
                value = mapping.to_value(target, at_bank)
                if needs_bank and value >= limit:
                    # A pointer too narrow to carry the bank holds only the
                    # address inside the window; the bank is what it leaves out.
                    value &= limit - 1
                if value < 0 or value >= limit:
                    continue
                if placed:
                    if mapping.to_offset(value, at_bank) != target:
                        continue
                    if mapping.to_offset(value, at_bank ^ 1) != target:
                        cand.banked.add(start)
                needle = pointer_bytes(value, size, endian)
                found = _find_all(data, needle)
                if found:
                    cand.hits[start] = found
                    cand.values[start] = value
                    cand.banks[start] = at_bank
        except Exception:  # noqa: BLE001 - a mapping is a plugin, and may raise
            continue
        if not cand.hits:
            continue
        cand.stride, cand.regularity = common_stride(_voters(cand))
        results.append(cand)
    results.sort(
        key=lambda c: (c.run_explained(), c.explained, c.regularity), reverse=True
    )
    return results
