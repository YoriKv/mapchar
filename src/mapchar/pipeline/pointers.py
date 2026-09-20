"""Reading a pointer source: which addresses hold a pointer, and where each one
reaches.

Beside :mod:`mapchar.pipeline.extract`, which cuts the strings a pointer
reaches into text: this is the table walk in front of it — a pointer table's or
list's own addresses, a nested source's records and the inner tables they name,
and the mapping from a raw value to a byte of the data. The Hex tab reads the
same pointers a different way (:func:`~mapchar.pipeline.view_read.pointer_cells`),
so the walk is stated here once and each caller keeps its own policy on top of
it: extraction drops a null pointer and records a notice for an unmapped one,
the view keeps both and flags them.
"""

from __future__ import annotations

from dataclasses import dataclass

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


def pointer_target(mapping, source: PointerSource, value, address, size) -> int | None:
    """Where a pointer at ``address`` holding ``value`` points in data of
    ``size`` bytes: mapped, moved by the source's offset, and ``None`` when it
    lands outside."""
    target = mapping.to_offset(value, source.bank, address)
    if target is None:
        return None
    target += source.offset
    return target if 0 <= target < size else None


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
    size = source.size
    for address in outer_addresses(source):
        values = [
            read_pointer(data, address + at, size, source.endian) for at in (0, size)
        ]
        if None in values:
            notices.append(Notice("pointer past the end of the data", offset=address))
            continue
        if source.null is not None and source.null in values:
            continue
        table, base = (
            pointer_target(mapping, source, v, address + at, len(data))
            for v, at in zip(values, (0, size), strict=True)
        )
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
    width = source.inner_size
    for rec in records:
        for address in inner_addresses(rec, source):
            value = read_pointer(data, address, width, source.inner_endian)
            if value is None:
                continue
            if value == source.inner_null:
                continue
            target = rec.base + value
            if target >= len(data):
                notices.append(
                    Notice(f"pointer ${value:X} maps outside the data", offset=address)
                )
                target = None
            refs.append(
                PointerRef(
                    address, width, source.inner_endian, "linear", rec.base, value
                )
            )
            targets.append(target)
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
    if isinstance(source, PointerTableSource):
        addresses = list(outer_addresses(source))
    else:
        addresses = list(source.addresses)
    refs: list[PointerRef] = []
    targets: list[int | None] = []
    for address in addresses:
        value = read_pointer(data, address, source.size, source.endian)
        if value is None:
            notices.append(Notice("pointer past the end of the data", offset=address))
            continue
        if value == source.null:
            continue
        target = pointer_target(mapping, source, value, address, len(data))
        if target is None:
            notices.append(
                Notice(f"pointer ${value:X} maps outside the data", offset=address)
            )
        refs.append(
            PointerRef(
                address,
                source.size,
                source.endian,
                source.mapping_id,
                source.offset,
                value,
            )
        )
        targets.append(target)
    return refs, targets, notices
