"""Unicode across the app: one composed form everywhere, graphemes as glyph
slots, and files that are not UTF-8.

The per-area modules cover their own non-ASCII cases; what is here is what
crosses areas — a table, a translation and a search needle meeting whatever
form each was typed in.
"""

from __future__ import annotations

import unicodedata

from helpers import table_set
from mapchar.core.bits import bytes_to_bits
from mapchar.core.block import Status, StringRecord
from mapchar.core.font import Font, TextBox
from mapchar.core.table import ID_PATTERN, Entry, Table, TokenKind
from mapchar.core.text import char_units, fold, graphemes, nfc, nfd
from mapchar.core.tokens import Token
from mapchar.engines import scriptfind
from mapchar.engines.encode import encode
from mapchar.engines.layout import layout
from mapchar.plugins.base import Stage
from mapchar.plugins.builtins.charsets import UNICODE, CodecCharset
from mapchar.plugins.charsets import apply_charset
from mapchar.project.formats.table_native import HEADER, parse_native, sanitize_id
from mapchar.project.formats.textfile import read_text_any
from mapchar.project.tables import read_table_file

GA = "が"
"""One code point: the composed form mapchar keeps."""
GA_NFD = nfd(GA)
"""Two: the kana and a combining dakuten, which is how abcde writes tables."""


def native(body: str) -> str:
    return HEADER + "\n" + body


# --- one form ---------------------------------------------------------------


def test_table_text_is_composed_however_the_file_spelled_it():
    for body in (f"@table main\n41={GA}\n", f"@table main\n41={GA_NFD}\n"):
        table = parse_native(native(body)).tables[0]
        assert table.entries["01000001"].text == GA
    # Any entry, from any dialect or built by hand.
    assert Entry("01000001", TokenKind.TEXT, GA_NFD).text == GA
    assert Entry("01000001", TokenKind.CODE, nfd("é")).text == "é"


def test_a_translation_is_composed_on_commit():
    rec = StringRecord(0, 0, 8, [])
    rec.translation = GA_NFD
    assert rec.translation == GA
    rec.status = Status.EDITED
    assert rec.status is Status.EDITED


def test_matches_original_ignores_the_form():
    entry = Entry("01000001", TokenKind.TEXT, GA)
    rec = StringRecord(0, 0, 8, [Token("01000001", 0, 8, entry)])
    assert rec.original_text() == GA
    assert rec.matches_original(GA_NFD)
    assert rec.matches_original(GA)
    assert not rec.matches_original("か")


# --- encoding either form ---------------------------------------------------


def test_a_decomposed_table_encodes_composed_text_and_back():
    decomposed_file = table_set(f"@table main\n41={GA_NFD}\n/00=[end]\n", "main")
    composed_file = table_set(f"@table main\n41={GA}\n/00=[end]\n", "main")
    for ts in (decomposed_file, composed_file):
        for text in (GA, GA_NFD):
            assert encode(text + "[end]", ts).data == bytes.fromhex("41 00")


def test_a_table_that_spells_the_dakuten_separately_still_encodes_it():
    # A ROM that draws the mark as its own glyph gives it its own code; the
    # encoder splits the composed kana to reach it.
    dakuten = GA_NFD[1]
    ts = table_set(f"@table main\n41=か\n42={dakuten}\n/00=[end]\n", "main")
    assert encode(GA + "[end]", ts).data == bytes.fromhex("41 42 00")
    assert encode(GA_NFD + "[end]", ts).data == bytes.fromhex("41 42 00")


def test_non_bmp_text_encodes_and_decodes():
    key = "𠀋".encode().hex().upper()
    ts = table_set(f"@table main\n{key}=𠀋\n/00=[end]\n", "main")
    assert encode("𠀋[end]", ts).data == "𠀋".encode() + b"\x00"


# --- ids --------------------------------------------------------------------


def test_a_table_id_may_be_kana_or_kanji():
    assert ID_PATTERN.fullmatch("かんじ")
    assert ID_PATTERN.fullmatch("漢字-2")
    assert not ID_PATTERN.fullmatch("かん じ")
    assert sanitize_id("かん じ") == "かん_じ"
    assert sanitize_id("カタカナ") == "カタカナ"
    # Decomposed on the way in, composed as the id.
    assert Table(nfd("がぎ")).id == "がぎ"
    assert parse_native(native("@table かんじ\n41=亜\n")).table.id == "かんじ"
    main = parse_native(native("@table main\n!42=[k] @かんじ:1\n")).table
    assert main.entries["01000010"].params[0].table_id == "かんじ"


# --- files ------------------------------------------------------------------


def test_read_text_any_names_the_encoding_it_used(tmp_path):
    cases = {
        "plain.txt": (b"41=A\n", "utf-8", "41=A\n"),
        # The mark says which encoding it is and is not part of the text.
        "bom.txt": ("﻿41=A\n".encode(), "utf-8-sig", "41=A\n"),
        "sjis.txt": ("82A9=か\n".encode("cp932"), "cp932", "82A9=か\n"),
        "latin.txt": (b"41=caf\xe9\n", "latin-1", "41=café\n"),
    }
    for name, (data, want_encoding, want_text) in cases.items():
        path = tmp_path / name
        path.write_bytes(data)
        text, encoding = read_text_any(str(path))
        assert (encoding, text) == (want_encoding, want_text), name


def test_read_text_any_composes_what_it_read(tmp_path):
    path = tmp_path / "nfd.txt"
    path.write_text(f"41={GA_NFD}\n", encoding="utf-8")
    text, encoding = read_text_any(str(path))
    assert (text, encoding) == (f"41={GA}\n", "utf-8")


def test_a_shift_jis_table_on_a_kana_path_loads_with_a_notice(tmp_path):
    path = tmp_path / "かんじ.tbl"
    path.write_bytes("@main\n41=A\n82A9=か\n".encode("cp932"))
    tf = read_table_file(str(path))
    assert tf.encoding == "cp932"
    assert [str(n) for n in tf.notices if "cp932" in str(n)]
    assert tf.tables[0].entries[bytes_to_bits(b"\x82\xa9")].text == "か"


# --- searching --------------------------------------------------------------


def test_find_folds_without_losing_the_span():
    # 'İ' folds to two characters: a folded whole string cannot be indexed
    # with the spans of the original.
    text = "İzmir[line]ok"
    assert scriptfind.find(text, "zmir", case=False) == (1, 5)
    assert scriptfind.find(text, "OK", case=False) == (11, 13)
    assert scriptfind.replace(text, "ok", "ne", case=False) == ("İzmir[line]ne", 1)


def test_a_needle_is_composed_before_it_is_matched():
    text = f"{GA}んばれ"
    assert scriptfind.find(text, GA_NFD) == (0, 1)
    assert scriptfind.contains(text, GA_NFD, case=False)
    assert fold("ガ") != fold(GA)  # katakana is not a case of hiragana


# --- glyphs -----------------------------------------------------------------


def test_a_dakuten_kana_is_one_glyph_slot():
    font = Font(None, 8, 8, 16, 0x20, nfd(f"あ{GA}い"))
    assert font.chars == f"あ{GA}い"
    assert font.units == ("あ", GA, "い")
    assert font.glyph_for(GA) == 0x21
    assert font.glyph_for(GA_NFD) == 0x21
    assert font.glyph_for(GA_NFD[1]) is None
    result = layout(GA_NFD + "い", font, TextBox(width=64, height=8, line_height=8))
    assert [p.glyph for p in result.placements] == [0x21, 0x22]


def test_graphemes_and_units():
    assert (len(GA), len(GA_NFD)) == (1, 2)
    assert graphemes(GA_NFD) == [GA_NFD]
    assert graphemes(f"a{GA}") == ["a", GA]
    assert char_units(nfd("ぱぴ")) == ("ぱ", "ぴ")
    assert unicodedata.is_normalized("NFC", nfc(GA_NFD))


# --- charsets ---------------------------------------------------------------


def test_the_unicode_charsets_reach_past_the_bmp(registry):
    astral = CodecCharset("x", "x", "utf-8", ((0x2000B, 0x2000B),))
    assert list(astral.entries()) == [(bytes_to_bits("𠀋".encode()), "𠀋")]
    pair = CodecCharset("x", "x", "utf-16-le", ((0x1F600, 0x1F600),))
    bits, text = next(iter(pair.entries()))
    assert (text, len(bits)) == ("😀", 32)  # a surrogate pair, four bytes
    assert registry.plugin(Stage.CHARSET, "utf-8").ranges == UNICODE


def test_shift_jis_is_cp932_and_the_yen_sign_reaches_5c(registry):
    table = Table("main", "shift-jis")
    apply_charset(table, registry)
    # An NEC/IBM extension row character cp932 has and plain Shift-JIS does not.
    assert table.entries[bytes_to_bits("①".encode("cp932"))].text == "①"
    assert table.entries[bytes_to_bits(b"\x5c")].text == "\\\\"
    assert table.aliases["¥"] == bytes_to_bits(b"\x5c")
    assert table.aliases["‾"] == bytes_to_bits(b"\x7e")
    ts = table_set("@table main\n@charset shift-jis\n/00=[end]\n", "main")
    assert encode("¥100[end]", ts).data == b"\x5c100\x00"
    assert encode("\\\\[end]", ts).data == b"\x5c\x00"


def test_a_long_string_encodes_through_a_charset_table_in_reasonable_time(registry):
    """A charset table is thousands of entries, so the encoder's per-search index
    and its atom walk must stay linear in the text. The bound is loose — the
    quadratic version this guards against took minutes, not seconds."""
    import time

    ts = table_set("@table main\n@charset shift-jis\n/00=[end]\n", "main")
    text = ("こんにちは世界アイウエオ漢字テスト" * 20)[:200]
    assert len(text) == 200
    started = time.monotonic()
    result = encode(text + "[end]", ts)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0, f"encoding 200 characters took {elapsed:.1f}s"
    # Optimal: every character of this text is one double-byte cp932 code.
    assert result.data == text.encode("cp932") + b"\x00"
