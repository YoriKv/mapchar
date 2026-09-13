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


def test_strings_per_pointer_allow_interior_end_tokens():
    body = "@table main\n41=A\n/C1=A[line]\\n\n/FF=[end]\n"
    assert roundtrip(body, "AA[line]\nA[end]", ends=2) == bytes.fromhex("41 C1 41 FF")
    with pytest.raises(EncodeError):
        roundtrip(body, "AA[line]\nA[end]")  # one run: the line end is interior
    with pytest.raises(EncodeError):
        roundtrip(body, "AA[end]", ends=2)  # two runs read, one written
    with pytest.raises(EncodeError):
        roundtrip(body, "A[end]A[end]A[end]", ends=2)


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
