from __future__ import annotations

import pytest

from helpers import ABC_TABLE, table_set, translated
from mapchar.core.block import (
    BlockConfig,
    EndToken,
    FixedLength,
    Lines,
    NestedPointerSource,
    PointerListSource,
    PointerTableSource,
    RangeSource,
    WriteMode,
)
from mapchar.core.errors import ScriptError
from mapchar.core.numbers import format_num, parse_num
from mapchar.pipeline.extract import extract
from mapchar.project.formats.script import (
    DumpMode,
    format_config,
    parse_config,
    parse_script,
    write_script,
)
from mapchar.project.formats.textfile import escape, unescape

TS = table_set(ABC_TABLE + "40=@\n23=#\n5C=\\\\\n", "main")


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
        fill=b"\x00",
        show_end=True,
        end_label="fin",
    )
    spec = format_config(cfg)
    assert parse_config(spec) == cfg
    assert "type=fixed:8:stop" in spec and "mode=slotted" in spec


def test_nested_null_and_fill_config():
    spec = (
        "source=nested start=$136A6F8 stop=$136B698 size=4 stride=8 endian=little "
        "mapping=linear offset=20358900 bank=0 null=$0 inner_size=2 "
        "inner_endian=little inner_null=$0 type=end table=m3"
    )
    cfg = parse_config(spec)
    assert cfg.source == NestedPointerSource(
        0x136A6F8, 0x136B698, 4, 8, offset=0x136A6F4, null=0, inner_null=0
    )
    assert format_config(cfg) == spec
    cfg = BlockConfig(
        PointerTableSource(0, 8, 2, 2, null=0xFFFF),
        FixedLength(18, True),
        "m3",
        fill=b"\xff\xff",
    )
    spec = format_config(cfg)
    assert "null=$FFFF" in spec and "fill=$FFFF" in spec
    assert parse_config(spec) == cfg
    listed = BlockConfig(PointerListSource((2, 4), 2, null=0), EndToken(), "m")
    assert parse_config(format_config(listed)) == listed
    # A nested source's stride defaults to a record of two pointers; a fill is
    # a byte for every two digits, and a decimal fill one byte.
    assert parse_config("source=nested start=0 stop=8 size=4").source.stride == 8
    assert (
        parse_config("source=range start=0 stop=1 fill=$0FFFF").fill == b"\x00\xff\xff"
    )
    assert parse_config("source=range start=0 stop=1 fill=0").fill == b"\x00"
    assert "fill" not in format_config(
        parse_config("source=range start=0 stop=1 fill=$FF")
    )


def test_lines_config():
    cfg = BlockConfig(RangeSource(0, 8), Lines(8), "main", line_label="br")
    spec = format_config(cfg)
    assert "type=lines:8" in spec and parse_config(spec) == cfg


def test_write_and_parse():
    data = bytes.fromhex("41 FE 42 00 40 23 5C 00")
    cfg = BlockConfig(RangeSource(0, 8), EndToken(), "main")
    strings, _ = translated(data, cfg, TS, {1: "B[end]"})
    text = write_script(
        [("Dialogue", cfg, strings)], DumpMode.BOTH, rom="rom.nes", tables=["t.tbl"]
    )
    assert text.startswith('@mapchar script 1\n@rom "rom.nes"\n@table "t.tbl"\n')
    assert "@string 0 at $0-$4\n# A[line]\n# B[end]\nA[line]\nB[end]\n" in text
    assert "\n# @#\\\\[end]\n" in text
    script = parse_script(text)
    assert script.rom == "rom.nes" and script.tables == ["t.tbl"]
    block = script.blocks[0]
    assert block.name == "Dialogue" and block.config == cfg
    assert block.strings[0].text == "A[line]B[end]"
    assert block.strings[1].text == "B[end]"
    assert (block.strings[1].start, block.strings[1].end) == (4, 6)
    originals = write_script([("Dialogue", cfg, strings)], DumpMode.ORIGINALS)
    assert "\n\\@#\\\\[end]\n" in originals
    assert parse_script(originals).blocks[0].strings[1].text == "@#\\\\[end]"


def test_a_line_break_in_a_quoted_name_round_trips():
    # The quoted fields are spelled by textfile.escape, which escapes the line
    # break a block name or a path may carry; a local copy of the rule did not.
    data = bytes.fromhex("41 00")
    cfg = BlockConfig(RangeSource(0, 2), EndToken(), "main")
    ex = extract(data, cfg, TS)
    text = write_script([("two\nlines", cfg, ex.strings)], rom='a"b\nc')
    assert '@rom "a\\"b\\nc"' in text
    script = parse_script(text)
    assert script.rom == 'a"b\nc' and script.blocks[0].name == "two\nlines"


def test_script_errors():
    with pytest.raises(ScriptError):
        parse_script("hello\n")
    with pytest.raises(ScriptError):
        parse_script("@mapchar script 1\ntext without string\n")
    with pytest.raises(ScriptError):
        parse_script('@mapchar script 1\n@block "b" source=bogus\n')


def test_numbers_and_escapes_round_trip():
    """The number and escape spelling every table and script format shares."""
    assert [parse_num(t) for t in ("$FF", "-$10", "$-10", "16")] == [255, -16, -16, 16]
    assert format_num(255) == "$FF" and format_num(-16) == "$-10"
    raw = "a\\b\nc\td"
    assert escape(raw, "\t") == "a\\\\b\\nc\\td" and unescape(escape(raw, "\t")) == raw
