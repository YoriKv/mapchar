from __future__ import annotations

import unicodedata

from helpers import ABC_TABLE, table_set, translated
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


DATA = bytes.fromhex("41 FE 42 00 42 00 FF FF FF FF FF FF")
CFG = BlockConfig(RangeSource(0, 12), EndToken(), "main")
"""Two strings and room to spare, for one to grow into."""


def strings():
    """Two strings, the second translated, marked for review and annotated."""
    recs, _ = translated(DATA, CFG, TS, {1: "AB[end]"})
    recs[1].status = Status.REVIEW
    recs[1].notes = "check\nme"
    return recs


def records():
    """The records of :func:`strings`, the translation spelled with what a
    file has to escape: a quote and a tab."""
    recs = records_for("Dialogue", strings())
    recs[1].translation = 'A "quoted"\ttab[end]'
    return recs


def fresh():
    return extract(DATA, CFG, TS).strings


def test_tsv_and_csv_roundtrip():
    recs = records()
    for delim in ("\t", ","):
        text = write_delimited(recs, delim)
        back = read_delimited(text)
        assert [r.id for r in back] == ["Dialogue/0", "Dialogue/1"]
        assert back[0].original == "A[line]\nB[end]" and back[0].address == 0
        assert back[1].translation == 'A "quoted"\ttab[end]'
        assert back[1].status == "review" and back[1].notes == "check\nme"


def test_po_roundtrip():
    recs = records()
    recs[0].status = "done"
    text = write_po(recs, "game.nes")
    assert "#, fuzzy" in text and "#: game.nes:$4" in text and "\n# done\n" in text
    back = read_po(text)
    assert [r.id for r in back] == ["Dialogue/0", "Dialogue/1"]
    assert back[0].original == "A[line]\nB[end]" and back[0].translation == ""
    assert back[0].status == "done"
    assert back[1].translation == 'A "quoted"\ttab[end]' and back[1].status == "review"
    assert back[1].notes == "check\nme" and back[1].address == 4
    # Done travels through the delimited files as any status does.
    assert [r.status for r in read_delimited(write_delimited(recs))] == [
        "done",
        "review",
    ]
    report = apply_records(back, {"Dialogue": fresh()})
    assert report.done == {"Dialogue": {0: True}} and report.review == {
        "Dialogue": {1: True}
    }


def test_po_reads_every_entry_but_the_header():
    """The header has neither a context nor an original. Every other entry is
    a record: one whose original is empty, and one with no context — which has
    no id, so the import lists it instead of dropping it unseen."""
    text = "\n".join(
        [
            'msgid ""',
            'msgstr ""',
            '"Content-Type: text/plain; charset=UTF-8\\n"',
            "",
            'msgctxt "Dialogue/0"',
            'msgid ""',
            'msgstr "A[end]"',
            "",
            'msgid "B[end]"',
            'msgstr "C[end]"',
            "",
            'msgctxt ""',
            'msgid "D[end]"',
            'msgstr ""',
            "",
        ]
    )
    back = read_po(text)
    assert [(r.id, r.original, r.translation) for r in back] == [
        ("Dialogue/0", "", "A[end]"),
        ("", "B[end]", "C[end]"),
        ("", "D[end]", ""),
    ]
    report = apply_records(back, {"Dialogue": fresh()})
    assert report.applied == 1
    assert report.skipped == ["(no id): no such block"] * 2


def test_apply_records():
    target = fresh()
    recs = records()
    recs[0].translation = "B[line]A[end]"
    recs.append(type(recs[0])("Dialogue/9", 0, "", "x", "edited", ""))
    recs.append(type(recs[0])("Other/0", 0, "", "x", "edited", ""))
    drift = type(recs[0])("Dialogue/0", 0, "Z[end]", "y", "edited", "")
    report = apply_records(recs + [drift], {"Dialogue": target})
    assert report.applied == 2
    # What is to change comes back; the records themselves are left alone.
    assert report.texts == {"Dialogue": {0: "B[line]A[end]", 1: 'A "quoted"\ttab[end]'}}
    assert report.review == {"Dialogue": {1: True}}
    assert report.notes == {"Dialogue": {1: "check\nme"}}
    assert [r.current_text() for r in target] == ["A[line]\nB[end]", "B[end]"]
    assert len(report.skipped) == 3
    report = apply_records([drift], {"Dialogue": target}, force=True)
    assert report.applied == 1 and report.texts == {"Dialogue": {0: "y"}}


def test_csv_carries_a_byte_order_mark_and_kana_survives_both_ways():
    recs = records_for("会話", fresh())
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
    target = fresh()
    recs = records_for("Dialogue", target)
    # A translator's editor may hand the original back decomposed; the row is
    # about the same string, so it is not "original changed".
    recs[0].original = unicodedata.normalize("NFD", "A[line]\nB[end]")
    recs[0].translation = unicodedata.normalize("NFD", "あが[end]")
    report = apply_records(recs, {"Dialogue": target})
    assert report.skipped == [] and report.applied == 2
    assert unicodedata.normalize("NFC", report.texts["Dialogue"][0]) == "あが[end]"
