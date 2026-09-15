from __future__ import annotations

import unicodedata

import pytest

from mapchar.core.bits import format_key
from mapchar.core.errors import TableError
from mapchar.core.table import Stop, TokenKind
from mapchar.project.formats.table_native import (
    HEADER,
    is_native,
    parse_entry,
    parse_native,
    write_native,
)

SAMPLE = """\
@mapchar table 1
# Dragon Warrior, main font
@table main
@charset none
41=A
42=B
0041=あ
%01=x
/FF=[end]
FE=[line]\\n
$F0=[color],u8
!F1=[item] @items:1
!F2=[name] @names:*
41<2>=weighted
"""

ITEMS = f"{HEADER}\n@table items\n01=Herb\n!FF=return\n"


def test_header_detection():
    assert is_native(SAMPLE)
    assert is_native("# c\n\n" + HEADER + "\n")
    assert not is_native("41=A\n")


def test_parse_sample():
    with pytest.raises(TableError, match="duplicate key"):
        parse_native(SAMPLE)
    text = SAMPLE.replace("41<2>=weighted\n", "43<2>=weighted\n")
    main = parse_native(text).table
    assert main.id == "main"
    assert main.entries["01000001"].text == "A"
    assert main.entries["0000000001000001"].text == "あ"
    assert main.entries["01"].text == "x"
    end = main.entries["11111111"]
    assert end.kind is TokenKind.END and end.label == "end"
    assert main.entries["11111110"].text == "[line]\\n"
    color = main.entries["11110000"]
    assert color.kind is TokenKind.CODE and color.operands[0].spec() == "u8"
    item = main.entries["11110001"]
    assert item.kind is TokenKind.SWITCH
    assert item.params[0].table_id == "items" and item.params[0].stop == Stop(count=1)
    assert main.entries["11110010"].params[0].stop.any
    assert main.entries["01000011"].weight == 2
    assert parse_native(ITEMS).table.entries["11111111"].kind is TokenKind.RETURN
    assert main.labels["end"] is end
    assert main.labels["line"] is main.entries["11111110"]


def test_switch_text_forms():
    silent = parse_entry("!F1=@t:1")
    assert silent.kind is TokenKind.SWITCH and silent.text == "" and silent.silent
    assert silent.label is None
    plain = parse_entry("!F2=Name: @t:1 @u:*")
    assert plain.text == "Name:" and [p.spec() for p in plain.params] == [
        "@t:1",
        "@u:*",
    ]
    nl = parse_entry("!F3=\\n @t:*")
    assert nl.text == "\\n" and nl.label is None
    coded = parse_entry("!F4=[item] @t:1")
    assert coded.label == "item"
    from mapchar.project.formats.table_native import format_entry

    for e in (silent, plain, nl, coded):
        assert parse_entry(format_entry(e)) == e


def test_param_forms():
    e = parse_entry("!03=[str] @upper:3+ @raw:2 @bits:1 @t:$FF @u:%101")
    specs = [p.spec() for p in e.params]
    assert specs == ["@upper:3+", "@raw:2", "@bits:1", "@t:$FF", "@u:%101"]


@pytest.mark.parametrize(
    "line",
    [
        "41=[unclosed",
        "41=stray]",
        "$F0=nolabel,1",
        "$F0=[c]",
        "!F1=[s]",
        "!F1=return now",
        "!F1=[s] @t:x",
        "!F1=[unclosed @t:1",
        "$F0=[c],u7",
        "ZZ=A",
    ],
)
def test_bad_entries(line):
    with pytest.raises(ValueError):
        parse_entry(line)


def test_errors_carry_line_numbers():
    with pytest.raises(TableError) as info:
        parse_native(HEADER + "\n41=A\n41=B\n", path="x.tbl")
    assert info.value.line == 3 and "x.tbl:3" in str(info.value)
    with pytest.raises(TableError, match="expected"):
        parse_native("41=A\n")
    with pytest.raises(TableError, match="duplicate label"):
        parse_native(HEADER + "\n$01=[a],1\n$02=[a],1\n")


def test_one_file_holds_one_table():
    with pytest.raises(TableError, match="holds one table") as info:
        parse_native(f"{HEADER}\n@table main\n41=A\n@table items\n01=B\n")
    assert info.value.line == 4
    # Without a @table line the table is named for the file; one may come late.
    assert parse_native(f"{HEADER}\n41=A\n", default_id="font").table.id == "font"
    late = parse_native(f"{HEADER}\n41=A\n@table main\n", default_id="font")
    assert late.table.id == "main" and "01000001" in late.table.entries


def test_roundtrip_through_writer():
    text = SAMPLE.replace("41<2>=weighted\n", "43<2>=weighted\n")
    table = parse_native(text).table
    out = write_native(table)
    assert parse_native(out).table.entries == table.entries
    assert out.startswith(f"{HEADER}\n@table main\n")
    assert "!F1=[item] @items:1" in out
    assert "$F0=[color],u8" in out
    assert "43<2>=weighted" in out
    assert "%01=x" in out


def test_format_key_spells_whole_nibbles_as_hex():
    assert format_key("11111111") == "FF" and format_key("01") == "%01"


def test_a_kana_table_id_round_trips_through_the_writer():
    kanji = parse_native(f"{HEADER}\n@table かんじ\n41=亜\n").table
    main = parse_native(f"{HEADER}\n@table main\n!42=[k] @かんじ:1\n").table
    assert "@table かんじ" in write_native(kanji)
    assert "@かんじ:1" in write_native(main)
    assert parse_native(write_native(kanji)).table.id == "かんじ"


def test_decomposed_entry_text_is_written_back_composed():
    decomposed = unicodedata.normalize("NFD", "が")
    assert len(decomposed) == 2
    table = parse_native(f"{HEADER}\n@table main\n41={decomposed}\n").table
    assert table.entries["01000001"].text == "が"
    assert write_native(table).endswith("41=が\n")


@pytest.mark.parametrize("bad", ["@table かん じ", "@table a b"])
def test_an_id_with_whitespace_is_rejected(bad):
    with pytest.raises(TableError, match="invalid table id"):
        parse_native(HEADER + "\n" + bad + "\n41=A\n")
