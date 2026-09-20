"""The one rule a landing is held to: the bytes are the translation, so they
must say what the translator said and nothing else."""

from __future__ import annotations

from helpers import ABC_TABLE, table_set
from mapchar.core.block import BlockConfig, EndToken, RangeSource
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, layout_block, reads_back

TS = table_set(ABC_TABLE, "main")
DATA = bytes.fromhex("41 42 00 43 00 41 41")
"""``AB[end]``, ``C[end]`` and the unterminated ``AA``."""
CFG = BlockConfig(RangeSource(0, 7), EndToken(), "main", fill=b"\xee")


def laid_out(edits: dict[int, str]) -> tuple[list, bytes]:
    """``DATA``'s strings and the buffer the block lays ``edits`` out into."""
    ex = extract(DATA, CFG, TS)
    for i, text in edits.items():
        ex.strings[i].replacement = text
    result = layout_block(DATA, CFG, TS, ex.strings, None, None)
    for rec in ex.strings:
        rec.replacement = None
    return ex.strings, apply_splices(DATA, result.splices)


def test_the_bytes_the_layout_wrote_read_back_as_the_edit():
    strings, out = laid_out({0: "A[end]"})
    back = reads_back(CFG, TS, strings, out, {0: "A[end]"}, None, (0, 3))
    assert back.block is None and back.string is None
    # The reading comes back with it, so the re-read after the edit lands does
    # not read the same bytes a second time.
    assert [r.current_text() for r in back.extraction.strings] == [
        "A[end]",
        "C[end]",
        "AA",
    ]


def test_bytes_that_say_something_else_are_refused_by_the_string():
    strings, _ = laid_out({0: "A[end]"})
    wrong = bytes.fromhex("42 00 EE 43 00 41 41")
    back = reads_back(CFG, TS, strings, wrong, {0: "A[end]"}, None, (0, 3))
    assert back.string == "#0: reads back as 'B[end]'"
    assert back.block is None and back.extraction is None


def test_a_string_nobody_edited_may_not_change():
    strings, _ = laid_out({0: "A[end]"})
    meddled = bytes.fromhex("41 00 EE 42 00 41 41")
    back = reads_back(CFG, TS, strings, meddled, {0: "A[end]"}, None, None)
    assert back.string == "#1: would change to 'B[end]'"


def test_a_block_that_would_read_as_other_strings_is_refused_whole():
    strings, _ = laid_out({0: "A[end]"})
    recut = bytes.fromhex("41 00 00 43 00 41 41")
    back = reads_back(CFG, TS, strings, recut, {0: "A[end]"}, None, None)
    assert back.block == "the block would read as 4 strings instead of 3"
    assert back.string is None and back.extraction is None
