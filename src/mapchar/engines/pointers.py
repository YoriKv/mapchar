"""Pointer discovery: which bytes of the file point at these strings."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
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
    stride: int = 0
    """The most common distance between consecutive hit addresses."""

    @property
    def explained(self) -> int:
        return len(self.hits)

    @property
    def addresses(self) -> list[int]:
        return sorted(a for addrs in self.hits.values() for a in addrs)

    def table_run(self) -> list[int]:
        """The longest run of hit addresses that reads as one table: each a
        whole number of strides after the last, and no more than
        :data:`RUN_GAP` of them — the room a null or an unlisted string takes.

        A short pointer value turns up elsewhere in a file by chance, and those
        strays are hits like any other; the run is what leaves them out.
        """
        addrs = self.addresses
        if not addrs or not self.stride:
            return addrs
        best = run = [addrs[0]]
        for address in addrs[1:]:
            gap = address - run[-1]
            if gap % self.stride == 0 and gap <= self.stride * RUN_GAP:
                run = [*run, address]
            else:
                run = [address]
            if len(run) > len(best):
                best = run
        return best

    def run_explained(self) -> int:
        """How many strings :meth:`table_run` alone reaches.

        What the ranking asks first: a two-byte value turns up all over a file
        by chance, so a count of strings explained counts coincidences as
        readily as pointers, while a run of them is a table or nothing.
        """
        run = set(self.table_run())
        return sum(1 for addrs in self.hits.values() if run.intersection(addrs))

    def regularity(self) -> float:
        return common_stride(self.addresses)[1]

    def bank(self) -> int:
        """The one bank the table is read in: the commonest among the strings
        its run reaches, since a source carries a single bank and a table whose
        strings straddle a bank boundary is the rare case.
        """
        run = set(self.table_run())
        banks = [
            b for start, b in self.banks.items() if run.intersection(self.hits[start])
        ] or list(self.banks.values())
        return Counter(banks).most_common(1)[0][0] if banks else 0

    def refs(self) -> dict[int, tuple[PointerRef, ...]]:
        """Per string start, the pointers this candidate found reaching it.

        What **Attach** puts on the strings: each ref names where the value sits
        and the whole reading that found it, so nothing else has to be carried
        alongside for the pointer to be written back. A packed write rewrites
        every one of them, so a coincidence left in here is a byte pair
        corrupted wherever in the file it happened to sit.

        A string the table run reaches therefore keeps only the hits inside the
        run: the rest are the same short value turning up in code. A string the
        run does not reach keeps all of its hits, since pointers scattered
        rather than tabulated are what **Attach** is for.
        """
        run = set(self.table_run())
        out: dict[int, tuple[PointerRef, ...]] = {}
        for start, addrs in self.hits.items():
            out[start] = tuple(
                PointerRef(
                    address,
                    self.size,
                    self.endian,
                    self.mapping_id,
                    self.offset,
                    self.values[start],
                )
                for address in ([a for a in addrs if a in run] or addrs)
            )
        return out

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
    banked table is not found at all when the bank is guessed wrong.
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
        cand = Candidate(mid, size, endian, off)
        limit = 1 << (size * 8)
        bank_of = getattr(mapping, "bank_of", None)
        needs_bank = getattr(mapping, "needs_bank", False)
        for start in starts:
            target = start - off
            if target < 0:
                continue
            at_bank = bank_of(target) if bank_of is not None else bank
            value = mapping.to_value(target, at_bank)
            if needs_bank and value >= limit:
                # A pointer too narrow to carry the bank holds only the address
                # inside the window; the bank is what it leaves out.
                value &= limit - 1
            if value < 0 or value >= limit:
                continue
            if mapping.to_offset(value, at_bank) != target:
                continue
            needle = pointer_bytes(value, size, endian)
            found = _find_all(data, needle)
            if found:
                cand.hits[start] = found
                cand.values[start] = value
                cand.banks[start] = at_bank
        if not cand.hits:
            continue
        cand.stride = common_stride(cand.addresses)[0]
        results.append(cand)
    results.sort(
        key=lambda c: (c.run_explained(), c.explained, c.regularity()), reverse=True
    )
    return results
