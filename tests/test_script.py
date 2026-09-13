from __future__ import annotations

import pytest

from helpers import table_set
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    RangeSource,
    WriteMode,
)
from mapchar.core.errors import ScriptError
from mapchar.pipeline.extract import extract
from mapchar.project.formats.script import (
    DumpMode,
    format_config,
    parse_config,
    parse_script,
    write_script,
)

TS = table_set(
    "@table main\n41=A\n42=B\n40=@\n23=#\n5C=\\\\\n/00=[end]\nFE=[line]\\n\n", "main"
)


def test_config_roundtrip():
    cfg = BlockConfig(
        RangeSource(0x100, 0x200),
        FixedLength(8, True),
        "main",
        strings_per_pointer=2,
        realign=(4, 1),
        skips=((0x10, 0x20),),
        line_length=4,
        bound=0x1F0,
        write_mode=WriteMode.SLOTTED,
        fill=0,
        show_end=True,
        end_label="fin",
    )
    spec = format_config(cfg)
    assert parse_config(spec) == cfg
    assert "type=fixed:8:stop" in spec and "mode=slotted" in spec


def test_write_and_parse():
    data = bytes.fromhex("41 FE 42 00 40 23 5C 00")
    cfg = BlockConfig(RangeSource(0, 8), EndToken(), "main")
    ex = extract(data, cfg, TS)
    ex.strings[1].translation = "B[end]"
    text = write_script(
        [("Dialogue", cfg, ex.strings)], DumpMode.BOTH, rom="rom.nes", tables=["t.tbl"]
    )
    assert text.startswith('@mapchar script 1\n@rom "rom.nes"\n@table "t.tbl"\n')
    assert "@string 0 at $0-$4\n# A[line]\n# B[end]\nA[line]\nB[end]\n" in text
    assert "\n# @#\\\\[end]\n" in text
    script = parse_script(text)
    assert script.rom == "rom.nes" and script.tables == ["t.tbl"]
    block = script.blocks[0]
    assert block.name == "Dialogue" and block.config == cfg
    assert block.strings[0].text == "A[line]B[end]"
    assert block.strings[0].original == "A[line]B[end]"
    assert (
        block.strings[1].text == "B[end]" and block.strings[1].original == "@#\\\\[end]"
    )
    assert (block.strings[1].start, block.strings[1].end) == (4, 8)
    originals = write_script([("Dialogue", cfg, ex.strings)], DumpMode.ORIGINALS)
    assert "\n\\@#\\\\[end]\n" in originals
    assert parse_script(originals).blocks[0].strings[1].text == "@#\\\\[end]"


def test_script_errors():
    with pytest.raises(ScriptError):
        parse_script("hello\n")
    with pytest.raises(ScriptError):
        parse_script("@mapchar script 1\ntext without string\n")
    with pytest.raises(ScriptError):
        parse_script('@mapchar script 1\n@block "b" source=bogus\n')
