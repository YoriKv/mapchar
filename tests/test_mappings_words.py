"""The word-address mappings: a CPU that addresses 32-bit words, as the
TMS320C3x of Midway's Zeus (Mortal Kombat 4) does, whose payload byte ``o``
is word ``base + o / 4``. Built in as ``tms320c3x-ram`` (base 0) and
``tms320c3x-rom`` (base ``$A00000``)."""

from __future__ import annotations

import pytest

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import BlockConfig, EndToken, PointerTableSource
from mapchar.pipeline.extract import extract
from mapchar.plugins.base import Stage
from mapchar.plugins.builtins.mappings import WordAddress
from mapchar.plugins.registry import default_registry, resolve_mapping

TS = table_set(ABC_TABLE, "main")
REG = default_registry()
RAM = resolve_mapping(REG, "tms320c3x-ram")
ROM = resolve_mapping(REG, "tms320c3x-rom")


def _rom(base: int) -> bytes:
    """Two little-endian word addresses at $00, to ``AB[end]`` at $10 and
    ``C[end]`` at $14."""
    table = b"".join((base + o // 4).to_bytes(4, "little") for o in (0x10, 0x14))
    return table + b"\xff" * 8 + bytes.fromhex("41 42 00 00 43 00 00 00") + b"\xff" * 8


def _cfg(mapping: str, realign: tuple[int, int] = (4, 0)) -> BlockConfig:
    """Strings start on a word, as the game's do."""
    return BlockConfig(
        PointerTableSource(0, 8, 4, 4, "little", mapping),
        EndToken(),
        "main",
        bound=0x20,
        fill=b"\x00",
        realign=realign,
    )


@pytest.mark.parametrize(
    ("mapping", "base"), [(RAM, 0), (ROM, 0xA00000)], ids=["ram", "rom"]
)
def test_they_are_built_in_four_bytes_wide_and_need_no_bank(mapping, base):
    assert mapping.info.id in REG.ids(Stage.MAPPING)
    assert isinstance(mapping, WordAddress)
    assert (mapping.base, mapping.unit) == (base, 4)
    assert mapping.sizes == (4,) and not mapping.needs_bank


def test_a_word_is_four_bytes_past_the_base():
    for offset in (0, 4, 0x1234, 0xFFFFFC):
        assert ROM.to_value(offset) == 0xA00000 + offset // 4
        assert ROM.to_offset(0xA00000 + offset // 4) == offset
        assert RAM.to_offset(RAM.to_value(offset)) == offset


def test_a_value_below_the_base_reaches_nothing():
    assert ROM.to_offset(0x9FFFFF) is None
    assert ROM.to_offset(0x10) is None
    assert RAM.to_offset(-1) is None


def test_an_offset_off_a_word_does_not_read_back():
    assert ROM.to_offset(ROM.to_value(0x13)) == 0x10


@pytest.mark.parametrize(
    ("mapping", "base"), [("tms320c3x-ram", 0), ("tms320c3x-rom", 0xA00000)]
)
def test_a_block_reads_and_writes_its_pointers_through_it(mapping, base):
    rom = _rom(base)
    assert texts(extract(rom, _cfg(mapping), TS, REG)) == ["AB[end]", "C[end]"]
    res, out = relayout(rom, _cfg(mapping), TS, {0: "ABCAB[end]"}, REG)
    assert res.ok, res.problems
    # "ABCAB" and its end take two words; "C" moves to the next word.
    assert out[4:8] == (base + 0x18 // 4).to_bytes(4, "little")
    assert texts(extract(out, _cfg(mapping), TS, REG)) == ["ABCAB[end]", "C[end]"]


def test_a_string_packed_off_a_word_is_refused():
    cfg = _cfg("tms320c3x-rom", realign=(0, 0))
    res, _ = relayout(_rom(0xA00000), cfg, TS, {0: "AB[end]"}, REG)
    assert not res.ok
    assert any("cannot reach" in p.message for p in res.problems)
