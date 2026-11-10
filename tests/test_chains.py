"""Chained blocks: strings the game reads back to back, one ending where the
next begins with no pointer of its own — read, written, checked and repaired
the way the game walks them. The Mortal Kombat II (Game Boy) story screens,
which draw chains of ``[u16 screen offset][u8 length][text]`` records, are the
case the tests hold the feature to."""

from __future__ import annotations

import json
import struct
from dataclasses import replace

import pytest

from conftest import game_rom, local_tool
from helpers import ASCII_TABLE, relayout, table_set
from mapchar.core.block import (
    Align,
    ChainMode,
    FixedLength,
    PointerRef,
    chain_refusal,
    recuts_strings,
    with_region,
)
from mapchar.core.errors import EncodeError
from mapchar.core.table import TableSet
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import (
    apply_splices,
    chain_heads,
    layout_block,
    pad_unit,
    padded_view,
    reads_back,
    repair_chains,
    string_ends,
)
from mapchar.project.formats.blockspec import format_config, parse_config
from mapchar.project.formats.table_native import parse_native
from mapchar.project.projectfile import (
    CLIPBOARD_KEY,
    PROJECT_VERSION,
    entries_from_payload,
    entries_payload,
    load_project,
    project_dict,
)

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
    text = local_tool("mk2_sample").table_files(data)["mk2-records.tbl"]
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
    realigned = replace(base, realign=(2, 0))
    assert "realign" in chain_refusal(realigned)
    # A skip range ends a chain rather than refusing one, and nothing is
    # packed across it: a block that holds Pack pads inside its strings.
    skipped = replace(base, skips=((0x86B0, 0x86B2),))
    assert chain_refusal(skipped) is None and skipped.chain_mode is ChainMode.PAD
    packed = replace(skipped, chain=ChainMode.PACK)
    assert packed.chained and packed.chain_mode is ChainMode.PAD
    assert replace(packed, skips=()).chain_mode is ChainMode.PACK
    assert fixed.chain_mode is None


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


# -- skip ranges between the strings ------------------------------------------
#
# The arcade Mortal Kombat games (Midway T-Unit) keep each message as a record:
# a 24-byte header, the text and its end token, then either a chain marker and
# alignment before the next record's header, or the argument longs a ``%d``
# code prints, at the next even offset after the end token. Each text must
# keep its exact length, so one block with skip ranges over what lies between
# the texts must write what one ``chain=pad`` block a record writes.

MESSAGES = (
    ("FLAWLESS VICTORY   ", b"\x01\x00"),
    ("ROUND %d ", b"\x00" + struct.pack(">II", 0x1234, 0x20)),
    ("FIGHT!", b"\x01"),
    ("%d WINS  ", b"\x00" + struct.pack(">I", 0x2000)),
)
"""Each message's text and what the game reads after it."""
HEADER = 24


def _messages(prefix: bool = False) -> tuple[bytes, list[tuple[int, int]]]:
    """The records of :data:`MESSAGES` between two stretches of other data,
    with each string's ``(start, end)``: from its text — or its one-byte
    length prefix — to just past its end token, or its last text byte."""
    data = b"\xee\x00\x20\xee"
    places = []
    for n, (text, tail) in enumerate(MESSAGES):
        # A header of its own bytes, a space and an end-token byte among them.
        data += bytes([0x80 + n, 0x00, 0x20, 0x41]) * (HEADER // 4)
        body = bytes([len(text)]) + text.encode() if prefix else text.encode() + b"\0"
        places.append((len(data), len(data) + len(body)))
        data += body + tail
    return data + b"\xee\x20\x00\xee", places


def _strings_type(prefix: bool) -> str:
    return "type=pascal:1" if prefix else "type=end"


def _one_block(places, prefix=False, align="", header=False) -> str:
    """One range block over every message, skip ranges over what lies
    between their texts — or, with ``header``, between a text and the next
    record, whose header the block's record header steps over."""
    first = places[0][0] - (HEADER if header else 0)
    skips = ",".join(
        f"${a[1]:X}>${b[0] - (HEADER if header else 0):X}"
        for a, b in zip(places, places[1:], strict=False)
    )
    words = f" header={HEADER}" if header else ""
    return (
        f"source=range start=${first:X} stop=${places[-1][1]:X} "
        f"{_strings_type(prefix)} table=main{words} skips={skips} fill=$20 "
        f"chain=pad{align}"
    )


def _string_blocks(places, prefix=False, align="") -> list[str]:
    """The blocks the sample projects map the same messages with: one range
    block a string."""
    return [
        f"source=range start=${a:X} stop=${b:X} {_strings_type(prefix)} "
        f"table=main fill=$20 chain=pad{align}"
        for a, b in places
    ]


def _write(data, spec, ts, edits, offset=0):
    """``edits`` — ``{index: (text, align)}`` counted from ``offset`` — laid
    out over the block ``spec``: the bytes, or ``None`` where it is refused,
    and the problems."""
    cfg = parse_config(spec)
    ex = extract(data, cfg, ts)
    for rec in ex.strings:
        if rec.index + offset in edits:
            rec.replacement, rec.align = edits[rec.index + offset]
    result = layout_block(data, cfg, ts, ex.strings)
    return (apply_splices(data, result.splices) if result.ok else None), [
        (p.index + offset, p.message) for p in result.problems
    ]


def _through_both(edits, prefix=False, align="", header=False):
    """``edits`` written through the one block with skip ranges, and through
    the blocks of one string each, one after the other."""
    data, places = _messages(prefix)
    ts = table_set(ASCII_TABLE)
    one = _write(data, _one_block(places, prefix, align, header), ts, edits)
    out, problems = data, []
    for i, spec in enumerate(_string_blocks(places, prefix, align)):
        written, refused = _write(out, spec, ts, edits, offset=i)
        out = written if written is not None else out
        problems += refused
    return data, one, (out if not problems else None, problems)


END_EDITS = {
    "shorter": {0: ("PERFECT[end]", None), 3: ("%d WON[end]", None)},
    "equal": {1: ("RUNDE %d [end]", None), 2: ("KAMPF![end]", None)},
    "aligned": {0: ("WIN[end]", None), 1: ("R %d[end]", None), 2: ("GO[end]", None)},
    "own align": {
        0: ("WIN[end]", Align.CENTRE),
        1: ("R %d[end]", Align.LEFT),
        2: ("GO[end]", Align.RIGHT),
    },
}


@pytest.mark.parametrize("align", ["", " align=left", " align=centre", " align=right"])
@pytest.mark.parametrize("case", sorted(END_EDITS))
@pytest.mark.parametrize("header", [False, True], ids=["skipped", "header"])
def test_a_block_with_skips_pads_as_a_block_a_string_does(case, align, header):
    """Acceptance: byte for byte what the blocks of one string each write, for
    a shorter text, one of the same length, every alignment and a string's
    own — with each record's header skipped, or stepped over as the block's
    record header with the skips over what follows the text alone."""
    data, (one, refused), (each, refused_each) = _through_both(
        END_EDITS[case], align=align, header=header
    )
    assert not refused and not refused_each
    assert one == each and one != data
    assert len(one) == len(data)


@pytest.mark.parametrize("header", [False, True], ids=["skipped", "header"])
def test_a_longer_text_is_refused_in_both(header):
    edits = {1: ("ROUND %d!!![end]", None), 2: ("FIGHT[end]", None)}
    _data, (one, refused), (each, refused_each) = _through_both(edits, header=header)
    assert one is None and each is None
    assert [i for i, _ in refused] == [i for i, _ in refused_each] == [1]
    assert "holds 10, so 2 do not fit" in refused[0][1]
    assert refused[0][1] == refused_each[0][1]


def test_what_room_a_string_between_skips_has_and_what_it_reads_back_as():
    """Each string's room is its own place: the skip in front of the next
    string ends the chain there, and the last before the stop has up to the
    bound. A write reads back as the strings it wrote, pad and all, with no
    gap, and an untouched block writes its own bytes."""
    data, places = _messages()
    ts = table_set(ASCII_TABLE)
    spec = _one_block(places, align=" align=centre")
    cfg = parse_config(spec)
    assert cfg.chained and cfg.chain_mode is ChainMode.PAD
    assert parse_config(format_config(cfg)) == cfg and format_config(cfg) == spec
    ex = extract(data, cfg, ts)
    assert [r.start for r in ex.strings] == [a for a, _ in places]
    assert string_ends(data, cfg, ex.strings) == {
        i: b for i, (_, b) in enumerate(places)
    }
    assert chain_heads(cfg, ex.strings) == {0, 1, 2, 3}
    assert not ex.notices and not ex.chain_gaps
    untouched = layout_block(data, cfg, ts, ex.strings)
    assert untouched.ok and apply_splices(data, untouched.splices) == data
    edits = {0: "WIN[end]", 3: "%d[end]"}
    for i, text in edits.items():
        ex.strings[i].replacement = text
    result = layout_block(data, cfg, ts, ex.strings)
    out = apply_splices(data, result.splices)
    back = reads_back(cfg, ts, ex.strings, out, edits)
    assert back.block is None and back.string is None
    assert [r.current_text() for r in back.extraction.strings] == [
        "        WIN        [end]",
        "ROUND %d [end]",
        "FIGHT![end]",
        "   %d    [end]",
    ]
    assert not back.extraction.chain_gaps and not back.extraction.notices
    # Everything between the texts is as it was.
    for (_, end), (start, _) in zip(places, places[1:], strict=False):
        assert out[end:start] == data[end:start]


def test_a_length_prefix_with_skips_pads_as_a_block_a_string_does():
    """The length prefix counts the pad, as in a block of one string."""
    edits = {0: ("PERFECT", Align.RIGHT), 1: ("RUNDE %d ", None), 3: ("%d", None)}
    data, (one, refused), (each, _) = _through_both(edits, prefix=True)
    assert not refused and one == each
    _, places = _messages(prefix=True)
    a, b = places[0]
    assert one[a:b] == bytes([19]) + b" " * 12 + b"PERFECT"
    _, (one, refused), (each, _) = _through_both({2: ("FIGHT!!", None)}, prefix=True)
    assert one is None and each is None and "holds 6, so 1 do not fit" in refused[0][1]


def test_strings_with_no_skip_between_them_stay_one_chain():
    """A skip ends a chain where it stands, and only there: two texts back to
    back still tie the first to the second, and fill in front of the second
    is a gap where fill in front of a skip is not."""
    ts = table_set(ASCII_TABLE)
    head = bytes([0x80] * 4)
    data = head + b"HELLO\0BYE\0" + b"\x01\x00" + head + b"FIGHT!\0"
    spec = "source=range start=$4 stop=$1B type=end table=main skips=$E>$14 chain=pad"
    cfg = parse_config(spec)
    ex = extract(data, cfg, ts)
    assert chain_heads(cfg, ex.strings) == {0, 2}
    assert string_ends(data, cfg, ex.strings) == {0: 0xA, 1: 0xE, 2: 0x1B}
    _cfg, _ex, result, out = _laid(data, spec, ts, {0: "HI[end]"})
    assert result.ok and out[4:0xE] == b"HI   \0BYE\0"
    # Shortened outside the chain: the second string stands in fill, the
    # third, past the skip, does not.
    broken = head + b"HI\0\xff\xff\xffBYE\0\xff\xff" + head + b"FIGHT!\0"
    spec = spec.replace("skips=$E>$14", "skips=$10>$14") + " fill=$FF"
    cfg = parse_config(spec)
    ex = extract(broken, cfg, ts)
    assert ex.chain_gaps == {1: 7} and len(ex.notices) == 1
    splices, problems = repair_chains(broken, cfg, ts, ex.strings, ex.chain_gaps)
    assert not problems
    assert apply_splices(broken, splices)[4:0x10] == b"HI   \0BYE\0\xff\xff"


def test_a_string_a_skip_cuts_in_two_is_not_rewritten():
    ts = table_set(ASCII_TABLE)
    data = b"HEL\x01\x02LO\0BYE\0"
    spec = "source=range start=$0 stop=$C type=end table=main skips=$3>$5 chain=pad"
    _cfg, ex, result, _out = _laid(data, spec, ts, {0: "HI[end]"})
    assert ex.strings[0].current_text() == "HELLO[end]"
    assert not result.ok and "spans a skip range" in result.problems[0].message


@pytest.mark.parametrize(
    "fill, tail",
    [("", b"\xff\xff"), (" fill=$00", b"\x00"), (" fill=$00 end_is_fill=1", b"\x00")],
    ids=["default fill", "fill 00", "fill 00, end is fill"],
)
def test_a_skip_that_holds_the_fill_is_no_string_s_room(fill, tail):
    """The bytes a skip range steps over are not the string's, even when they
    are the fill — a chain marker's alignment byte of ``00`` under a fill of
    ``00`` — so a longer text is refused rather than written into them, and
    an equal or shorter one leaves them as they are."""
    ts = table_set(ASCII_TABLE)
    head = bytes([0x80] * 4)
    data = head + b"HELLO\0" + tail + head + b"BYE\0"
    end, nxt = 10, 10 + len(tail) + 4
    spec = (
        f"source=range start=$4 stop=${len(data):X} type=end table=main "
        f"skips=${end:X}>${nxt:X}{fill} chain=pad"
    )
    cfg = parse_config(spec)
    ex = extract(data, cfg, ts)
    assert [r.current_text() for r in ex.strings] == ["HELLO[end]", "BYE[end]"]
    assert string_ends(data, cfg, ex.strings)[0] == end
    _cfg, _ex, result, _out = _laid(data, spec, ts, {0: "HELLOO[end]"})
    assert not result.ok and "holds 6, so 1 do not fit" in result.problems[0].message
    for text, written in (("WORLD[end]", b"WORLD\0"), ("HI[end]", b"HI   \0")):
        _cfg, _ex, result, out = _laid(data, spec, ts, {0: text})
        assert result.ok and out[4:end] == written
        assert out[end:] == data[end:]


def test_a_version_3_project_keeps_its_chain_over_skips_unchained(tmp_path):
    """Version 3 read a block asking for a chain over skip ranges as not
    chained and wrote it slotted: such a project opens meaning just that, and
    keeps meaning it once saved. Written at the current version, the same
    line is a chain."""
    data = b"HELLO\0BYE\0\x01\x00" + bytes([0x80] * 4) + b"FIGHT!\0"
    line = (
        "source=range start=$0 stop=$17 type=end table=main skips=$A>$10 "
        "chain=pad pad=$2E"
    )

    def project(version: int) -> str:
        path = tmp_path / f"v{version}.mapchar"
        block = {"kind": "block", "name": "messages", "parent": 0, "config": line}
        entries = [{"kind": "file", "name": "r"}, block]
        path.write_text(json.dumps({"version": version, "entries": entries}))
        return str(path)

    loaded = load_project(project(3))
    cfg = loaded.entries[1].config
    assert loaded.migrated_from == 3
    assert cfg.chain is None and not cfg.chained
    assert cfg.skips == ((0xA, 0x10),) and cfg.pad == b"."
    ts = table_set(ASCII_TABLE)
    res, out = relayout(data, cfg, ts, {0: "HI[end]"})
    assert res.ok and out[:10] == bytes.fromhex("48 49 00 ff ff ff 42 59 45 00")
    back = extract(out, cfg, ts)
    assert [r.current_text() for r in back.strings] == [
        "HI[end]",
        "BYE[end]",
        "FIGHT![end]",
    ]
    assert not back.notices and not back.chain_gaps
    saved = tmp_path / "saved.mapchar"
    saved.write_text(json.dumps(project_dict(loaded.entries, None, str(tmp_path))))
    again = load_project(str(saved))
    assert again.migrated_from is None and not again.entries[1].config.chained
    current = load_project(project(PROJECT_VERSION)).entries[1].config
    assert current.chained and current.chain_mode is ChainMode.PAD


def test_a_block_copied_in_a_version_3_build_pastes_as_it_was_there(tmp_path):
    """A clipboard payload says the version it was copied at, and walks
    forward as a project file does: a chain over skip ranges copied where it
    was not one pastes unchained; copied at the current version, or without
    skip ranges, it pastes as it is."""
    over = "source=range start=$0 stop=$17 type=end table=main skips=$A>$10"
    plain = "source=range start=$0 stop=$17 type=end table=main"
    path = tmp_path / "p.mapchar"
    blocks = [
        {"kind": "block", "name": name, "parent": 0, "config": line}
        for name, line in (("skips", over + " chain=pack"), ("none", plain))
    ]
    entries = [{"kind": "file", "name": "r"}, *blocks]
    path.write_text(json.dumps({"version": PROJECT_VERSION, "entries": entries}))
    loaded = load_project(str(path))
    loaded.entries[2].config = replace(loaded.entries[2].config, chain=ChainMode.PAD)
    payload = json.loads(entries_payload(loaded.entries))
    assert payload["version"] == PROJECT_VERSION

    def pasted(version: int) -> list:
        payload["version"] = version
        return [e.config for e in entries_from_payload(json.dumps(payload))[1:]]

    skipped, unskipped = pasted(3)
    assert skipped.chain is None and skipped.skips == ((0xA, 0x10),)
    assert unskipped.chained and unskipped.chain is ChainMode.PAD
    skipped, unskipped = pasted(PROJECT_VERSION)
    assert skipped.chained and skipped.chain is ChainMode.PACK
    assert skipped.chain_mode is ChainMode.PAD and unskipped.chained
    # A payload from a newer build is read as it stands.
    skipped, _ = pasted(PROJECT_VERSION + 1)
    assert skipped.chain is ChainMode.PACK


def test_a_view_s_reading_keeps_its_chain_through_the_step(tmp_path):
    """The 3 → 4 step unchains a block's line and only that: a file's or a
    bookmark's reading is a view, which reads no chain, and a block made from
    it is cut to its region without the skips — chained in version 3 as now —
    so it loads as written, at either version and from a pasted payload."""
    line = "source=range start=$0 stop=$17 type=end table=main skips=$10>$14 chain=pack"
    entries = [
        {"kind": "file", "name": "r", "session": {"config": line}},
        {
            "kind": "bookmark",
            "name": "b",
            "parent": 0,
            "offset": 4,
            "session": {"config": line},
        },
        {"kind": "block", "name": "messages", "parent": 0, "config": line},
    ]
    path = tmp_path / "p.mapchar"
    for version in (3, PROJECT_VERSION):
        path.write_text(json.dumps({"version": version, "entries": entries}))
        file, bookmark, block = load_project(str(path)).entries
        for view in (file.session.config, bookmark.session.config):
            assert view.chain is ChainMode.PACK and view.skips == ((0x10, 0x14),)
            made = with_region(view, 0, 8)
            assert made.chained and made.chain_mode is ChainMode.PACK
        assert block.config.chained is (version == PROJECT_VERSION)
    payload = json.dumps({"version": 3, CLIPBOARD_KEY: entries[:2]})
    file, bookmark = entries_from_payload(payload)
    assert file.session.config.chain is ChainMode.PACK
    assert bookmark.session.config.chain is ChainMode.PACK
