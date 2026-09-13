"""Pointer discovery: which bytes of the file point at these strings."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field

from mapchar.core.block import PointerTableSource
from mapchar.core.mapping import pointer_bytes


@dataclass
class Candidate:
    mapping_id: str
    size: int
    endian: str
    offset: int
    hits: dict[int, list[int]] = field(default_factory=dict)
    """String start offset to the addresses holding a pointer to it."""
    stride: int = 0
    """The most common distance between consecutive hit addresses."""

    @property
    def explained(self) -> int:
        return len(self.hits)

    @property
    def addresses(self) -> list[int]:
        return sorted(a for addrs in self.hits.values() for a in addrs)

    def regularity(self) -> float:
        addrs = self.addresses
        if len(addrs) < 2:
            return 0.0
        diffs = Counter(b - a for a, b in zip(addrs, addrs[1:], strict=False))
        return diffs.most_common(1)[0][1] / (len(addrs) - 1)

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


def _find_all(data: bytes, needle: bytes, align: int = 1) -> list[int]:
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
        if not cand.hits:
            continue
        addrs = cand.addresses
        if len(addrs) >= 2:
            diffs = Counter(b - a for a, b in zip(addrs, addrs[1:], strict=False))
            cand.stride = diffs.most_common(1)[0][0]
        results.append(cand)
    results.sort(key=lambda c: (c.explained, c.regularity()), reverse=True)
    return results
