from __future__ import annotations

import pytest

from mapchar.core.block import (
    EndToken,
    FixedLength,
    NextPointer,
    PointerTableSource,
    RangeSource,
)
from mapchar.pipeline.exchange.cartographer import CommandFileError, parse_command_file

RAW_BLOCK = """\
#GAME NAME:      Synthetic
#BLOCK NAME:     Intro // a comment
#TYPE:           NORMAL
#METHOD:         RAW
#SCRIPT START:   $10
#SCRIPT STOP:    $40
#TABLE:          main.tbl
#COMMENTS:       No
#END BLOCK
"""


def test_raw_block():
    cf = parse_command_file(RAW_BLOCK)
    b = cf.blocks[0]
    assert b.name == "Intro" and b.game_name == "Synthetic"
    assert b.config.source == RangeSource(0x10, 0x40)
    assert b.config.string_type == EndToken()
    assert b.config.bound == 0x40 and b.table_file == "main.tbl"


def test_fixed_pointer_block_and_sub_table():
    text = """\
#SUB TABLE: extra.tbl
#BLOCK NAME: Names
#TYPE: FIXED_STRING && FIXED_LINE
#STRING LENGTH: 8
#STRING END: Yes
#END CTRL: [end]
#LINE LENGTH: 4
#LINE END: Yes
#LINE CTRL: <LINE>
#METHOD: POINTER_RELATIVE
#POINTER ENDIAN: LITTLE
#POINTER TABLE START: $100
#POINTER TABLE STOP: $110
#POINTER SIZE: 2
#POINTER SPACE: 1
#ATLAS PTRS: Yes
#BASE POINTER: $-8000
#TABLE: main.tbl
#TABLE ID: upper
#COMMENTS: Both
#STRINGS PER POINTER: 2
#STRING END REALIGN MULTIPLE: 4
#AUTO JUMP START: $200
#AUTO JUMP STOP: $210
#END BLOCK
#BLOCK NAME: Second
#TYPE: NORMAL
#METHOD: POINTER
#POINTER ENDIAN: BIG
#POINTER TABLE START: 0
#POINTER TABLE STOP: 4
#POINTER SIZE: 2
#POINTER SPACE: 0
#ATLAS PTRS: No
#STRINGS END AT NEXT POINTER: Yes
#TABLE: main.tbl
#COMMENTS: No
#END BLOCK
"""
    cf = parse_command_file(text)
    assert cf.sub_tables == ["extra.tbl"]
    b = cf.blocks[0]
    assert b.config.source == PointerTableSource(
        0x100, 0x110, 2, 3, "little", "linear", -0x8000
    )
    assert b.config.string_type == FixedLength(8, True)
    assert b.config.line_length == 4 and b.config.line_label == "LINE"
    assert (
        parse_command_file(text.replace("#LINE END: Yes", "#LINE END: No"))
        .blocks[0]
        .config.line_label
        == ""
    )
    assert b.config.show_end and b.config.end_label == "end"
    assert b.config.strings_per_pointer == 2 and b.config.realign == (4, 0)
    assert b.config.skips == ((0x200, 0x210),) and b.table_id == "upper"
    assert b.comments == "Both" and b.atlas_ptrs
    second = cf.blocks[1]
    assert second.game_name is None
    assert second.config.string_type == NextPointer()
    assert second.config.source.endian == "big"


@pytest.mark.parametrize(
    "text, message",
    [
        (RAW_BLOCK.replace("#END BLOCK\n", ""), "END BLOCK"),
        (
            RAW_BLOCK.replace(
                "#TYPE:           NORMAL\n", "#TYPE: NORMAL\n#TYPE: NORMAL\n"
            ),
            "repeated",
        ),
        (RAW_BLOCK.replace("#COMMENTS:       No\n", ""), "COMMENTS"),
        (
            RAW_BLOCK.replace("#SCRIPT STOP:    $40\n", "#SCRIPT STOP: x\n"),
            "bad number",
        ),
        (RAW_BLOCK + "#BOGUS: 1\n", "unknown command"),
        (
            RAW_BLOCK.replace(
                "#METHOD:         RAW\n", "#METHOD: RAW\n#BASE POINTER: 1\n"
            ),
            "not allowed",
        ),
        ("", "no blocks"),
    ],
)
def test_errors(text, message):
    with pytest.raises(CommandFileError, match=message):
        parse_command_file(text)
