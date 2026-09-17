from __future__ import annotations

from helpers import ABC_TABLE, pointer_rom, table_set, tables_from, translated
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    NestedPointerSource,
    PointerTableSource,
    RangeSource,
)
from mapchar.pipeline.extract import extract
from mapchar.project.exchange.addresses import shift_config
from mapchar.project.exchange.atlas import (
    atlas_text,
    read_atlas,
    write_abcde_table,
    write_atlas,
)
from mapchar.project.exchange.cartographer import (
    parse_command_file,
    write_command_file,
)
from mapchar.project.formats.legacy.abcde import read_abcde

BODY = ABC_TABLE + (
    "$F0=[color],u8\n!F1=[item] @items:1\n@table items\n01=[Herb]\n!FF=return\n"
)
TS = table_set(BODY, "main")


def test_abcde_tables_roundtrip():
    tables = tables_from(BODY)
    text = write_abcde_table(tables["main"])
    items = write_abcde_table(tables["items"])
    assert "!F1=<[item]>,<@items>:1" in text and "!FF=,-1" in items
    assert "!F0=<[color]>,1" in text and "/00=[end]" in text and "FE=[line]\\n" in text
    back = {tid: read_abcde(x).table for tid, x in (("main", text), ("items", items))}
    assert back["main"].entries["11110001"].params[0].table_id == "items"
    assert back["items"].entries["11111111"].kind.value == "return"


def test_atlas_text():
    assert atlas_text("A[color $03]B[$1F][end]", TS) == "A[color]<$03>B<$1F>[end]"
    assert atlas_text("x\\[y\\]", None) == "x[y]"


def test_write_and_read_atlas_script(registry):
    data = pointer_rom((0x10, 0x13), "41 42 00 43 00")
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
    )
    strings, _ = translated(data, cfg, TS, {0: "AB[color $03]C[end]"}, registry)
    export = write_atlas(
        "D", cfg, strings, TS, {f"{t.id}.tbl": t for t in TS.tables.values()}
    )
    s = export.script
    assert '#ADDTBL("main.tbl", Table_0)' in s and "#ACTIVETBL(Table_0)" in s
    assert '#ADDTBL("items.tbl", Table_1)' in s
    assert '#SMA("LINEAR")' in s and "#JMP($10, $18)" in s
    assert "#W16($0)\nAB[color]<$03>C[end]" in s and "#W16($2)\nC[end]" in s
    assert "@main" in export.tables["main.tbl"]
    back = read_atlas(s)
    assert [(a.text, a.pointers) for a in back.strings] == [
        ("AB[color]<$03>C[end]".replace("<$03>", "[$03]"), (0,)),
        ("C[end]", (2,)),
    ]
    assert back.strings[0].insert_at == 0x10 and len(back.tables) == 2
    stopped = read_atlas(s + "#AUTOCMD($1, #JMP($2))\nX\n")
    assert stopped.stopped_at is not None and len(stopped.strings) == 2


def test_atlas_addresses_are_file_offsets(registry):
    """Atlas writes to the file, so the export carries the header on every address."""
    header = 0x200
    data = pointer_rom((0x10, 0x13), "41 42 00 43 00")
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
    )
    ex = extract(data, cfg, TS, registry)
    export = write_atlas(
        "D",
        shift_config(cfg, header),
        ex.strings,
        TS,
        {f"{t.id}.tbl": t for t in TS.tables.values()},
        header=header,
    )
    s = export.script
    assert f"#JMP(${0x10 + header:X}, ${0x18 + header:X})" in s
    assert f"#HDR(${header:X})" in s
    assert f"#W16(${header:X})\n" in s and f"#W16(${header + 2:X})\n" in s
    back = read_atlas(s)
    assert back.strings[0].insert_at - header == 0x10
    assert [tuple(a - header for a in x.pointers) for x in back.strings] == [(0,), (2,)]
    assert [x.text for x in back.strings] == [r.current_text() for r in ex.strings]


def test_cartographer_export_roundtrip():
    cfg = BlockConfig(
        PointerTableSource(0x100, 0x110, 2, 3, "little", "linear", -0x8000),
        EndToken(),
        "upper",
        bound=0x2000,
        strings_per_pointer=2,
        realign=(4, 0),
        skips=((0x200, 0x210),),
    )
    text, notes = write_command_file(
        "Names", cfg, "main.tbl", table_id="upper", game_name="G"
    )
    assert not notes
    back = parse_command_file(text).blocks[0]
    assert back.config == cfg and back.name == "Names" and back.table_id == "upper"
    cfg2 = BlockConfig(RangeSource(0, 0x40), EndToken(), "main", bound=0x40)
    text, notes = write_command_file("Raw", cfg2, "main.tbl", table_id="main")
    assert parse_command_file(text).blocks[0].config == cfg2


def test_what_atlas_and_cartographer_cannot_express_is_said(registry):
    nested = BlockConfig(
        NestedPointerSource(0, 4, 2, 4, null=0, inner_null=0), EndToken(), "main"
    )
    data = bytes.fromhex("04 00 08 00 01 00 03 00 FF 41 00 43 00")
    ex = extract(data, nested, TS, registry)
    export = write_atlas("N", nested, ex.strings, TS, {})
    assert any("nested" in n for n in export.notices)
    assert "#W16" not in export.script and "#JMP($9, $A)" in export.script
    text, notes = write_command_file("N", nested, "main.tbl")
    assert not text and "nested" in notes[0]
    table = BlockConfig(PointerTableSource(0, 4, 2, 2, null=0), EndToken(), "main")
    text, notes = write_command_file("T", table, "main.tbl")
    assert text and any("null" in n for n in notes)
    # A fixed string's end token is not its text, but it is bytes Atlas writes;
    # a fill wider than a byte pads with its first.
    fixed = BlockConfig(
        RangeSource(0, 4), FixedLength(4, True), "main", fill=b"\xee\xdd"
    )
    ex = extract(bytes.fromhex("41 00 EE DD"), fixed, TS, registry)
    assert ex.strings[0].current_text() == "A"
    export = write_atlas("F", fixed, ex.strings, TS, {})
    assert "#FIXEDLENGTH(4, $EE)" in export.script
    assert "\nA[end]\n" in export.script
    assert any("fill" in n for n in export.notices)
