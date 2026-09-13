from __future__ import annotations

import pytest

from mapchar.engines.relsearch import (
    HIRAGANA,
    KATAKANA,
    RUNS,
    entries_from_hit,
    relative_search,
)


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
    assert relative_search(data, "Hello99", widths=(1,), case_gap=False).hits == []
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

    assert relative_search(data, "AB", widths=(1,), progress=progress).hits == []
    assert calls


def test_limit_reports_truncation():
    data = b"\x41\x42" * 400
    found = relative_search(data, "AB", widths=(1,), limit=10)
    assert len(found) == 10 and found.truncated
    found = relative_search(data, "AB", widths=(1,), limit=10000)
    assert len(found) == 400 and not found.truncated


def kana_bytes(text: str, base: int, run: str) -> bytes:
    return bytes(base + RUNS[run].index(ch) for ch in text)


def test_finds_kana_in_gojuon_order():
    data = b"\x00" * 4 + kana_bytes("ひめさま", 0x40, HIRAGANA) + b"\xff"
    hits = relative_search(data, "ひめさま", widths=(1,))
    assert [h.offset for h in hits] == [4]
    assert hits[0].bases == {HIRAGANA: 0x40}
    katakana = kana_bytes("アイテム", 0xA0, KATAKANA)
    hit = relative_search(b"\x00" + katakana, "アイテム", widths=(1,))[0]
    assert hit.bases == {KATAKANA: 0xA0}
    # A mixed query pins both runs at once.
    mixed = kana_bytes("かな", 0x40, HIRAGANA) + kana_bytes("カナ", 0xA0, KATAKANA)
    hit = relative_search(mixed, "かなカナ", widths=(1,))[0]
    assert hit.bases == {HIRAGANA: 0x40, KATAKANA: 0xA0}


def test_kana_entries_from_a_hit_are_the_whole_alphabet():
    data = kana_bytes("あいう", 0x20, HIRAGANA)
    hit = relative_search(data, "あいう", widths=(1,))[0]
    entries = entries_from_hit(hit)
    assert len(entries) == len(RUNS[HIRAGANA])
    assert (entries[0].bits, entries[0].text) == ("00100000", "あ")
    assert entries[-1].text == "ん"


def test_a_decomposed_query_is_not_a_kana_of_the_run():
    with pytest.raises(ValueError, match="kana"):
        relative_search(b"\x00" * 8, "が", widths=(1,))
