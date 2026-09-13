from __future__ import annotations

from helpers import table_set
from mapchar.engines.scan import scan, score_window

TS = table_set("@table main\n@charset ascii\n/00=[end]\n", "main")


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
