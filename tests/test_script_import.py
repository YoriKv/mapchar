from __future__ import annotations

from helpers import ABC_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource, Status
from mapchar.pipeline.exchange.script_import import apply_script
from mapchar.pipeline.extract import extract
from mapchar.project.formats.script import DumpMode, parse_script, write_script

TS = table_set(ABC_TABLE, "main")


def test_apply_script_roundtrip():
    data = bytes.fromhex("41 00 42 00")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    ex = extract(data, cfg, TS)
    ex.strings[0].translation = "B[end]"
    text = write_script([("D", cfg, ex.strings)], DumpMode.TRANSLATIONS)
    fresh = extract(data, cfg, TS).strings
    report = apply_script(parse_script(text), {"D": fresh})
    assert report.applied == 2 and not report.notices
    assert fresh[0].translation == "B[end]" and fresh[0].status is Status.EDITED
    assert fresh[1].translation is None and fresh[1].status is Status.UNTOUCHED
    other = parse_script(text.replace('@block "D"', '@block "E"'))
    report = apply_script(other, {"D": fresh})
    assert report.new_blocks[0][0] == "E" and report.new_blocks[0][1] == cfg
