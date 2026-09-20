from __future__ import annotations

import time

import pytest

from conftest import ROOT
from helpers import ASCII_TABLE, table_set, texts
from mapchar.core.block import BlockConfig, EndToken, Pascal, RangeSource
from mapchar.core.table import TableSet
from mapchar.engines.textscan import Records, scan, score_window
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


def test_a_table_with_no_end_token_still_finds_terminated_text():
    """A table fresh from a relative search has no end token, so the byte
    between two runs of characters is the break, and the region comes back cut
    to the text with that byte guessed as its terminator."""
    tables = table_set("@table main\n@charset ascii\n", "main")
    data = NOISE + TERMINATED * 4 + NOISE
    regions = scan(data, tables)
    assert len(regions) == 1
    region = regions[0]
    assert (region.start, region.end) == (len(NOISE), len(NOISE) + len(TERMINATED) * 4)
    assert region.terminator == 0x00


def test_codes_inside_a_string_do_not_break_the_run():
    """An operand code is the text's own: only bytes the table cannot read at
    all cut a run of strings."""
    tables = table_set(ASCII_TABLE + "$FC=[color],u8\n", "main")
    text = (
        b"\xfc\x01THE KING SAID HELLO TO YOU\x00"
        b"\xfc\x02YOU ARE THE KING\x00"
        b"\xfc\x01GOOD ITEM FOR YOU\x00"
    ) * 6
    data = NOISE + text + NOISE
    regions = scan(data, tables)
    assert [(r.start, r.end) for r in regions] == [(len(NOISE), len(NOISE) + len(text))]


def test_two_runs_of_text_in_one_region_come_back_as_two():
    """Unreadable bytes between two runs of strings end one region and begin
    another; neither is lost to the other."""
    data = NOISE + TERMINATED * 4 + b"\x80\x81\x82\x83" + TERMINATED * 8 + NOISE
    first = len(NOISE)
    second = first + len(TERMINATED) * 4 + 4
    bounds = sorted((r.start, r.end) for r in scan(data, TS))
    assert bounds == [
        (first, first + len(TERMINATED) * 4),
        (second, second + len(TERMINATED) * 8),
    ]


def test_a_region_reaches_text_that_begins_before_its_first_window():
    """The window that scored may begin after its text does: the search for
    strings starts a window back, so the region holds the whole first string."""
    long = b"QZXV JKPW BBRT MNBV CXZL KJHG\x00"
    data = NOISE[:250] + long * 8 + NOISE
    regions = scan(data, TS, window=64, step=32)
    assert [(r.start, r.end) for r in regions] == [(250, 250 + len(long) * 8)]


def test_one_run_of_text_comes_back_as_one_region():
    """Two coarse regions over one continuous run of strings are one region,
    not two that overlap and cut a string in half."""
    long = b"QZXV JKPW BBRT MNBV CXZL KJHG\x00"
    data = NOISE[:20] + long * 8 + b"Q\x00" * 25 + long * 8 + NOISE
    regions = scan(data, TS)
    assert [(r.start, r.end) for r in regions] == [(20, len(data) - len(NOISE))]


@pytest.mark.parametrize("pad", [0, 1, 7, 13, 31])
def test_text_is_found_whatever_it_is_aligned_to(pad):
    """Text that does not begin on a multiple of the step is still cut to its
    own strings."""
    data = NOISE[: 128 + pad] + TERMINATED * 6 + NOISE
    regions = scan(data, TS, window=64, step=32)
    at = 128 + pad
    assert [(r.start, r.end) for r in regions] == [(at, at + len(TERMINATED) * 6)]


def test_a_chain_of_one_character_records_is_not_a_region():
    """A chain earns its place by how many characters a record holds: under a
    table that reads small byte values as text, a header with a byte behind it
    is a table of screen positions, not of strings."""
    body = "".join(f"{b:02X}={chr(0x41 + b % 26)}\n" for b in range(0x40))
    small = table_set("@table main\n" + body)
    data = NOISE + b"\x20\x60\x01\x00" + b"\x30\x60\x01\x00" * 60 + NOISE
    assert all(r.records is None for r in scan(data, small))


def test_an_empty_record_does_not_end_a_chain():
    """A record with no characters is crossed, not counted: the records behind
    it are part of the same chain."""
    names = [
        b"ALEXANDRIA",
        b"BRIGANTINE",
        b"CARTHAGENA",
        b"DORCHESTER",
        b"EDINBURGH!",
        b"FRANKFURTS",
    ]
    chain = b"".join(b"\x90\xa1" + bytes([len(n)]) + n for n in names[:4])
    chain += b"\x90\xa1\x00"
    chain += b"".join(b"\x90\xa1" + bytes([len(n)]) + n for n in names[4:])
    data = NOISE + chain + NOISE
    regions = [r for r in scan(data, TS) if r.records is not None]
    assert len(regions) == 1
    assert (regions[0].start, regions[0].end) == (len(NOISE), len(NOISE) + len(chain))
    ex = extract(
        data,
        BlockConfig(
            RangeSource(regions[0].start, regions[0].end),
            Pascal(1),
            "main",
            header=2,
        ),
        TS,
    )
    assert texts(ex) == [n.decode() for n in names[:4]] + [""] + [
        n.decode() for n in names[4:]
    ]


def test_one_long_string_is_a_region_of_its_own():
    """A game's intro or ending is one string: terminated text earns its region
    with its score and its length, not by holding several strings."""
    long = (b"THE KING SAID HELLO TO YOU AND YOU ARE HERE IN THE CASTLE " * 5)[:280]
    for count in (1, 2):
        data = NOISE + (long + b"\x00") * count + NOISE
        regions = scan(data, TS)
        assert [(r.start, r.end) for r in regions] == [
            (len(NOISE), len(NOISE) + 281 * count)
        ]


def test_a_scan_stopped_while_scoring_still_cuts_what_scored():
    """Stop stays pressed, so the second half of the work is never asked: a
    scan cut short while scoring the windows still cuts what did score and
    hands the regions back."""
    data = NOISE + TERMINATED * 40 + NOISE
    pressed = False

    def stop_halfway(done: int, total: int) -> bool:
        nonlocal pressed
        pressed = pressed or done >= 1024
        return not pressed

    regions = scan(data, TS, window=32, step=8, progress=stop_halfway)
    assert regions and all(r.records is None for r in regions)
    assert regions[0].start == len(NOISE)


def test_a_stopped_scan_returns_what_it_had_refined():
    """Cutting the regions to what they hold reports its own progress, so Stop
    reaches it and a scan stopped there still ranks what it had."""
    data = NOISE + TERMINATED * 4 + NOISE + TERMINATED * 4 + NOISE
    assert len(scan(data, TS)) == 2
    refining: list[int] = []

    def stop_after_one(done: int, total: int) -> bool:
        if done >= len(data):  # the second half of the work is the refining
            refining.append(done)
        return len(refining) < 2

    assert len(scan(data, TS, progress=stop_after_one)) == 1


def test_a_long_record_chain_does_not_slow_the_scan_down():
    """Chains are found once over the whole buffer, not again for every window
    that scored inside one: a chain of thousands of records is walked once."""
    records = [
        (b"THE KING SAID HELLO TO YOU AND TO THE QUE" if i % 40 == 0 else b"QZXVJ")
        for i in range(4000)
    ]
    data = b"".join(b"\x90\xa1\x85\x93" + bytes([len(r)]) + r for r in records)
    started = time.perf_counter()
    regions = scan(data, TS)
    assert time.perf_counter() - started < 5.0
    assert any(r.records == Records(header=4) for r in regions)


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
