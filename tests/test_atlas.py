from __future__ import annotations

import subprocess

from conftest import ABCDE, needs_abcde
from helpers import ABC_TABLE, pointer_rom, table_set, tables_from
from mapchar.core.block import BlockConfig, EndToken, PointerTableSource, RangeSource
from mapchar.pipeline.exchange.atlas import (
    atlas_text,
    read_atlas,
    write_abcde_table,
    write_atlas,
)
from mapchar.pipeline.exchange.cartographer import (
    parse_command_file,
    write_command_file,
)
from mapchar.pipeline.extract import extract
from mapchar.project.formats.table_legacy import read_abcde

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
    ex = extract(data, cfg, TS, registry)
    ex.strings[0].translation = "AB[color $03]C[end]"
    export = write_atlas(
        "D", cfg, ex.strings, TS, {f"{t.id}.tbl": t for t in TS.tables.values()}
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


@needs_abcde
def test_atlas_export_inserts_like_mapchar(tmp_path, registry):
    """abcde's Atlas over the export writes what mapchar's layout writes."""
    from mapchar.pipeline.insert import apply_splices, layout_block

    data = pointer_rom((0x10, 0x13), "41 42 00 43 00")
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
        fill=0xFF,
    )
    ex = extract(data, cfg, TS, registry)
    ex.strings[0].translation = "ABC[item][Herb][end]"
    ex.strings[1].translation = "B[end]"
    res = layout_block(data, cfg, TS, ex.strings, registry)
    assert res.ok
    expected = apply_splices(data, res.splices)
    export = write_atlas(
        "D", cfg, ex.strings, TS, {f"{t.id}.tbl": t for t in TS.tables.values()}
    )
    for name, text in export.tables.items():
        (tmp_path / name).write_text(text)
    (tmp_path / "script.txt").write_text(export.script)
    target = tmp_path / "rom.bin"
    target.write_bytes(data)
    result = subprocess.run(
        [
            "perl",
            str(ABCDE / "abcde.pl"),
            "-cm",
            "abcde::Atlas",
            "rom.bin",
            "script.txt",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert target.read_bytes() == expected


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
