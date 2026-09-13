from __future__ import annotations

import unicodedata

from helpers import ABC_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource, Status
from mapchar.pipeline.extract import extract
from mapchar.project.formats.translator import (
    apply_records,
    read_delimited,
    read_po,
    records_for,
    write_delimited,
    write_po,
)

TS = table_set(ABC_TABLE, "main")


def strings():
    data = bytes.fromhex("41 FE 42 00 42 00")
    ex = extract(data, BlockConfig(RangeSource(0, 6), EndToken(), "main"), TS)
    ex.strings[1].translation = 'A "quoted"\ttab[end]'
    ex.strings[1].status = Status.REVIEW
    ex.strings[1].notes = "check\nme"
    return ex.strings


def test_tsv_and_csv_roundtrip():
    recs = records_for("Dialogue", strings())
    for delim in ("\t", ","):
        text = write_delimited(recs, delim)
        back = read_delimited(text)
        assert [r.id for r in back] == ["Dialogue/0", "Dialogue/1"]
        assert back[0].original == "A[line]\nB[end]" and back[0].address == 0
        assert back[1].translation == 'A "quoted"\ttab[end]'
        assert back[1].status == "review" and back[1].notes == "check\nme"


def test_po_roundtrip():
    recs = records_for("Dialogue", strings())
    text = write_po(recs, "game.nes")
    assert "#, fuzzy" in text and "#: game.nes:$4" in text
    back = read_po(text)
    assert [r.id for r in back] == ["Dialogue/0", "Dialogue/1"]
    assert back[0].original == "A[line]\nB[end]" and back[0].translation == ""
    assert back[1].translation == 'A "quoted"\ttab[end]' and back[1].status == "review"
    assert back[1].notes == "check\nme" and back[1].address == 4


def test_apply_records():
    target = strings()
    for s in target:
        s.translation, s.status, s.notes = None, Status.UNTOUCHED, ""
    recs = records_for("Dialogue", strings())
    recs[0].translation = "B[line]A[end]"
    recs.append(type(recs[0])("Dialogue/9", 0, "", "x", "edited", ""))
    recs.append(type(recs[0])("Other/0", 0, "", "x", "edited", ""))
    drift = type(recs[0])("Dialogue/0", 0, "Z[end]", "y", "edited", "")
    report = apply_records(recs + [drift], {"Dialogue": target})
    assert report.applied == 2
    assert (
        target[0].translation == "B[line]A[end]" and target[0].status is Status.EDITED
    )
    assert target[1].status is Status.REVIEW and target[1].notes == "check\nme"
    assert len(report.skipped) == 3
    report = apply_records([drift], {"Dialogue": target}, force=True)
    assert report.applied == 1 and target[0].translation == "y"


def test_csv_carries_a_byte_order_mark_and_kana_survives_both_ways():
    recs = records_for("会話", strings())
    recs[0].translation = "はじめまして[end]"
    recs[0].notes = "漢字, with a comma"
    csv_text = write_delimited(recs, ",")
    assert csv_text.startswith("\ufeff")  # what a spreadsheet needs to read UTF-8
    assert not write_delimited(recs, "\t").startswith("\ufeff")
    assert "\ufeff" not in write_po(recs)
    for text in (csv_text, csv_text.lstrip("\ufeff"), write_delimited(recs, "\t")):
        back = read_delimited(text)
        assert [r.id for r in back] == ["会話/0", "会話/1"]
        assert back[0].translation == "はじめまして[end]"
        assert back[0].notes == "漢字, with a comma"


def test_a_decomposed_record_still_finds_its_string():
    target = strings()
    for s in target:
        s.translation, s.status = None, Status.UNTOUCHED
    target[0].original = list(target[0].original)
    recs = records_for("Dialogue", target)
    # A translator's editor may hand the original back decomposed; the row is
    # about the same string, so it is not "original changed".
    recs[0].original = unicodedata.normalize("NFD", "A[line]\nB[end]")
    recs[0].translation = unicodedata.normalize("NFD", "あが[end]")
    report = apply_records(recs, {"Dialogue": target})
    assert report.skipped == [] and report.applied == 2
    assert target[0].translation == "あが[end]"
    assert unicodedata.is_normalized("NFC", target[0].translation)
