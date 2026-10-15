"""A pointer table whose records carry the string's length beside its pointer."""

from __future__ import annotations

import pytest

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    LengthField,
    NextPointer,
    PointerTableSource,
    WriteMode,
)
from mapchar.pipeline.extract import extract
from mapchar.project.formats.blockspec import format_config, parse_config

TS = table_set(ABC_TABLE, "main")


def records(*pairs: tuple[int, int]) -> bytes:
    """``{u16 offset, u16 length}`` records, little-endian."""
    return b"".join(o.to_bytes(2, "little") + n.to_bytes(2, "little") for o, n in pairs)


ROM = (
    records((0x10, 3), (0x13, 2), (0x15, 4))
    + b"\xff" * 4
    + bytes.fromhex("41 42 00 43 00 41 41 41 00")
    + b"\xff" * 7
)
"""Three records at 0 reaching AB[end] C[end] AAA[end] at $10, each length
counting the end token."""


U16 = LengthField(2, 2)
"""A ``u16`` length just after the pointer, counting the end token."""


def config(length: LengthField | None = U16, **kw) -> BlockConfig:
    source = PointerTableSource(0, 12, 2, 4, "little", "linear", length=length)
    return BlockConfig(source, kw.pop("string_type", EndToken()), "main", **kw)


def lengths(data: bytes, count: int = 3) -> list[tuple[int, int]]:
    """Every record's ``(offset, length)``."""
    return [
        (
            int.from_bytes(data[4 * i : 4 * i + 2], "little"),
            int.from_bytes(data[4 * i + 2 : 4 * i + 4], "little"),
        )
        for i in range(count)
    ]


def test_lengths_that_agree_read_quietly(registry):
    ex = extract(ROM, config(), TS, registry)
    assert texts(ex) == ["AB[end]", "C[end]", "AAA[end]"]
    assert not ex.notices


def test_a_length_that_disagrees_is_a_notice_and_the_end_token_wins(registry):
    data = bytearray(ROM)
    data[6] = 5  # record 1 says 5 bytes for C[end]
    ex = extract(bytes(data), config(), TS, registry)
    assert texts(ex) == ["AB[end]", "C[end]", "AAA[end]"]
    [notice] = ex.notices
    assert notice.offset == 6
    assert "says 5" in notice.message and "holds 2" in notice.message


def test_a_length_that_counts_the_text_leaves_the_end_token_out(registry):
    data = records((0x10, 2), (0x13, 1), (0x15, 3)) + ROM[12:]
    cfg = config(LengthField(2, 2, counts_end=False))
    assert not extract(data, cfg, TS, registry).notices
    assert extract(ROM, cfg, TS, registry).notices


def test_a_string_with_no_end_token_ends_at_its_length(registry):
    # ABC CA AA, packed with no end tokens; lengths 2, 1, 2 leave a byte of
    # each slot unread.
    data = records((0x10, 2), (0x13, 1), (0x15, 2)) + b"\xff" * 4 + b"ABCCAAA"
    cfg = config(string_type=NextPointer())
    ex = extract(data, cfg, TS, registry)
    assert texts(ex) == ["AB", "C", "AA"]
    assert not ex.notices


def test_a_packed_write_sets_every_offset_and_length(registry):
    cfg = config(bound=0x19, fill=b"\xee")
    assert cfg.effective_write_mode is WriteMode.PACKED
    res, out = relayout(ROM, cfg, TS, {0: "A[end]", 2: "AAAA[end]"}, registry)
    assert res.ok, res.problems
    assert out[0x10:0x19] == bytes.fromhex("41 00 43 00 41 41 41 41 00")
    assert lengths(out) == [(0x10, 2), (0x12, 2), (0x14, 5)]
    ex = extract(out, cfg, TS, registry)
    assert texts(ex) == ["A[end]", "C[end]", "AAAA[end]"]
    assert not ex.notices


def test_a_packed_write_puts_a_stale_length_right(registry):
    data = bytearray(ROM)
    data[6] = 9
    cfg = config(bound=0x1C)
    res, out = relayout(bytes(data), cfg, TS, {0: "ABC[end]"}, registry)
    assert res.ok, res.problems
    assert lengths(out) == [(0x10, 4), (0x14, 2), (0x16, 4)]


def test_a_length_that_counts_the_text_is_written_without_the_end_token(registry):
    data = records((0x10, 2), (0x13, 1), (0x15, 3)) + ROM[12:]
    cfg = config(LengthField(2, 2, counts_end=False), bound=0x1C)
    res, out = relayout(data, cfg, TS, {1: "CCC[end]"}, registry)
    assert res.ok, res.problems
    assert lengths(out) == [(0x10, 2), (0x13, 3), (0x17, 3)]


def test_a_slotted_write_sets_the_length_of_the_string_it_shortens(registry):
    cfg = config(write_mode=WriteMode.SLOTTED, fill=b"\xff")
    res, out = relayout(ROM, cfg, TS, {2: "A[end]"}, registry)
    assert res.ok, res.problems
    assert lengths(out) == [(0x10, 3), (0x13, 2), (0x15, 2)]


def test_a_length_its_field_cannot_hold_refuses_the_write(registry):
    source = PointerTableSource(
        0, 4, 2, 3, "little", "linear", length=LengthField(1, 2)
    )
    data = bytes.fromhex("10 00 02") + b"\xff" * 13 + b"A\x00" + b"\xff" * 300
    cfg = BlockConfig(source, EndToken(), "main", bound=0x200)
    res, _ = relayout(data, cfg, TS, {0: "A" * 300 + "[end]"}, registry)
    assert not res.ok
    assert "does not fit the u8 at $2" in res.problems[0].message


def test_the_configuration_line_carries_the_length_field():
    line = (
        "source=pointers start=$0 stop=$C size=2 stride=4 endian=little "
        "mapping=linear offset=0 bank=0 length=u16 type=end table=main"
    )
    cfg = parse_config(line)
    assert cfg.source.length == LengthField(2, 2, True)
    assert format_config(cfg) == line
    cfg = parse_config(line + " length_offset=-2 length_counts=text")
    assert cfg.source.length == LengthField(2, -2, False)
    assert "length=u16 length_offset=-2 length_counts=text" in format_config(cfg)
    assert parse_config(format_config(cfg)) == cfg


@pytest.mark.parametrize(
    "extra",
    [
        "length=u24",
        "length=u16 length_counts=bytes",
        "length=u16 length_offset=1",
        "length=u32 length_offset=2",
        "length=u16 length_offset=-4",
    ],
)
def test_a_length_field_that_cannot_be_is_refused(extra):
    with pytest.raises(ValueError):
        parse_config(
            f"source=pointers start=$0 stop=$C size=2 stride=4 {extra} table=main"
        )
