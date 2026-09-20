"""Reading a pointer source: which addresses hold a pointer, and where each one
reaches.

Two readers share the walk. Extraction (:func:`read_pointers`, in front of
:mod:`mapchar.pipeline.extract`, which cuts the strings a pointer reaches into
text) reads every pointer in the source's order, drops a null one and records a
notice for one it cannot follow. The Hex and Text tabs (:func:`pointer_cells`)
read the pointers that start in the bytes in view, keep every one the data
holds whole, and flag the null and the unmapped rather than leaving them out.

What neither decides is stated once: the addresses a source steps to
(:func:`outer_addresses`, :func:`inner_addresses`), and what one pointer holds
and reaches (:class:`PointerSlot`, from :func:`pointer_slots`,
:func:`record_slots` and :func:`inner_slots`).
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

from mapchar.core.bits import align_up
from mapchar.core.block import (
    NestedPointerSource,
    PointerListSource,
    PointerRef,
    PointerSource,
    PointerTableSource,
)
from mapchar.core.mapping import read_pointer
from mapchar.core.notices import Notice
from mapchar.plugins.registry import mapping_for


@dataclass(frozen=True)
class NestedRecord:
    """One record of a nested source's outer table, both pointers mapped."""

    address: int
    """Where the record's first outer pointer sits."""
    table: int
    """Where its inner table starts."""
    base: int
    """What its inner pointers count from; where the inner table stops."""


def outer_addresses(source: PointerTableSource | NestedPointerSource) -> range:
    """Every address the source's own table steps to: a pointer table's
    pointers, and a nested source's records. A stride of none is one step."""
    return range(source.start, source.stop, max(source.stride, 1))


def inner_addresses(record: NestedRecord, source: NestedPointerSource) -> range:
    """Every address a record's inner table holds a pointer at: from the table
    up to the base its pointers count from, which is where the table stops."""
    width = source.inner_size
    return range(record.table, record.base - width + 1, width)


def _within(addresses: range, lo: int | None, hi: int | None) -> range:
    """The addresses of a run that start in bytes ``lo`` to ``hi``, or all of
    them when no window is given."""
    if lo is None or hi is None:
        return addresses
    first = align_up(lo, addresses.step, addresses.start)
    return range(first, min(addresses.stop, hi), addresses.step)


def pointer_target(mapping, source: PointerSource, value, address, size) -> int | None:
    """Where a pointer at ``address`` holding ``value`` points in data of
    ``size`` bytes: mapped, moved by the source's offset, and ``None`` when it
    lands outside."""
    target = mapping.to_offset(value, source.bank, address)
    if target is None:
        return None
    target += source.offset
    return target if 0 <= target < size else None


@dataclass(frozen=True)
class PointerSlot:
    """One pointer as the data holds it, before either reader's policy."""

    address: int
    size: int
    value: int | None
    """``None`` when the end of the data cuts the pointer short."""
    null: bool
    """It holds its source's null value: it reaches nothing."""
    target: int | None
    """The byte it reaches. ``None`` when it is cut short or null, is read
    through a mapping the build has not got, or lands outside the data."""


def _slot(
    data: bytes,
    address: int,
    size: int,
    endian: str,
    null: int | None,
    reach: Callable[[int], int | None],
) -> PointerSlot:
    value = read_pointer(data, address, size, endian)
    if value is None:
        return PointerSlot(address, size, None, False, None)
    if value == null:
        return PointerSlot(address, size, value, True, None)
    return PointerSlot(address, size, value, False, reach(value))


def _outer_slot(data: bytes, source: PointerSource, mapping, address: int):
    """A slot read by the source's own size, endianness, null and mapping."""

    def reach(value: int) -> int | None:
        if mapping is None:
            return None
        return pointer_target(mapping, source, value, address, len(data))

    return _slot(data, address, source.size, source.endian, source.null, reach)


def pointer_slots(
    data: bytes,
    source: PointerTableSource | PointerListSource,
    mapping,
    lo: int | None = None,
    hi: int | None = None,
) -> Iterator[PointerSlot]:
    """A pointer table's or list's pointers in the source's own order: all of
    them, or those that start in bytes ``lo`` to ``hi``."""
    if isinstance(source, PointerTableSource):
        addresses = _within(outer_addresses(source), lo, hi)
    elif lo is None or hi is None:
        addresses = source.addresses
    else:
        addresses = [a for a in source.addresses if lo <= a < hi]
    for address in addresses:
        yield _outer_slot(data, source, mapping, address)


def record_slots(
    data: bytes, source: NestedPointerSource, mapping, address: int
) -> tuple[PointerSlot, PointerSlot]:
    """The two outer pointers of the record at ``address``: the one at its
    inner table, then the one at the base that table's pointers count from."""
    return (
        _outer_slot(data, source, mapping, address),
        _outer_slot(data, source, mapping, address + source.size),
    )


def inner_slots(
    data: bytes,
    record: NestedRecord,
    source: NestedPointerSource,
    lo: int | None = None,
    hi: int | None = None,
) -> Iterator[PointerSlot]:
    """A record's inner pointers, each counted from the record's base: all of
    them, or those that start in bytes ``lo`` to ``hi``."""

    def reach(value: int) -> int | None:
        target = record.base + value
        return target if target < len(data) else None

    for address in _within(inner_addresses(record, source), lo, hi):
        yield _slot(
            data,
            address,
            source.inner_size,
            source.inner_endian,
            source.inner_null,
            reach,
        )


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
    for address in outer_addresses(source):
        pair = record_slots(data, source, mapping, address)
        if any(slot.value is None for slot in pair):
            notices.append(Notice("pointer past the end of the data", offset=address))
            continue
        if any(slot.null for slot in pair):
            continue
        table, base = (slot.target for slot in pair)
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
        return [(a, source.size) for a in outer_addresses(source)]
    if isinstance(source, PointerListSource):
        return [(a, source.size) for a in source.addresses]
    records, _ = nested_records(data, source, registry)
    out = [
        (a + at, source.size)
        for a in outer_addresses(source)
        for at in (0, source.size)
    ]
    for rec in records:
        out += [(a, source.inner_size) for a in inner_addresses(rec, source)]
    return out


def _outside(slot: PointerSlot) -> Notice:
    return Notice(f"pointer ${slot.value:X} maps outside the data", offset=slot.address)


def read_nested(
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
    for rec in records:
        for slot in inner_slots(data, rec, source):
            if slot.value is None or slot.null:
                continue
            if slot.target is None:
                notices.append(_outside(slot))
            refs.append(
                PointerRef(
                    slot.address,
                    slot.size,
                    source.inner_endian,
                    "linear",
                    rec.base,
                    slot.value,
                )
            )
            targets.append(slot.target)
    return refs, targets, notices


def read_pointers(
    data: bytes, source: PointerSource, registry, bases=None
) -> tuple[list[PointerRef], list[int | None], list[Notice]]:
    """Every pointer of the source with its target offset (None when unmapped).

    A pointer holding the source's null value reaches no string and is left
    out."""
    if isinstance(source, NestedPointerSource):
        return read_nested(data, source, registry, bases)
    mapping = mapping_for(source, registry)
    notices: list[Notice] = []
    if mapping is None:
        notices.append(Notice(f"unknown mapping {source.mapping_id!r}"))
        return [], [], notices
    refs: list[PointerRef] = []
    targets: list[int | None] = []
    for slot in pointer_slots(data, source, mapping):
        if slot.value is None:
            notices.append(
                Notice("pointer past the end of the data", offset=slot.address)
            )
            continue
        if slot.null:
            continue
        if slot.target is None:
            notices.append(_outside(slot))
        refs.append(
            PointerRef(
                slot.address,
                slot.size,
                source.endian,
                source.mapping_id,
                source.offset,
                slot.value,
            )
        )
        targets.append(slot.target)
    return refs, targets, notices


@dataclass(frozen=True)
class PointerCell:
    """One pointer in view."""

    address: int
    size: int
    value: int
    target: int | None
    """The byte it reaches, or ``None`` when it maps outside the data."""
    null: bool = False
    """It holds the source's null value: it reaches nothing."""
    role: str | None = None
    """What a nested source's outer pointer is, since it reaches structure
    rather than text: ``"table"`` for the one at its record's inner pointer
    table, ``"base"`` for the one at the base those pointers count from.
    ``None`` for every pointer that reaches a string."""


def _cells(slots, role: str | None = None) -> list[PointerCell]:
    """The slots the data holds whole, as cells. A view shows a pointer
    whatever it holds, so one that is null or read through a mapping the build
    has not got reaches nothing rather than being left out."""
    return [
        PointerCell(s.address, s.size, s.value, s.target, s.null, role)
        for s in slots
        if s.value is not None
    ]


def pointer_cells(
    data: bytes, source: PointerSource, lo: int, hi: int, registry=None
) -> list[PointerCell]:
    """The pointers of ``source`` that start in bytes ``lo`` to ``hi``, in
    address order.

    A pointer table's are every ``stride`` bytes from its start, and a list's
    are its addresses; one cut short by the end of the data is left out. A
    nested source's are its outer table's pointers and its inner tables'.
    """
    mapping = mapping_for(source, registry)
    if not isinstance(source, NestedPointerSource):
        cells = _cells(pointer_slots(data, source, mapping, lo, hi))
        return sorted(cells, key=lambda c: c.address)
    cells = []
    # A record two pointers wide can start before the window and end in it.
    for record in _within(outer_addresses(source), lo - 2 * source.size, hi):
        pair = record_slots(data, source, mapping, record)
        for slot, role in zip(pair, ("table", "base"), strict=True):
            if lo <= slot.address < hi:
                cells += _cells([slot], role)
    records, _ = nested_records(data, source, registry)
    for rec in records:
        cells += _cells(inner_slots(data, rec, source, lo, hi))
    return sorted(cells, key=lambda c: c.address)


def pointer_window(
    source: PointerSource, lo: int, count: int, data: bytes = b"", registry=None
) -> int | None:
    """The byte after the ``count``-th pointer of ``source`` at or past ``lo``:
    how far a view from ``lo`` reads to show that many. ``None`` when the source
    has fewer. A nested source's inner tables are read from ``data``."""
    if isinstance(source, PointerTableSource):
        addresses = outer_addresses(source)
        found = _within(addresses, lo, addresses.stop)[count - 1 : count]
        return found[0] + source.size if found else None
    found = sorted(
        (a, size) for a, size in pointer_addresses(data, source, registry) if a >= lo
    )
    if len(found) < count:
        return None
    address, size = found[count - 1]
    return address + size
