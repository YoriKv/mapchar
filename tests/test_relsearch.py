from __future__ import annotations

import pytest

from mapchar.engines.relsearch import entries_from_hit, relative_search


def encode(text: str, upper: int, lower: int, digit: int = 0x30) -> bytes:
    out = bytearray()
    for ch in text:
        if ch.isupper():
            out.append(upper + ord(ch) - ord("A"))
        elif ch.islower():
            out.append(lower + ord(ch) - ord("a"))
        else:
            out.append(digit + ord(ch) - ord("0"))
    return bytes(out)


def test_finds_8bit_with_case_gap():
    data = b"\x00" * 16 + encode("Hello99", 0x80, 0xA0, 0xC0) + b"\xff" * 5
    hits = relative_search(data, "Hello99", widths=(1,))
    assert [h.offset for h in hits] == [16]
    assert hits[0].bases == {"upper": 0x80, "lower": 0xA0, "digit": 0xC0}
    assert relative_search(data, "Hello99", widths=(1,), case_gap=False) == []
    assert relative_search(data, "Hell?99", widths=(1,))[0].offset == 16


def test_finds_16bit_both_endians():
    codes = [0x100 + i for i in (0, 1, 2)]
    little = b"".join(c.to_bytes(2, "little") for c in codes)
    big = b"".join(c.to_bytes(2, "big") for c in codes)
    data = b"\x55" + little + b"\x00\x00" + big
    hits = relative_search(data, "ABC", widths=(2,))
    assert {(h.offset, h.endian) for h in hits} == {(1, "little"), (9, "big")}


def test_entries_from_hit():
    data = encode("Cat", 0x41, 0x61)
    hit = relative_search(data, "Cat", widths=(1,))[0]
    entries = entries_from_hit(hit)
    texts = {e.bits: e.text for e in entries}
    assert texts["01000001"] == "A" and texts["01111010"] == "z"
    assert len(entries) == 52


def test_bad_query():
    with pytest.raises(ValueError):
        relative_search(b"abc", "a-b")
    with pytest.raises(ValueError):
        relative_search(b"abc", "???")


def test_cancel():
    data = bytes(range(256)) * 300
    calls = []

    def progress(done, total):
        calls.append(done)
        return False

    assert relative_search(data, "AB", widths=(1,), progress=progress) == []
    assert calls
