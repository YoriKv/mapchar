from __future__ import annotations

import unicodedata

from helpers import ABC_TABLE, table_set, translated
from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.pipeline.extract import extract
from mapchar.project.formats.script import (
    DumpMode,
    apply_script,
    parse_script,
    write_script,
)

TS = table_set(ABC_TABLE, "main")


def test_apply_script_roundtrip():
    data = bytes.fromhex("41 00 42 00")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    strings, _ = translated(data, cfg, TS, {0: "B[end]"})
    text = write_script([("D", cfg, strings)], DumpMode.TRANSLATIONS)
    fresh = extract(data, cfg, TS).strings
    report = apply_script(parse_script(text), {"D": fresh})
    assert report.applied == 2 and not report.notices
    # What each string is to say; putting it in the bytes is the window's.
    assert report.texts == {"D": {0: "B[end]", 1: "B[end]"}}
    assert [r.current_text() for r in fresh] == ["A[end]", "B[end]"]
    other = parse_script(text.replace('@block "D"', '@block "E"'))
    report = apply_script(other, {"D": fresh})
    assert report.new_blocks[0][0] == "E" and report.new_blocks[0][1] == cfg


KANA_TS = table_set("@table main\n41=が\n42=か\n/00=[end]\n", "main")


def kana_block():
    data = bytes.fromhex("41 00 42 00")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    return cfg, extract(data, cfg, KANA_TS).strings


def script_for(cfg, texts: list[str]) -> str:
    head = (
        "@mapchar script 1\n"
        '@block "D" source=range start=$0 stop=$4 type=end table=main\n'
    )
    body = "".join(
        f"@string {i} at ${i * 2:X}-${i * 2 + 2:X}\n{text}\n"
        for i, text in enumerate(texts)
    )
    return head + body


def test_a_decomposed_script_is_composed_on_import():
    cfg, fresh = kana_block()
    decomposed = unicodedata.normalize("NFD", "が")
    text = script_for(cfg, [f"{decomposed}[end]", f"{decomposed}{decomposed}[end]"])
    report = apply_script(parse_script(text), {"D": fresh})
    assert report.applied == 2 and not report.notices
    # The first string is its own original spelled decomposed: the same text.
    assert fresh[0].matches_original(report.texts["D"][0])
    assert unicodedata.normalize("NFC", report.texts["D"][1]) == "がが[end]"
