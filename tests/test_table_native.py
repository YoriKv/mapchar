from __future__ import annotations

import pytest

from helpers import tables_from
from mapchar.core.errors import TableError
from mapchar.core.table import EntryKind, Stop
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
@table items
01=Herb
!FF=return
@table names
02=Erdrick
"""


def test_header_detection():
    assert is_native(SAMPLE)
    assert is_native("# c\n\n" + HEADER + "\n")
    assert not is_native("41=A\n")


def test_parse_sample():
    with pytest.raises(TableError, match="duplicate key"):
        parse_native(SAMPLE)
    text = SAMPLE.replace("41<2>=weighted\n", "43<2>=weighted\n")
    tables = {t.id: t for t in parse_native(text).tables}
    main = tables["main"]
    assert set(tables) == {"main", "items", "names"}
    assert main.entries["01000001"].text == "A"
    assert main.entries["0000000001000001"].text == "あ"
    assert main.entries["01"].text == "x"
    end = main.entries["11111111"]
    assert end.kind is EntryKind.END and end.label == "end"
    assert main.entries["11111110"].text == "[line]\\n"
    color = main.entries["11110000"]
    assert color.kind is EntryKind.CODE and color.operands[0].spec() == "u8"
    item = main.entries["11110001"]
    assert item.kind is EntryKind.SWITCH
    assert item.params[0].table_id == "items" and item.params[0].stop == Stop(count=1)
    assert main.entries["11110010"].params[0].stop.any
    assert main.entries["01000011"].weight == 2
    assert tables["items"].entries["11111111"].kind is EntryKind.RETURN
    assert main.labels["end"] is end
    assert main.labels["line"] is main.entries["11111110"]


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
        "!F1=@t:1",
        "!F1=[s] @t:x",
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


def test_roundtrip_through_writer():
    text = SAMPLE.replace("41<2>=weighted\n", "43<2>=weighted\n")
    tables = list(tables_from(text.split("\n", 1)[1]).values())
    out = write_native(tables)
    again = {t.id: t for t in parse_native(out).tables}
    for tid, table in again.items():
        assert table.entries == tables_from(text.split("\n", 1)[1])[tid].entries
    assert "!F1=[item] @items:1" in out
    assert "$F0=[color],u8" in out
    assert "43<2>=weighted" in out
    assert "%01=x" in out
