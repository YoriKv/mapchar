from __future__ import annotations

import os
import shutil
import subprocess

import pytest

from helpers import table_set, tables_from
from mapchar.core.block import BlockConfig, EndToken, PointerTableSource, RangeSource
from mapchar.pipeline.exchange.atlas import (
    atlas_text,
    read_atlas,
    write_abcde_tables,
    write_atlas,
)
from mapchar.pipeline.exchange.cartographer import (
    parse_command_file,
    write_command_file,
)
from mapchar.pipeline.extract import extract
from mapchar.plugins.registry import default_registry
from mapchar.project.formats.table_legacy import read_abcde

BODY = (
    "@table main\n41=A\n42=B\n43=C\n/00=[end]\nFE=[line]\\n\n$F0=[color],u8\n"
    "!F1=[item] @items:1\n@table items\n01=[Herb]\n!FF=return\n"
)
TS = table_set(BODY, "main")
REG = default_registry()
ABCDE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "abcde", "abcde.pl"
)


def test_abcde_tables_roundtrip():
    text = write_abcde_tables(list(tables_from(BODY).values()))
    assert "!F1=<[item]>,<@items>:1" in text and "!FF=,-1" in text
    assert "!F0=<[color]>,1" in text and "/00=[end]" in text and "FE=[line]\\n" in text
    back = {t.id: t for t in read_abcde(text).tables}
    assert back["main"].entries["11110001"].params[0].table_id == "items"
    assert back["items"].entries["11111111"].kind.value == "return"


def test_atlas_text():
    assert atlas_text("A[color $03]B[$1F][end]", TS) == "A[color]<$03>B<$1F>[end]"
    assert atlas_text("x\\[y\\]", None) == "x[y]"


def test_write_and_read_atlas_script():
    table = (0x10).to_bytes(2, "little") + (0x13).to_bytes(2, "little")
    data = table + b"\xff" * 12 + bytes.fromhex("41 42 00 43 00") + b"\xff" * 8
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
    )
    ex = extract(data, cfg, TS, REG)
    ex.strings[0].translation = "AB[color $03]C[end]"
    export = write_atlas(
        "D", cfg, ex.strings, TS, {"main.tbl": list(TS.tables.values())}
    )
    s = export.script
    assert '#ADDTBL("main_main.tbl", Table_0)' in s and "#ACTIVETBL(Table_0)" in s
    assert '#ADDTBL("main_items.tbl", Table_1)' in s
    assert '#SMA("LINEAR")' in s and "#JMP($10, $18)" in s
    assert "#W16($0)\nAB[color]<$03>C[end]" in s and "#W16($2)\nC[end]" in s
    assert "@main" in export.tables["main_main.tbl"]
    back = read_atlas(s)
    assert [(a.text, a.pointers) for a in back.strings] == [
        ("AB[color]<$03>C[end]".replace("<$03>", "[$03]"), (0,)),
        ("C[end]", (2,)),
    ]
    assert back.strings[0].insert_at == 0x10 and len(back.tables) == 2
    stopped = read_atlas(s + "#AUTOCMD($1, #JMP($2))\nX\n")
    assert stopped.stopped_at is not None and len(stopped.strings) == 2


@pytest.mark.skipif(
    shutil.which("perl") is None or not os.path.exists(ABCDE),
    reason="abcde not available",
)
def test_atlas_export_inserts_like_mapchar(tmp_path):
    """abcde's Atlas over the export writes what mapchar's layout writes."""
    from mapchar.pipeline.insert import apply_splices, layout_block

    table = (0x10).to_bytes(2, "little") + (0x13).to_bytes(2, "little")
    data = table + b"\xff" * 12 + bytes.fromhex("41 42 00 43 00") + b"\xff" * 8
    cfg = BlockConfig(
        PointerTableSource(0, 4, 2, 2, "little", "linear"),
        EndToken(),
        "main",
        bound=0x19,
        fill=0xFF,
    )
    ex = extract(data, cfg, TS, REG)
    ex.strings[0].translation = "ABC[item][Herb][end]"
    ex.strings[1].translation = "B[end]"
    res = layout_block(data, cfg, TS, ex.strings, REG)
    assert res.ok
    expected = apply_splices(data, res.splices)
    export = write_atlas(
        "D", cfg, ex.strings, TS, {"main.tbl": list(TS.tables.values())}
    )
    for name, text in export.tables.items():
        (tmp_path / name).write_text(text)
    (tmp_path / "script.txt").write_text(export.script)
    target = tmp_path / "rom.bin"
    target.write_bytes(data)
    result = subprocess.run(
        ["perl", ABCDE, "-cm", "abcde::Atlas", "rom.bin", "script.txt"],
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
