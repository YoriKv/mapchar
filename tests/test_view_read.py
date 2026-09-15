"""Reading the bytes in view: string rules from the view's first byte, pointers,
the standard encodings as tables, and a new block's region."""

from __future__ import annotations

from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    FixedSource,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    with_region,
)
from mapchar.core.table import TableSet, TokenKind
from mapchar.core.tokens import render
from mapchar.pipeline.view_read import (
    decode_strings,
    pointer_cells,
    target_string,
)
from mapchar.plugins.charsets import CharsetTables
from mapchar.plugins.registry import default_registry


def _ascii() -> TableSet:
    tables = CharsetTables(default_registry())
    return TableSet.build(tables["ascii"], tables)


def test_the_encodings_are_tables_built_when_first_read():
    tables = CharsetTables(default_registry())
    ids = [cid for cid, _ in tables.names()]
    assert ids[:2] == ["ascii", "latin-1"]
    assert {"shift-jis", "euc-jp", "jis-x-0201", "utf-8", "utf-16le", "big5"} <= set(
        ids
    )
    assert "none" not in tables and "none" not in ids
    assert not tables._built  # listing them reads none
    ascii_table = tables["ascii"]
    assert ascii_table.entries["01000001"].text == "A"
    # A string of an encoding ends at its NUL, as wide as its code unit.
    assert ascii_table.entries["00000000"].kind is TokenKind.END
    assert tables["utf-16le"].entries["0" * 16].kind is TokenKind.END
    assert tables["ascii"] is ascii_table


def test_half_width_katakana_are_single_bytes_in_jis_x_0201():
    table = CharsetTables(default_registry())["jis-x-0201"]
    assert table.entries[format(0xB1, "08b")].text == "ｱ"
    assert max(len(bits) for bits in table.entries) == 8


def test_a_view_cuts_strings_by_the_reading_s_string_type():
    data = b"ABCDEF"
    ends = decode_strings(data, None, _ascii())
    assert ends.starts == [0]
    fixed = BlockConfig(RangeSource(0, 0), FixedLength(2), "ascii")
    run = decode_strings(data, fixed, _ascii())
    assert run.starts == [0, 16, 32]
    assert render(run.tokens) == "ABCDEF"
    # Fixed strings cut by their own length, whatever the string type says.
    source = BlockConfig(FixedSource(0, 1, 3), EndToken(), "ascii")
    assert decode_strings(data, source, _ascii()).starts == [0, 24]


def test_pointer_cells_are_every_stride_from_the_table_s_start():
    data = bytes.fromhex("08 00 FF 0A 00 FF 40 00") + b"HI\x00OK\x00"
    source = PointerTableSource(0, 6, 2, 3)
    cells = pointer_cells(data, source, 1, 6)
    assert [(c.address, c.value, c.target) for c in cells] == [(3, 0x0A, 0x0A)]
    whole = pointer_cells(data, PointerTableSource(0, 9, 2, 3), 0, 9)
    # $40 points past the data, so it reaches nothing.
    assert [c.target for c in whole] == [8, 0x0A, None]
    listed = pointer_cells(data, PointerListSource((3, 0), 2), 0, 9)
    assert [c.address for c in listed] == [0, 3]


def test_a_pointer_s_string_reads_by_the_string_rules():
    data = b"HI\x00OK\x00"
    cfg = BlockConfig(PointerTableSource(0, 0, 2, 2), EndToken(), "ascii")
    assert render(target_string(data, cfg, _ascii(), 3)) == "OK[end]"


def test_a_new_block_keeps_its_reading_s_kind_over_the_region():
    base = BlockConfig(RangeSource(0, 0), EndToken(), "t", skips=((1, 2),), bound=9)
    assert with_region(base, 4, 8).source == RangeSource(4, 8)
    assert with_region(base, 4, 8).skips == () and with_region(base, 4, 8).bound is None
    fixed = BlockConfig(FixedSource(0, 1, 3), EndToken(), "t")
    assert with_region(fixed, 4, 11).source == FixedSource(4, 2, 3)
    table = BlockConfig(PointerTableSource(0, 0, 3, 4, "big"), EndToken(), "t")
    assert with_region(table, 4, 8).source == PointerTableSource(4, 8, 3, 4, "big")
    listed = BlockConfig(PointerListSource((1, 2), 2), EndToken(), "t")
    assert with_region(listed, 4, 8).source == PointerTableSource(4, 8, 2, 2)
