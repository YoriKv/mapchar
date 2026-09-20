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
from mapchar.project.formats.summary import (
    EDIT,
    NEW_BLOCK,
    summarise_records,
    summarise_script,
)
from mapchar.project.formats.translator import Record, apply_records

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


# -- the summary an import is confirmed by -------------------------------------


def test_a_script_summary_counts_a_new_block_from_the_script():
    """The block does not exist yet, so ``apply_script`` places nothing in it.
    The summary still counts its strings: the import creates the block and
    plans again, which is the pass that lands them."""
    data = bytes.fromhex("41 00 42 00")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    strings, _ = translated(data, cfg, TS, {0: "B[end]"})
    text = write_script([("D", cfg, strings)], DumpMode.TRANSLATIONS)
    script = parse_script(text)

    known = extract(data, cfg, TS).strings
    summary = summarise_script(script, apply_script(script, {"D": known}))
    assert summary.kind == "Native script"
    assert [(b.name, b.strings, b.action) for b in summary.blocks] == [("D", 2, EDIT)]
    assert not summary.skipped and not summary.forceable

    fresh = summarise_script(script, apply_script(script, {}))
    assert [(b.name, b.strings, b.action) for b in fresh.blocks] == [
        ("D", 2, NEW_BLOCK)
    ]
    assert not fresh.nothing_to_do


def test_a_record_summary_counts_every_string_the_records_reach():
    """A record carrying nothing but a status or a note still lands on its
    string, so the count is what the import touches, not what it rewrites."""
    data = bytes.fromhex("41 00 42 00")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    fresh = extract(data, cfg, TS).strings
    records = [
        Record("D/0", 0, "A[end]", "B[end]", "edited", ""),
        Record("D/1", 2, "B[end]", "", "done", "checked"),
        Record("D/2", 4, "C[end]", "C[end]", "edited", ""),
        Record("E/0", 0, "A[end]", "B[end]", "edited", ""),
    ]
    summary = summarise_records("PO file", apply_records(records, {"D": fresh}))
    assert summary.kind == "PO file" and summary.forceable
    assert [(b.name, b.strings) for b in summary.blocks] == [("D", 2)]
    assert summary.skipped == ["D/2: no such string", "E/0: no such block"]


def test_force_is_what_takes_a_drifted_record_back():
    data = bytes.fromhex("41 00 42 00")
    cfg = BlockConfig(RangeSource(0, 4), EndToken(), "main")
    fresh = extract(data, cfg, TS).strings
    records = [Record("D/0", 0, "moved on[end]", "B[end]", "edited", "")]
    plain = summarise_records("TSV", apply_records(records, {"D": fresh}))
    assert plain.skipped == ["D/0: original changed"] and not plain.blocks
    assert plain.nothing_to_do
    forced = summarise_records("TSV", apply_records(records, {"D": fresh}, force=True))
    assert not forced.skipped
    assert [(b.name, b.strings) for b in forced.blocks] == [("D", 1)]
