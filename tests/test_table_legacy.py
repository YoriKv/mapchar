from __future__ import annotations

import pytest

from mapchar.core.errors import TableError
from mapchar.core.table import EntryKind
from mapchar.project.formats.table_legacy import (
    detect_dialect,
    load_table_text,
    read_abcde,
    read_atlas,
    read_cartographer,
    read_romjuice,
)
from mapchar.project.formats.table_native import write_native


def test_detect():
    assert detect_dialect("@mapchar table 1\n41=A\n") == ("native", True)
    assert detect_dialect("@main\n41=A\n")[0] == "abcde"
    assert detect_dialect("!FF=<[x]>,1\n")[0] == "abcde"
    assert detect_dialect("41=A\n!F0\n")[0] == "romjuice"
    assert detect_dialect("@C1=2,C000\n")[0] == "romjuice"
    assert detect_dialect("$F0=color,1\n")[0] == "cartographer"
    assert detect_dialect("*FE\n41=A\n")[0] == "atlas"
    assert detect_dialect("/<END>\n")[0] == "atlas"
    assert detect_dialect("41=A\n42=B\n") == ("cartographer", False)


def test_romjuice():
    text = (
        "41=A\n041=B\n0041=x\nC012=Kanji\n@C1=2,C000\n$F0=2\n"
        "FF=\\n<end>\\r\\r\n41=dup\nrubbish=x\nignored\n"
    )
    tf = read_romjuice(text, "font.tbl", "font")
    main = tf.tables[0]
    assert main.entries["01000001"].text == "A"  # 041 is also a 1-byte 41: first wins
    assert main.entries["0000000001000001"].text == "x"
    assert main.entries["11111111"].text == "\\n<end>\\n\\n"
    kanji = main.entries["11000001"]
    assert kanji.kind is EntryKind.SWITCH and kanji.params[0].table_id == "kanji_C000"
    assert kanji.params[0].stop.count == 2
    linked = main.entries["11110000"]
    assert linked.kind is EntryKind.CODE and linked.operands[0].bits == 16
    kt = {t.id: t for t in tf.tables}["kanji_C000"]
    assert kt.entries["00010010"].text == "Kanji"
    assert any("duplicate key" in n.message for n in tf.notices)
    assert any("without a hex key" in n.message for n in tf.notices)


def test_romjuice_swap():
    tf = read_romjuice("41=A\n!F0\n", swap_table="kana")
    sw = tf.tables[0].entries["11110000"]
    assert sw.kind is EntryKind.SWITCH and sw.params[0].stop.any
    tf = read_romjuice("41=A\n!F0\n")
    assert "11110000" not in tf.tables[0].entries


def test_cartographer():
    tf = read_cartographer("41=A\n/FF=<END>\n$F0=Item Name,2\n2020=[heart]\n")
    t = tf.tables[0]
    assert (
        t.entries["11111111"].kind is EntryKind.END
        and t.entries["11111111"].text == "<END>"
    )
    code = t.entries["11110000"]
    assert code.text == "Item_Name" and code.operands[0].bits == 16
    assert t.entries["0010000000100000"].label == "heart"
    with pytest.raises(TableError):
        read_cartographer("041=A\n")
    with pytest.raises(TableError):
        read_cartographer("(bookmark)\n")


def test_atlas():
    tf = read_atlas("41=A\n*FE\n*FD=x\n/FF=[END]\n/<END>\n!DE\n$F0=y\n(book)\n// c\n")
    t = tf.tables[0]
    assert t.entries["11111110"].text == "\\n"
    assert t.entries["11111101"].text == "x\\n"
    assert t.entries["11111111"].label == "END"
    assert tf.end_marker == "<END>"
    assert len(t.entries) == 4


ABCDE = """\
# comment
@main
01=foo
%0011=bits
/FF=[end]
!AB=<[Item Name:]>,<@ItemNames>:1,<binary>:2,3
!AC=,0
!AD=<[x]>,-1
!AE=<[p]>,<@ItemNames>:$AB
/!03=,<@upper>:3+
03<2>=[Batman]
@ItemNames
01=[Potion]
@upper
41=A
"""


def test_abcde():
    with pytest.raises(TableError, match="duplicate"):
        read_abcde(ABCDE)
    text = ABCDE.replace("03<2>=[Batman]", "04<2>=[Batman]")
    tf = read_abcde(text)
    tables = {t.id: t for t in tf.tables}
    main = tables["main"]
    assert main.entries["0011"].text == "bits"
    sw = main.entries["10101011"]
    assert sw.text == "[Item_Name:]" and sw.label == "Item_Name:"
    assert [p.spec() for p in sw.params] == ["@ItemNames:1", "@bits:2", "@raw:3"]
    assert (
        main.entries["10101100"].text == ""
        and main.entries["10101100"].silent
        and main.entries["10101100"].params[0].stop.any
    )
    ret = main.entries["10101101"]
    assert ret.kind is EntryKind.SWITCH and ret.text == "[x]"
    assert [p.spec() for p in ret.params] == ["return"]
    assert main.entries["10101110"].params[0].stop.fallback == "10101011"
    pascal = main.entries["00000011"]
    assert pascal.kind is EntryKind.SWITCH and pascal.params[0].shared
    assert main.entries["00000100"].weight == 2
    assert tables["upper"].entries["01000001"].text == "A"
    out = write_native(tf.tables)
    assert "!AB=[Item_Name:] @ItemNames:1 @bits:2 @raw:3" in out
    assert "!AD=[x] return" in out and "!AC= @raw:*" in out


def test_load_table_text_dispatch(tmp_path):
    tf = load_table_text("41=A\n", "/x/y/Main Font.tbl")
    assert tf.dialect == "cartographer" and tf.tables[0].id == "Main_Font"
    tf = load_table_text("41=A\n", dialect="romjuice")
    assert tf.dialect == "romjuice"
    with pytest.raises(TableError):
        load_table_text("41=A\n", dialect="nope")
