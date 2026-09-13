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
    stride: int = 0
    """The most common distance between consecutive hit addresses."""

    @property
    def explained(self) -> int:
        return len(self.hits)

    @property
    def addresses(self) -> list[int]:
        return sorted(a for addrs in self.hits.values() for a in addrs)

    def regularity(self) -> float:
        return common_stride(self.addresses)[1]

    def refs(self) -> dict[int, tuple[PointerRef, ...]]:
        """Per string start, the pointers this candidate found reaching it.

        What **Attach** puts on the strings: each ref names where the value sits
        and the whole reading that found it, so nothing else has to be carried
        alongside for the pointer to be written back.
        """
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
            for start, addrs in self.hits.items()
        }

    def source(self) -> PointerTableSource:
        addrs = self.addresses
        return PointerTableSource(
            addrs[0],
            addrs[-1] + self.size,
            self.size,
            self.stride or self.size,
            self.endian,
            self.mapping_id,
            self.offset,
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
    """Rank ``(mapping, size, endian, offset)`` combinations by strings explained."""
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
        for start in starts:
            target = start - off
            if target < 0:
                continue
            value = mapping.to_value(target, bank)
            if value < 0 or value >= 1 << (size * 8):
                continue
            needle = pointer_bytes(value, size, endian)
            found = _find_all(data, needle)
            if found:
                cand.hits[start] = found
                cand.values[start] = value
        if not cand.hits:
            continue
        cand.stride = common_stride(cand.addresses)[0]
        results.append(cand)
    results.sort(key=lambda c: (c.explained, c.regularity()), reverse=True)
    return results
