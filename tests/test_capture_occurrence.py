"""Finding typed text in a replay's evidence (:mod:`mapchar.capture.occurrence`)
on synthetic events: text written as tile-and-attribute cells, 16-bit codes
read a byte at a time, text stored backwards, voiced kana with a mark code,
too many places, and what a failed search says."""

from __future__ import annotations

import unicodedata
from dataclasses import replace

from capture_fake import CONSOLE, encode
from mapchar.capture import chains, evidence, occurrence
from mapchar.capture.occurrence import Occurrence
from mapchar.capture.protocol import drive

TEXT = "Welcome to the Island of Tests"
BUFFER = 0x7E2000
WRITER, READER = 0x9010, 0x9000


def ev_of(tmp_path, events):
    return evidence.Evidence.of(str(tmp_path), CONSOLE, events)


def writes(codes, at=BUFFER, pc=WRITER, frame=3):
    return [("W", at + k, c, frame, pc) for k, c in enumerate(codes)]


def test_text_written_as_tile_and_attribute_cells_is_found(tmp_path):
    events = []
    for k, c in enumerate(encode(TEXT)):
        events.append(("W", BUFFER + 2 * k, c, 3, WRITER))
        events.append(("W", BUFFER + 2 * k + 1, 0x20, 3, WRITER))
    ram = bytearray(0x20000)
    for _, a, v, _, _ in events:
        ram[a & 0x1FFFF] = v
    (tmp_path / f"{evidence.EVIDENCE}.snesWorkRam").write_bytes(bytes(ram))
    occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    # Not "before": the text was written during the replay, a cell a code.
    assert bad is None
    assert occ.kind == "ram" and occ.pc == WRITER and occ.stride == 2
    assert occ.events == list(range(0, 2 * len(TEXT), 2))
    assert occ.letters[0] == (0, "W")
    assert occ.decoder[encode("W")[0]] == "W"


def reads(values, at=0x4000, pc=READER):
    return [("E", at + k, v, 3, pc) for k, v in enumerate(values)]


def test_sixteen_bit_codes_read_a_byte_at_a_time_are_paired(tmp_path):
    codes = [0x100 + c for c in encode(TEXT)]
    for endian in ("little", "big"):
        data = b"".join(c.to_bytes(2, endian) for c in codes)
        events = [("E", 0x800, 0, 1, 0x8100)] + reads(data)
        occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
        assert bad is None, bad
        assert (occ.kind, occ.unit, occ.endian) == ("rom", 2, endian)
        assert occ.letters[0] == (1, "W")  # the first byte's read
        assert occ.decoder[0x100 + encode("W")[0]] == "W"
        assert 2 in occ.events  # both bytes of each letter's code


def test_text_stored_backwards_is_found_last(tmp_path):
    data = encode(TEXT)[::-1]
    events = reads(data)
    occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    assert bad is None
    assert occ.reverse and occ.kind == "rom"
    first = dict((ch, i) for i, ch in reversed(occ.letters))
    assert events[first["W"]][1] == 0x4000 + len(data) - 1


def test_voiced_kana_written_with_a_mark_code(tmp_path):
    text = "ぶどうだ ですが"
    kana = chains.GOJUON["hiragana"]
    codes = []
    for ch in text:
        if ch == " ":
            codes.append(0x70)
            continue
        d = unicodedata.normalize("NFD", ch)
        codes.append(0x10 + kana.index(d[0]))
        if len(d) == 2:
            codes.append(0x5E)
    occ, bad = drive(occurrence.find(ev_of(tmp_path, writes(codes)), text))
    assert bad is None
    assert occ.decoder[0x5E] == "゙" and occ.decoder[0x10] == "あ"
    assert occ.order is chains.GOJUON


def test_a_word_typed_twice_and_matched_once_is_named(tmp_path):
    events = writes(encode("Hello there Hello"))
    _, bad = drive(occurrence.find(ev_of(tmp_path, events), "Hello there Hello Hello"))
    assert bad.kind == "no-match"
    assert bad.words == ["Hello", "there", "Hello", "Hello"]
    assert bad.matched == ["Hello", "there", "Hello"]
    assert bad.message.startswith("These words do not fit with the rest: Hello.")


def test_text_in_too_many_places_is_said(tmp_path):
    events = []
    for n in range(occurrence.TOO_MANY + 1):
        events += writes(encode(TEXT), at=BUFFER + 0x100 * n, pc=0x9100 + n)
    _, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    assert bad.kind == "too-many"
    # One text written by two routines is one place.
    events = writes(encode(TEXT)) + writes(encode(TEXT), pc=WRITER + 4, frame=4)
    occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    assert bad is None and occ.pc == WRITER


def test_places_count_first_letters_addresses(tmp_path):
    events = writes(encode(TEXT)) + writes(encode(TEXT), at=BUFFER + 0x100)
    ev = ev_of(tmp_path, events)

    def occ(first):
        return Occurrence("ram", "", None, [first], [(first, "W")], {}, {})

    n = len(TEXT)
    assert occurrence._places(ev, [occ(0), occ(0), occ(n)]) == 2
    assert occurrence._places(ev, []) == 0


def test_check_text_counts_letters_once():
    assert occurrence.check_text("Wel").kind == "too-short"
    assert occurrence.check_text("…!?").kind == "no-letters"
    assert occurrence.check_text("がっこう") is None


def test_a_reader_comes_before_text_found_only_every_other_write(tmp_path):
    # Yoshi's Island: the SuperFX writes each character twice to one cell on
    # its way to VRAM, while its reader reads the string from the ROM.
    codes = encode(TEXT)
    events = []
    for k, c in enumerate(codes):
        events.append(("E", 0x4000 + k, c, 3, READER))
        events += [("W", 0x70003E, c, 3, 0x9E9CA)] * 2
    occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    assert bad is None and occ.kind == "rom" and occ.pc == READER
    # With no reader, the cell is the place.
    writes_only = [e for e in events if e[0] == "W"]
    occ, bad = drive(occurrence.find(ev_of(tmp_path, writes_only), TEXT))
    assert bad is None and (occ.kind, occ.stride) == ("ram", 2)


def test_a_variable_written_a_character_at_a_time_comes_first(tmp_path):
    # EarthBound and Dragon Warrior II: the first place a decoder puts each
    # character is one cell, and nearest the ROM.
    codes = encode(TEXT)
    events = []
    for k, c in enumerate(codes):
        events += [("E", 0x4000 + k, c, 3, READER), ("W", 0x5E76, c, 3, 0xC44EE8)]
    events += writes(codes, frame=4)
    occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    assert bad is None and (occ.kind, occ.pc) == ("ram", 0xC44EE8)


def test_text_in_a_save_ram_before_the_replay_is_found(tmp_path):
    save = bytearray(0x2000)
    save[0x100 : 0x100 + len(TEXT)] = encode(TEXT)
    (tmp_path / f"{evidence.EVIDENCE}.snesSaveRam").write_bytes(bytes(save))
    console = replace(CONSOLE, extra_rams=("snesSaveRam",))
    ev = evidence.Evidence.of(str(tmp_path), console, [("E", 0x800, 0, 1, 0x8100)])
    _, bad = drive(occurrence.find(ev, TEXT))
    assert bad.kind == "before" and bad.where == "snesSaveRam $100"


def test_wide_codes_are_traced_from_their_reader(tmp_path):
    # Mother 3: a bus as wide as the access logs a 16-bit code as one event,
    # read from the ROM and written to a RAM buffer with a palette nibble.
    codes = [0x100 + c for c in encode(TEXT)]
    buffer = [
        ("W", 0x201D57C + 2 * k, 0xF000 | c, 3, 0x8049464) for k, c in enumerate(codes)
    ]
    events = []
    for k, c in enumerate(codes):
        events += [("E", 0x1BC330A + 2 * k, c, 3, READER), buffer[k]]
    occ, bad = drive(occurrence.find(ev_of(tmp_path, events), TEXT))
    assert bad is None and (occ.kind, occ.unit, occ.pc) == ("rom", 2, READER)
    assert occ.decoder[0x100 + encode("W")[0]] == "W"
    # A dictionary's reader, its letters read out of order, is no string's.
    shuffled = [e for e in events if e[0] == "W"]
    for k, c in enumerate(codes):
        shuffled.append(("E", 0x1BC330A + (7 * k) % 64 * 2, c, 3, READER))
    occ, bad = drive(occurrence.find(ev_of(tmp_path, shuffled), TEXT))
    assert bad is None and (occ.kind, occ.unit) == ("ram", 2)
    # Only the buffer holds it: the buffer, a code two bytes.
    occ, bad = drive(occurrence.find(ev_of(tmp_path, buffer), TEXT))
    assert bad is None and (occ.kind, occ.unit) == ("ram", 2)
