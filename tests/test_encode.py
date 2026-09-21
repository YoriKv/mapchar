from __future__ import annotations

import pytest

from helpers import table_set
from mapchar.core.bits import Bits
from mapchar.core.errors import EncodeError
from mapchar.core.tokens import render
from mapchar.engines.decode import DecodeRules, decode
from mapchar.engines.encode import encode


def roundtrip(body: str, text: str, start="main", **kw) -> bytes:
    ts = table_set(body, start)
    r = encode(text, ts, **kw)
    return r.data


def test_simple_and_optimal():
    data = roundtrip(
        "@table main\n01=a\n02=b\n03=c\n040506=ab\n", "abc", end_terminated=False
    )
    assert data == bytes.fromhex("01 02 03")
    data = roundtrip(
        "@table main\n01=a\n02=b\n03=c\n0405=ab\n", "abc", end_terminated=False
    )
    assert data in (bytes.fromhex("01 02 03"), bytes.fromhex("04 05 03"))  # equal cost
    data = roundtrip(
        "@table main\n01=a\n02=b\n03=c\n04=ab\n", "abc", end_terminated=False
    )
    assert data == bytes.fromhex("04 03")


def test_longest_prefix_safety():
    # AB would decode as X, so A then B must not be encoded as 01 02.
    with pytest.raises(EncodeError):
        roundtrip("@table main\n01=A\n02=B\n0102=X\n", "AB", end_terminated=False)
    data = roundtrip(
        "@table main\n01=A\n02=B\n0102=X\n03=B\n", "AB", end_terminated=False
    )
    assert data == bytes.fromhex("01 03")


def test_end_token_and_codes():
    body = "@table main\n41=A\n/FF=[end]\n$F0=[color],u8\nFE=[line]\\n\n"
    data = roundtrip(body, "A[color $03]A[line]\nA[end]")
    assert data == bytes.fromhex("41 F0 03 41 FE 41 FF")
    with pytest.raises(EncodeError):
        roundtrip(body, "A[end]A")  # an end token in the middle cannot round-trip
    assert roundtrip(body, "A[end]A", end_terminated=False) == bytes.fromhex("41 FF 41")
    with pytest.raises(EncodeError):
        roundtrip(body, "A[bogus]")
    with pytest.raises(EncodeError):
        roundtrip(body, "A[color]")


def test_an_end_token_inside_the_text_is_refused():
    body = "@table main\n41=A\n/C1=A[line]\\n\n/FF=[end]\n"
    assert roundtrip(body, "AA[line]\n") == bytes.fromhex("41 C1")
    with pytest.raises(EncodeError):
        roundtrip(body, "AA[line]\nA[end]")  # the line end is interior
    with pytest.raises(EncodeError):
        roundtrip(body, "A[end]A[end]")
    assert roundtrip(body, "A[end]A", end_terminated=False) == bytes.fromhex("41 FF 41")


def test_switch_count_and_return():
    body = (
        "@table main\n01=foo\n02=bar\n!AB=[item] @items:1\n"
        "!AC=[names] @names:*\n/FF=[end]\n"
        "@table items\n01=[Potion]\n02=[Sword]\n"
        "@table names\n01=x\n02=y\n!FE=return\n"
    )
    data = roundtrip(body, "[item][Sword]foo[names]xy[end]")
    assert data == bytes.fromhex("AB 02 01 AC 01 02 FE FF")
    data = roundtrip(body, "[item][Sword]foo[names]xy", end_terminated=False)
    assert data == bytes.fromhex("AB 02 01 AC 01 02")


def test_fallback_bits_always_emitted():
    body = (
        "@table main\n01=foo\n!AB=[page] @items:$CC\n@table items\n"
        "01=[Potion]\n02=[Sword]\n\n"
    )
    data = roundtrip(body, "[page][Potion][Sword]foo", end_terminated=False)
    assert data == bytes.fromhex("AB 01 02 CC 01")
    data = roundtrip(body, "[page][Potion]", end_terminated=False)
    assert data == bytes.fromhex("AB 01 CC")


def test_raw_bytes_and_shared_counts():
    body = (
        "@table main\n41=A\n!F1=[w] @raw:2\n03<2>=[Batman]\n"
        "!F2=[two] @sub:2\n@table sub\n01=x\n!05=[in] @main:1+\n\n"
    )
    assert roundtrip(body, "[w][$01][$02]A", end_terminated=False) == bytes.fromhex(
        "F1 01 02 41"
    )
    # An unmatched byte in the main table: 99 matches nothing.
    assert roundtrip(body, "A[$99]A", end_terminated=False) == bytes.fromhex("41 99 41")
    with pytest.raises(EncodeError):
        roundtrip(body, "[$41]", end_terminated=False)
    assert roundtrip(body, "[two]xx", end_terminated=False) == bytes.fromhex("F2 01 01")
    assert roundtrip(body, "[two][in]A", end_terminated=False) == bytes.fromhex(
        "F2 05 41"
    )


def test_bit_entries_pack():
    body = "@table main\n%11=a\n%0=b\n"
    r = encode("aab", table_set(body, "main"), end_terminated=False)
    assert r.bits == "11110" and r.data == bytes.fromhex("F0")


def test_every_decoded_string_re_encodes():
    body = (
        "@table main\n41=A\n42=B\n4142=AB\n$F0=[c],u16\n/00=[end]\n"
        "!F1=[s] @items:1\n@table items\n01=[Herb]\n\n"
    )
    ts = table_set(body, "main")
    for hexdata in ("41 42 00", "41 42 41 00", "F0 34 12 F1 01 42 00", "41 F1 01 00"):
        data = bytes.fromhex(hexdata)
        tokens = decode(Bits(data), ts, 0, DecodeRules()).tokens
        text = render(tokens)
        assert encode(text, ts).data == data, hexdata


def test_error_context():
    with pytest.raises(EncodeError) as info:
        roundtrip("@table main\n41=A\n", "AAZAA", end_terminated=False)
    assert info.value.position == 2 and "Z" in info.value.context


def test_labelled_return_encodes():
    body = (
        "@table main\n41=A\n!F0=[sub] @names:*\n/00=[end]\n@table names\n01=x\n"
        "!FE=[pal] @raw:2 return\n!FF=[back] return\n"
    )
    packed = roundtrip(body, "[sub]x[pal][$AA][$BB]A[end]")
    assert packed == bytes.fromhex("F0 01 FE AA BB 41 00")
    assert roundtrip(body, "[sub]x[back]A[end]") == bytes.fromhex("F0 01 FF 41 00")


def test_a_count_read_from_the_data_is_written_back():
    body = (
        "@table main\n01=a\n02=b\n!F0=[n] @raw:u8\n!F1=[items] @items:u8+\n"
        "/FF=[end]\n@table items\n01=[Potion]\n02=[Sword]\n"
    )
    # 01 and 02 are entries, so only a count of two puts them in the frame.
    assert roundtrip(body, "a[n][$01][$02]b[end]") == bytes.fromhex(
        "01 F0 02 01 02 02 FF"
    )
    assert roundtrip(body, "[items][Sword][Potion][Sword]a[end]") == bytes.fromhex(
        "F1 03 02 01 02 01 FF"
    )
    # A frame with nothing in it still writes its count.
    assert roundtrip(body, "[n]a[end]") == bytes.fromhex("F0 00 01 FF")
    ts = table_set(body, "main")
    for text in ("a[n][$01][$02]b[end]", "[items][Sword][Potion]a[end]", "[n][end]"):
        data = encode(text, ts).data
        assert render(decode(Bits(data), ts, 0).tokens) == text


def test_the_table_keeps_the_index_until_it_changes():
    # Building the index walks every entry, so it is built once per table and
    # not once per string; a change to the table drops it.
    ts = table_set("@table main\n41=A\n42=B\n/00=[end]\n", "main")
    encode("A[end]", ts)
    index = ts.start._cache["encode_index"]
    encode("B[end]", ts)
    assert ts.start._cache["encode_index"] is index
    ts.start.remove("01000010")  # B, keyed by its bits
    encode("A[end]", ts)
    assert ts.start._cache["encode_index"] is not index
    with pytest.raises(EncodeError):
        encode("B[end]", ts)


# --- why an encode failed ----------------------------------------------------

WHY = (
    "@table main\n41=A\n42=B\n45=e\n/00=[end]\n$50=[pause],u8\n"
    "!FD=[kana] @kata:*\n@table kata\n41=X\n!FE=return\n"
)
"""Letters, a code with an operand and a named switch into a second table."""


def why(text: str, body: str = WHY, start: str = "main", **kw) -> str:
    ts = table_set(body, start)
    with pytest.raises(EncodeError) as caught:
        encode(text, ts, **kw)
    return str(caught.value)


def test_a_character_no_table_has_is_named_with_its_code_point():
    """A refusal a translator can act on names the character, not a position."""
    assert why("AZ[end]") == "no table has an entry for 'Z' (U+005A)"
    # A combining mark is shown on the character it joins: the table has an
    # entry for e and none for the acute, and naming the acute alone would
    # send the translator looking for a character nobody typed.
    assert why("Aé[end]") == "no table has an entry for 'é' (U+00E9)"


def test_a_character_in_another_table_says_which_and_how_to_reach_it():
    assert why("AX[end]") == (
        "'X' (U+0058) is in table @kata; the text is read in table @main "
        "there, so write [kana] first"
    )
    # Read in the table that has it, it encodes.
    assert encode("A[kana]X[end]", table_set(WHY, "main")).data == bytes.fromhex(
        "41 FD 41 FE 00"
    )


def test_a_code_says_whether_it_is_unknown_or_its_operands_are():
    assert why("A[nope][end]") == "no table has a code [nope]"
    assert why("A[pause][end]") == "[pause] needs more operands"
    assert why("A[pause 1 2][end]") == "[pause] has too many operands"
    # An operand too big for its spec is a refusal, not an OverflowError.
    assert why("A[pause 999][end]") == (
        "[pause] takes operands u8, which 999 does not fit"
    )


def test_a_long_text_says_where_it_failed_as_well():
    """The character alone places a refusal in a short string; in a long one
    the text around it is what finds the place."""
    assert why("A" * 20 + "Z" + "A" * 12 + "[end]").endswith(
        ' — near "AAAAAAAAAAZAAAAAAAAA"'
    )
