from __future__ import annotations

import pytest

from conftest import ROOT
from helpers import ASCII_TABLE, table_set, texts
from mapchar.core.block import BlockConfig, EndToken, Pascal, RangeSource
from mapchar.core.table import TableSet
from mapchar.engines.scan import Records, scan, score_window
from mapchar.pipeline.extract import extract
from mapchar.plugins.charsets import apply_charset
from mapchar.project.formats.table_native import parse_native

TS = table_set(ASCII_TABLE, "main")

NOISE = bytes(range(0x80, 0x100)) * 2
"""Bytes the ASCII table reads as nothing, in front of and behind the text."""

RECORDS = ("THE KING IS HERE", "YOU ARE THE ONE", "FIND THE GOLD", "SAVE THE GAME")
"""What the synthetic ``[2 header bytes][length][characters]`` chain holds."""

TERMINATED = b"THE KING SAID HELLO\x00YOU ARE HERE\x00GOOD ITEM\x00"


def records_and_text() -> tuple[bytes, int, int]:
    """A buffer of noise, a chain of length-prefixed records, more noise and a
    run of terminated strings, with where each of the two starts."""
    chain = b"".join(b"\x90\xa1" + bytes([len(t)]) + t.encode() for t in RECORDS)
    text = TERMINATED * 3
    data = NOISE + chain + NOISE + text + NOISE
    return data, len(NOISE), len(NOISE) + len(chain) + len(NOISE)


def test_scan_finds_text_and_terminator():
    text = b"THE KING SAID HELLO\x00YOU ARE HERE\x00GOOD ITEM\x00"
    data = bytes(range(0x80, 0x100)) * 3 + text * 4 + bytes(range(0x80, 0x100)) * 3
    regions = scan(data, TS, window=32, step=16, threshold=0.6)
    assert regions
    best = regions[0]
    start = len(bytes(range(0x80, 0x100)) * 3)
    assert best.start <= start + 16 and best.end >= start + len(text) * 4 - 16
    assert best.terminator == 0x00 and best.initial in (ord("T"), ord("Y"), ord("G"))
    assert score_window(text, TS)[0] > 0.8
    assert score_window(bytes(range(0x80, 0xC0)), TS)[0] < 0.2
    assert scan(bytes(range(0x80, 0x100)) * 4, TS, threshold=0.6) == []


def test_a_scan_tells_a_record_chain_from_terminated_text():
    """The two runs come back as two regions, each on its own bounds and each
    saying how its strings are cut — not on the window grid, and not merged."""
    data, chain_at, text_at = records_and_text()
    regions = sorted(scan(data, TS), key=lambda r: r.start)
    assert len(regions) == 2
    chain, text = regions
    assert (chain.start, chain.end) == (chain_at, text_at - len(NOISE))
    assert chain.records == Records(header=2)
    assert (text.start, text.end) == (text_at, text_at + len(TERMINATED) * 3)
    assert text.records is None and text.terminator == 0x00


def test_the_blocks_a_scan_s_regions_describe_read_their_strings():
    """What each region says it is, read as a block: the chain behind its
    header and length prefix, the rest at its end token."""
    data, _, _ = records_and_text()
    chain, text = sorted(scan(data, TS), key=lambda r: r.start)
    ex = extract(
        data,
        BlockConfig(
            RangeSource(chain.start, chain.end),
            Pascal(chain.records.width),
            "main",
            header=chain.records.header,
        ),
        TS,
    )
    assert texts(ex) == list(RECORDS)
    ex = extract(
        data,
        BlockConfig(RangeSource(text.start, text.end), EndToken(), "main"),
        TS,
    )
    assert texts(ex)[:2] == ["THE KING SAID HELLO[end]", "YOU ARE HERE[end]"]


def test_ordinary_text_is_not_read_as_a_record_chain():
    """A small byte in front of a run of characters is not a length: a chain
    has to hold several records in a row, each behind a byte the table does not
    read as text."""
    data = NOISE + TERMINATED * 4 + NOISE
    assert all(r.records is None for r in scan(data, TS))


def test_the_mortal_kombat_ii_finishing_messages_scan_as_a_record_chain(registry):
    """The sample's records under its own table, when the ROM is present.

    The ROM never enters the repository; without it this skips. The finishing
    messages at ``$8646`` are part of one longer chain of records that runs
    from the winner messages at ``$8593``, and the block the region describes
    reads every one of them.
    """
    rom = ROOT / "sample-projects" / "MK2" / "Mortal Kombat II (USA, Europe).gb"
    folder = ROOT / "sample-projects" / "MK2"
    if not rom.exists():
        pytest.skip("the Mortal Kombat II ROM is not present")
    table = parse_native((folder / "mk2.tbl").read_text(encoding="utf-8")).table
    apply_charset(table, registry)
    ts = TableSet.build(table, {table.id: table})
    data = rom.read_bytes()
    regions = [r for r in scan(data, ts) if r.start <= 0x8646 < r.end]
    assert len(regions) == 1
    region = regions[0]
    assert region.records == Records(header=2)
    assert region.start <= 0x8646 and region.end >= 0x86A6
    ex = extract(
        data,
        BlockConfig(
            RangeSource(region.start, region.end),
            Pascal(region.records.width),
            table.id,
            header=region.records.header,
        ),
        ts,
        registry,
    )
    read = texts(ex)
    assert read[read.index("FINISH HIM!") :][:7] == [
        "FINISH HIM!",
        "FINISH HER!",
        "FLAWLESS VICTORY",
        "DOUBLE FLAWLESS",
        "DRAW",
        "FATALITY",
        "BABALITY!!",
    ]
    assert read[0] == "LIU KANG WINS"
