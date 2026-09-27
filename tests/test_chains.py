"""Chained blocks: strings the game reads back to back, one ending where the
next begins with no pointer of its own — read, written, checked and repaired
the way the game walks them. The Mortal Kombat II (Game Boy) story screens,
which draw chains of ``[u16 screen offset][u8 length][text]`` records, are the
case the tests hold the feature to."""

from __future__ import annotations

import struct
from dataclasses import replace

import pytest

from conftest import game_rom, tool_module
from helpers import ASCII_TABLE, relayout, table_set
from mapchar.core.block import (
    Align,
    ChainMode,
    FixedLength,
    PointerRef,
    chain_refusal,
    recuts_strings,
)
from mapchar.core.errors import EncodeError
from mapchar.core.table import TableSet
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import (
    apply_splices,
    layout_block,
    pad_unit,
    padded_view,
    reads_back,
    repair_chains,
    string_ends,
)
from mapchar.project.formats.blockspec import format_config, parse_config
from mapchar.project.formats.table_native import parse_native

ROM = "Mortal Kombat II (USA, Europe).gb"
MOD = "gb_mk2_us_mod.gb"
"""The shipped translation, whose story chains a write outside the chain broke."""
BANK2 = 0x4000

RECORDS = "type=pascal:1 table=mk2-records header=2"
OUTWORLD = f"source=range start=$86A6 stop=$86CE {RECORDS}"
GORO = f"source=range start=$86E8 stop=$87C8 {RECORDS} breaks=1,5,9,10"
ENDING = f"source=range start=$911C stop=$9179 {RECORDS} breaks=2"
GORO_OPERANDS = (0x1241, 0x124C, 0x1276, 0x128A, 0x1295)
"""The ``ld hl,nn`` operands that reach each chain of Goro's lair."""


@pytest.fixture(scope="module")
def mk2():
    """The original ROM and the record table the sample builds from it."""
    data = game_rom("MK2", ROM).read_bytes()
    text = tool_module("mk2_sample").table_files(data)["mk2-records.tbl"]
    table = parse_native(text).table
    return data, TableSet.build(table, {table.id: table})


def _laid(data, spec, tables, edits, align=None, pointers=None):
    """``edits`` laid out over the block ``spec`` reads, with the result."""
    cfg = parse_config(spec)
    ex = extract(data, cfg, tables)
    for i, text in edits.items():
        ex.strings[i].replacement = text
        ex.strings[i].align = align
    for i, refs in (pointers or {}).items():
        ex.strings[i].pointers = refs
    result = layout_block(data, cfg, tables, ex.strings)
    for rec in ex.strings:
        rec.replacement = None
    out = apply_splices(data, result.splices) if result.ok else None
    return cfg, ex, result, out


def _game_walk(data: bytes, at: int, count: int) -> int:
    """Where ``$18DE`` leaves ``hl`` after ``count`` records from ``at``."""
    for _ in range(count):
        at += 3 + data[at + 2]
    return at


# -- the setting ------------------------------------------------------------


def test_the_configuration_line_carries_the_chain():
    spec = f"{OUTWORLD} chain=pad breaks=3,1 pad=$20 align=centre"
    cfg = parse_config(spec)
    assert cfg.chain is ChainMode.PAD and cfg.chain_breaks == (1, 3)
    assert cfg.pad == b" " and cfg.align is Align.CENTRE
    assert format_config(cfg).endswith("chain=pad breaks=1,3 pad=$20 align=centre")
    assert parse_config(format_config(cfg)) == cfg
    # Absent means off, and an older line reads unchanged.
    plain = parse_config(OUTWORLD)
    assert plain.chain is None and not plain.chained
    assert format_config(plain) == OUTWORLD


def test_what_cannot_chain_says_why():
    base = parse_config(f"{OUTWORLD} chain=pad")
    assert chain_refusal(base) is None and base.chained
    fixed = replace(base, string_type=FixedLength(4))
    assert chain_refusal(fixed) and not fixed.chained
    skipped = replace(base, skips=((0x86B0, 0x86B2),))
    assert "skip" in chain_refusal(skipped)
    realigned = replace(base, realign=(2, 0))
    assert "realign" in chain_refusal(realigned)


def test_chaining_leaves_the_strings_where_they_are_cut():
    """Chains change how the strings are read back and written, not where they
    are cut: switching them on asks nothing of an edited block."""
    base = parse_config(OUTWORLD)
    assert not recuts_strings(base, replace(base, chain=ChainMode.PAD))
    assert not recuts_strings(base, replace(base, chain_breaks=(1,)))


# -- a synthetic chain --------------------------------------------------------


def _records(*texts: str, tail: int = 4) -> bytes:
    """``[header][length][text]`` records back to back, then ``$FF``."""
    out = b""
    for n, text in enumerate(texts):
        out += bytes([0x10 + n, 0x20]) + bytes([len(text)]) + text.encode()
    return out + b"\xff" * tail


SYNTH = "source=range start=$0 stop=${stop:X} type=pascal:1 table=main header=2"


def test_a_shorter_text_is_padded_inside_its_string():
    data = _records("HELLO THERE", "BYE")
    ts = table_set(ASCII_TABLE)
    spec = SYNTH.format(stop=len(data)) + " chain=pad"
    _cfg, _ex, result, out = _laid(data, spec, ts, {0: "HI"})
    assert result.ok
    assert out[:14] == bytes([0x10, 0x20, 11]) + b"HI" + b" " * 9
    assert out[14:] == data[14:]


def test_the_pad_is_the_block_s_where_it_names_one():
    data = _records("HELLO THERE", "BYE")
    ts = table_set(ASCII_TABLE)
    spec = SYNTH.format(stop=len(data)) + " chain=pad pad=$2E"
    _cfg, _ex, _result, out = _laid(data, spec, ts, {0: "HI"})
    assert out[3:14] == b"HI........."
    with pytest.raises(EncodeError, match="not text"):
        pad_unit(replace(parse_config(spec), pad=b"\x01"), table_set(ASCII_TABLE))


def test_padding_an_end_token_string_goes_before_its_end_token():
    data = b"HELLO\x00BYE\x00" + b"\xff" * 4
    ts = table_set(ASCII_TABLE)
    spec = f"source=range start=$0 stop=${len(data):X} type=end table=main chain=pad"
    _cfg, _ex, result, out = _laid(data, spec, ts, {0: "HI[end]"})
    assert result.ok and out[:6] == b"HI   \x00"
    back = extract(out, parse_config(spec), ts)
    assert back.strings[0].current_text() == "HI   [end]"


def test_the_last_of_a_chain_may_grow_into_the_fill_after_it():
    data = _records("HELLO", "BYE", tail=4)
    ts = table_set(ASCII_TABLE)
    spec = SYNTH.format(stop=len(data)) + " chain=pad"
    cfg = parse_config(spec)
    ex = extract(data, cfg, ts)
    ends = string_ends(data, cfg, ex.strings)
    assert ends[0] == 8 and ends[1] == len(data)
    _cfg, _ex, result, out = _laid(data, spec, ts, {1: "BYE NOW"})
    assert result.ok and out[8:] == bytes([0x11, 0x20, 7]) + b"BYE NOW"
    # Shorter, it keeps the place it had.
    _cfg, _ex, _result, out = _laid(data, spec, ts, {1: "B"})
    assert out[8:] == bytes([0x11, 0x20, 3]) + b"B  " + b"\xff" * 4


def test_a_padded_text_reads_back_as_what_was_typed():
    """The pad a write adds is not the translator's: the edit is checked
    without it, and text read back from an earlier write is laid afresh."""
    data = _records("HELLO THERE", "BYE")
    ts = table_set(ASCII_TABLE)
    spec = SYNTH.format(stop=len(data)) + " chain=pad"
    cfg, ex, result, out = _laid(data, spec, ts, {0: "HI"})
    assert reads_back(cfg, ts, ex.strings, out, {0: "HI"}).extraction is not None
    _cfg, _ex, _result, again = _laid(out, spec, ts, {0: "HI         "})
    assert again == out
    assert padded_view("HI   ", " ", "·", ts) == "HI···"


def test_a_pack_that_moves_a_chain_s_first_string_needs_its_pointer():
    records = _records("HELLO THERE", "BYE", "NEXT", tail=4)
    # One byte past the text points at the third record, at 20.
    data = records + b"\x00" * (0x40 - len(records)) + bytes([20])
    ts = table_set(ASCII_TABLE)
    spec = SYNTH.format(stop=len(records)) + " chain=pack breaks=2"
    _cfg, _ex, result, _out = _laid(data, spec, ts, {0: "HI"})
    assert [p.index for p in result.problems] == [2]
    assert "has no pointer" in result.problems[0].message
    # A string inside a chain moves with the one before it and needs none.
    ref = PointerRef(0x40, 1, "little", "linear", 0, 20)
    _cfg, _ex, result, out = _laid(data, spec, ts, {0: "HI"}, pointers={2: (ref,)})
    assert result.ok, result.problems
    assert out[0x40] == 11
    assert out[11:13] == bytes([0x12, 0x20])


# -- Mortal Kombat II ----------------------------------------------------------


def test_pad_outworld(mk2):
    """Acceptance 1: nothing moves, and the game's walk lands where it did."""
    data, ts = mk2
    cfg, ex, result, out = _laid(
        data, f"{OUTWORLD} chain=pad", ts, {0: "NOW YOU RETURN"}
    )
    assert result.ok
    assert out[0x86A6:0x86CE] == (
        bytes.fromhex("80 cb 13")
        + b"NOW YOU RETURN"
        + b" " * 5
        + bytes.fromhex("c2 cb 0f")
        + b"TO THE OUTWORLD"
    )
    back = reads_back(cfg, ts, ex.strings, out, {0: "NOW YOU RETURN"})
    assert back.block is None and back.string is None
    assert [r.current_text() for r in back.extraction.strings] == [
        "NOW YOU RETURN     ",
        "TO THE OUTWORLD",
    ]
    assert _game_walk(out, 0x86A6, 1) == 0x86BC


def test_pad_too_long_names_the_room(mk2):
    """Acceptance 2."""
    data, ts = mk2
    _cfg, _ex, result, _out = _laid(data, f"{OUTWORLD} chain=pad", ts, {0: "X" * 20})
    assert not result.ok
    assert "holds 19" in result.problems[0].message


def test_pad_centred(mk2):
    """Acceptance 3."""
    data, ts = mk2
    spec = f"{OUTWORLD} chain=pad"
    _cfg, _ex, _result, out = _laid(data, spec, ts, {0: "NOW YOU RETURN"}, Align.CENTRE)
    assert out[0x86A8] == 0x13
    assert out[0x86A9:0x86BC] == b"  NOW YOU RETURN   "


def test_pack_outworld(mk2):
    """Acceptance 4: the first string stays, so its operand is untouched."""
    data, ts = mk2
    _cfg, _ex, result, out = _laid(
        data, f"{OUTWORLD} chain=pack", ts, {0: "NOW YOU RETURN"}
    )
    assert result.ok
    assert out[0x86A6:0x86CE] == (
        bytes.fromhex("80 cb 0e")
        + b"NOW YOU RETURN"
        + bytes.fromhex("c2 cb 0f")
        + b"TO THE OUTWORLD"
        + b"\xff" * 5
    )
    assert out[0x0FFF:0x1001] == data[0x0FFF:0x1001]


def _operands(data, strings):
    """Goro's lair's ``ld hl`` operands, attached to the strings they reach."""
    out = {}
    for op in GORO_OPERANDS:
        value = struct.unpack_from("<H", data, op)[0]
        rec = next(r for r in strings if r.start - 2 == value + BANK2)
        out[rec.index] = (PointerRef(op, 2, "little", "linear", BANK2, value),)
    return out


def test_pack_goro_s_lair(mk2):
    """Acceptance 5: each later chain moves and its operand follows it; with
    none attached, the write is refused before anything happens."""
    data, ts = mk2
    spec = f"{GORO} chain=pack"
    _cfg, _ex, result, _out = _laid(data, spec, ts, {0: "CONGRATS !!"})
    assert sorted(p.index for p in result.problems) == [1, 5, 9, 10]
    assert all("has no pointer" in p.message for p in result.problems)
    cfg = parse_config(spec)
    pointers = _operands(data, extract(data, cfg, ts).strings)
    cfg, ex, result, out = _laid(data, spec, ts, {0: "CONGRATS !!"}, pointers=pointers)
    assert result.ok, result.problems
    moved = [struct.unpack_from("<H", out, op)[0] for op in GORO_OPERANDS]
    assert moved == [0x46E8, 0x46F6, 0x473E, 0x478E, 0x47A4]
    assert reads_back(cfg, ts, ex.strings, out, {0: "CONGRATS !!"}).extraction
    # The game's own walk finds every chain whole.
    for op, count in zip(GORO_OPERANDS, (1, 4, 4, 1, 2), strict=True):
        at = struct.unpack_from("<H", out, op)[0] + BANK2
        end = _game_walk(out, at, count)
        assert all(out[a] != 0xFF for a in range(at, end))


def test_a_layout_that_leaves_fill_in_a_chain_fails_the_check(mk2):
    """Acceptance 7: the slotted write of old leaves fill between two chained
    strings, and the check refuses it though every string encodes."""
    data, ts = mk2
    _cfg, ex, result, out = _laid(data, OUTWORLD, ts, {0: "NOW YOU RETURN"})
    assert result.ok
    chained = parse_config(f"{OUTWORLD} chain=pad")
    back = reads_back(chained, ts, ex.strings, out, {0: "NOW YOU RETURN"})
    assert back.block is not None and "fill" in back.block
    # Unchained, the same bytes read back clean: the old check was mapchar's.
    assert reads_back(
        parse_config(OUTWORLD), ts, ex.strings, out, {0: "NOW YOU RETURN"}
    ).extraction


SECTION_2_5 = {
    # record: (old length, new length, first and last byte that become spaces)
    0x46A6: (0x11, 0x13, 0x46BA, 0x46BB),
    0x46FD: (0x0B, 0x12, 0x470B, 0x4711),
    0x4712: (0x08, 0x10, 0x471D, 0x4724),
    0x4725: (0x0A, 0x0F, 0x4732, 0x4736),
    0x4745: (0x12, 0x13, 0x475A, 0x475A),
    0x475B: (0x0B, 0x14, 0x4769, 0x4771),
    0x4772: (0x0F, 0x13, 0x4784, 0x4787),
    0x5139: (0x06, 0x0A, 0x5142, 0x5145),
    0x5146: (0x0F, 0x13, 0x5158, 0x515B),
    0x515C: (0x0C, 0x0D, 0x516B, 0x516B),
}
"""The repair the shipped translation needs (the spec's §2.5), in CPU
addresses of bank 2."""


def test_a_broken_translation_is_noticed_and_repaired(mk2):
    """Acceptance 6: a notice at the first gap of each chain, and a repair
    that writes exactly the bytes the game needs."""
    _data, ts = mk2
    mod = game_rom("MK2", MOD).read_bytes()
    out = bytearray(mod)
    noticed = []
    for spec in (OUTWORLD, GORO, ENDING):
        cfg = parse_config(f"{spec} chain=pad")
        ex = extract(mod, cfg, ts)
        noticed += [n.offset - BANK2 for n in ex.notices]
        splices, problems = repair_chains(mod, cfg, ts, ex.strings, ex.chain_gaps)
        assert not problems
        for s in splices:
            out[s.offset : s.end] = s.data
        assert not extract(bytes(out), cfg, ts).chain_gaps
    assert noticed == [0x46BA, 0x470B, 0x475A, 0x5142]
    expected = bytearray(mod)
    for record, (old, new, first, last) in SECTION_2_5.items():
        assert mod[BANK2 + record + 2] == old
        expected[BANK2 + record + 2] = new
        expected[BANK2 + first : BANK2 + last + 1] = b" " * (last - first + 1)
    assert bytes(out) == bytes(expected)


def test_the_repaired_translation_is_the_one_fix_mod_made(mk2):
    """The chains of ``fix_mod.py``'s output read with no gap, as the game
    reads them."""
    _data, ts = mk2
    fixed = game_rom("MK2", "gb_mk2_us_mod_fixed.gb").read_bytes()
    for spec in (OUTWORLD, GORO, ENDING):
        ex = extract(fixed, parse_config(f"{spec} chain=pad"), ts)
        assert not ex.chain_gaps and not ex.notices


def test_off_by_default_the_sample_blocks_write_as_before(mk2):
    """Acceptance 8, for the records: unchained, a shortened record is still
    written slotted, fill and all."""
    data, ts = mk2
    res, out = relayout(data, parse_config(OUTWORLD), ts, {0: "NOW YOU RETURN"})
    assert res.ok and out[0x86B7:0x86BC] == b"\xff" * 5
