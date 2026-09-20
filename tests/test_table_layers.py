"""What a table takes from others and says about layout: ``@include``, a switch
frame that falls through, and the effects an entry declares."""

from __future__ import annotations

from copy import deepcopy

import pytest

from helpers import native_files, table_set, tables_from
from mapchar.core.bits import Bits
from mapchar.core.errors import EncodeError, TableError
from mapchar.core.font import CodeEffect, Effect, TextBox
from mapchar.core.table import TableSet, inherited, resolve
from mapchar.core.tokens import render
from mapchar.engines.decode import DecodeRules, decode
from mapchar.engines.encode import encode
from mapchar.engines.layout import char_layout, code_effects, with_code_effects, wrap
from mapchar.project.formats.table_native import (
    HEADER,
    format_entry,
    parse_entry,
    parse_native,
    write_native,
)
from mapchar.project.tables import capture_overlay, fold_overlay
from mapchar.project.workspace import Entry, EntryKind

# -- @include ----------------------------------------------------------------

BASE = """\
@table base
41=A
42=B
43=C
/00=[end]
FE=[line]
F0=[wait]
"""

BATTLE = """\
@table battle
@include base
# the battle font has its own B
42=b
43=
F0=[pause]
F1=[name]
"""


def decoded(tables: TableSet, data: bytes) -> str:
    return render(decode(Bits(data), tables, 0, DecodeRules()).tokens)


def test_an_include_lays_the_other_table_under_the_file_s_entries():
    tables = tables_from(BASE + BATTLE)
    ts = TableSet.build(tables["battle"], tables)
    # 42 is overridden, 43 removed, F0 relabelled, 41 and the end inherited.
    assert decoded(ts, bytes.fromhex("41 42 43 F0 F1 FE 00")) == (
        "Ab[$43][pause][name][line]\n[end]"
    )
    assert encode("Ab[pause][end]", ts).data == bytes.fromhex("41 42 F0 00")
    # The table itself still holds only what its file says.
    assert sorted(tables["battle"].labels) == ["name", "pause"]
    given = inherited(tables["battle"], tables)
    assert given["01000001"][1] == "base" and "11110001" not in given


def test_an_include_is_written_back_and_says_only_its_own_entries():
    table = parse_native(native_files(BASE + BATTLE)[1]).table
    assert table.includes == ("base",)
    text = write_native(table)
    assert "@include base\n" in text and "41=A" not in text
    assert "43=\n" in text  # the removal of an inherited key survives
    assert parse_native(text).table.entries == table.entries


def test_includes_resolve_in_order_and_through_each_other():
    body = (
        BASE
        + "@table mid\n@include base\n41=a\n"
        + "@table other\n41=α\n44=D\n"
        + "@table top\n@include mid\n@include other\n45=E\n"
    )
    tables = tables_from(body)
    ts = TableSet.build(tables["top"], tables)
    assert decoded(ts, bytes.fromhex("41 42 44 45 00")) == "αBDE[end]"
    given = inherited(tables["top"], tables)
    assert given["01000010"][1] == "base"  # where it is own, not the include named
    assert given["01000001"][1] == "other"


def test_a_resolution_is_kept_until_an_included_table_changes():
    tables = tables_from(BASE + BATTLE)
    first = resolve(tables["battle"], tables)
    assert resolve(tables["battle"], tables) is first
    tables["base"].add(parse_entry("44=D"))
    again = resolve(tables["battle"], tables)
    assert again is not first and again.entries["01000100"].text == "D"
    # A copy of the table leaves the resolved one behind.
    assert "resolved" not in deepcopy(tables["battle"])._cache


def test_a_copy_shares_the_entries_it_holds_and_changes_apart_from_the_table():
    """An entry is frozen and is never edited in place, so a snapshot of a
    table on a charset copies the lookups over its entries and not the tens of
    thousands of entries themselves."""
    table = tables_from(BASE)["base"]
    copy = deepcopy(table)
    assert copy.entries["01000001"] is table.entries["01000001"]
    copy.add(parse_entry("44=D"))
    table.remove("11111110")  # the [line] code
    assert "01000100" not in table.entries and "line" not in table.labels
    assert "01000100" in copy.entries and copy.labels["line"].bits == "11111110"


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("@table a\n@include nowhere\n", "includes unknown table 'nowhere'"),
        ("@table a\n@include b\n@table b\n@include a\n", "include each other"),
        ("@table a\n01=[x]\n@table b\n@include a\n02=[x]\n", "duplicate label [x]"),
    ],
)
def test_a_bad_include_fails_when_the_set_is_built(body, message):
    tables = tables_from(body)
    with pytest.raises(TableError, match=message.replace("[", r"\[")):
        TableSet.build(tables["a"] if "nowhere" in body else tables["b"], tables)


def test_an_override_at_the_same_key_takes_the_inherited_label():
    tables = tables_from(BASE + "@table t\n@include base\nFE=[newline]\n")
    ts = TableSet.build(tables["t"], tables)
    assert "line" not in ts.start.labels and "newline" in ts.start.labels


def test_a_repeated_or_malformed_include_is_a_load_error():
    with pytest.raises(TableError, match="repeated"):
        parse_native(f"{HEADER}\n@table t\n@include a\n@include a\n")
    with pytest.raises(TableError, match="invalid table id"):
        parse_native(f"{HEADER}\n@table t\n@include a b\n")


def test_the_project_carries_includes_set_in_the_app():
    table = parse_native(native_files(BATTLE)[0]).table
    entry = Entry(EntryKind.TABLE, "battle.tbl", "battle.tbl", table=table)
    entry.file_table = deepcopy(table)
    capture_overlay(entry)
    assert entry.table_includes is None
    table.includes = ("base", "extra")
    capture_overlay(entry)
    assert entry.table_includes == ("base", "extra") and not entry.table_overlay
    fold_overlay(entry)
    assert entry.table_includes is None


# -- falling through ---------------------------------------------------------

SCRIPT = """\
@table script
41=A
42=B
/FFFF=[end]
0100=[line]
$0400=[wait],u8
!0B00=[saturn] @saturn:*|
"""

SATURN = """\
@table saturn
41=あ
42=い
"""


def test_a_frame_that_falls_through_reads_what_its_table_lacks_beneath():
    ts = table_set(SCRIPT + SATURN, "script")
    data = bytes.fromhex("41 0B00 41 0100 42 0400 07 43 FFFF")
    assert decoded(ts, data) == "A[saturn]あ[line]\nい[wait $07][$43][end]"
    # Without the mark the codes are the frame's unmatched bytes.
    plain = table_set(SCRIPT.replace("*|", "*") + SATURN, "script")
    assert "[$01]" in decoded(plain, data)


def test_encoding_inside_a_frame_uses_the_tables_beneath():
    ts = table_set(SCRIPT + SATURN, "script")
    text = "A[saturn]あ[line]い[wait $07][end]"
    data = encode(text, ts).data
    assert data == bytes.fromhex("41 0B00 41 0100 42 0400 07 FFFF")
    assert decoded(ts, data).replace("\n", "") == text


def test_a_key_the_frame_matches_is_never_taken_from_beneath():
    # "B" is only beneath, at 42 -- which the frame's own table reads as い, so
    # it cannot be written inside the frame at all.
    ts = table_set(SCRIPT + SATURN, "script")
    with pytest.raises(EncodeError, match="cannot follow the text before it"):
        encode("[saturn]B[end]", ts)
    # A longer key of the frame's table forbids what would complete it.
    ts = table_set(SCRIPT + "@table saturn\n4100=あ\n", "script")
    data = encode("[saturn]A[end]", ts).data
    assert decoded(ts, data).replace("\n", "") == "[saturn]A[end]"


def test_a_return_beneath_leaves_the_frame_of_its_own_table():
    body = (
        "@table main\n41=A\n/00=[end]\n!F0=[a] @mid:*\n"
        "@table mid\n42=B\n!FF=return\n!F1=[b] @inner:*|\n"
        "@table inner\n43=C\n"
    )
    ts = table_set(body, "main")
    data = bytes.fromhex("F0 F1 43 42 FF 41 00")
    # FF is mid's return: found from inner's frame, it leaves mid's frame, so 41
    # is read in main again.
    assert decoded(ts, data) == "[a][b]CBA[end]"
    assert encode("[a][b]CBA[end]", ts).data == data


def test_falling_through_is_written_back():
    entry = parse_entry("!0B00=[saturn] @saturn:3+|")
    assert entry.params[0].through and entry.params[0].shared
    assert format_entry(entry) == "!0B00=[saturn] @saturn:3+|"


# -- effects -----------------------------------------------------------------

EFFECTS = """\
@table main
41=A
/00=[end]
01{newline}=[br]
02{page}=[next]
03{pause}=[wait]
FE=[line]
"""


def test_an_effect_is_read_and_written_with_its_key():
    entry = parse_entry("$04FF<0>{pause}=[FF04],u16")
    assert entry.effect is Effect.PAUSE and entry.weight == 0
    assert format_entry(entry) == "$04FF<0>{pause}=[FF04],u16"
    with pytest.raises(ValueError, match="unknown effect"):
        parse_entry("41{space}=A")
    with pytest.raises(ValueError, match="takes no effect"):
        parse_entry("!FF{page}=return")


def test_newline_and_page_break_the_line_and_a_pause_does_not():
    ts = table_set(EFFECTS, "main")
    result = decode(Bits(bytes.fromhex("41 01 41 02 41 03 41 FE 41 00")), ts, 0)
    assert render(result.tokens) == "A[br]\nA[next]\nA[wait]A[line]\nA[end]"
    # A page is not a line: only line codes count towards a Lines string.
    lines = decode(
        Bits(bytes.fromhex("41 02 41 01 41 01")), ts, 0, DecodeRules(max_lines=2)
    )
    assert render(lines.tokens) == "A[next]\nA[br]\nA[br]\n"


def test_layout_takes_the_table_s_effects_under_the_box_s():
    ts = table_set(EFFECTS, "main")
    effects = code_effects(ts, "line")
    assert effects == {
        "br": CodeEffect(Effect.NEWLINE),
        "next": CodeEffect(Effect.PAGE),
        "wait": CodeEffect(Effect.PAUSE),
        "line": CodeEffect(Effect.NEWLINE),
    }
    box = TextBox(chars_per_line=4, lines_per_page=2)
    merged = with_code_effects(box, effects)
    counted = char_layout("AA[br]AA[br]AA", merged)
    assert counted.overflow_lines
    assert not char_layout("AA[br]AA[next]AA[wait]A", merged).overflows
    # The box overrides the table, none included.
    quiet = with_code_effects(
        TextBox(chars_per_line=4, effects={"next": CodeEffect()}), effects
    )
    assert char_layout("AA[next]AA[br]AA", quiet).lines == 2
    # Wrapping restarts after a page.
    text, overflow = wrap("AAA AAA[next]AAA AAA", None, merged, "br", "next")
    assert text == "AAA[br]AAA[next]AAA[br]AAA" and not overflow
