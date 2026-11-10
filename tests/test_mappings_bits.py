"""The bit-address mapping: a CPU that addresses bits, as the TMS34010 of
Midway's T-Unit and Wolf-Unit does, whose program ROM byte ``o`` is
``$FF800000 + 8 * o``. Built in as ``tms34010-bits``, the id the arcade
samples' own plugin had: that plugin file, still in a project's folder, is
passed over without a prompt or an issue, and any other is a duplicate."""

from __future__ import annotations

import hashlib

import pytest

from helpers import ABC_TABLE, relayout, table_set, texts
from mapchar.core.block import BlockConfig, EndToken, PointerTableSource
from mapchar.pipeline.extract import extract
from mapchar.plugins.aliases import SUPERSEDED
from mapchar.plugins.base import Stage
from mapchar.plugins.builtins.mappings import BitAddress
from mapchar.plugins.discovery import discover, plugin_roots
from mapchar.plugins.registry import default_registry, resolve_mapping
from mapchar.plugins.trust import TrustStore

TS = table_set(ABC_TABLE, "main")
REG = default_registry()
TMS = resolve_mapping(REG, "tms34010-bits")
BASE = 0xFF800000


def _rom() -> bytes:
    """Two little-endian bit addresses at $00, to ``AB[end]`` at $10 and
    ``C[end]`` at $13."""
    table = b"".join((BASE + 8 * o).to_bytes(4, "little") for o in (0x10, 0x13))
    return table + b"\xff" * 8 + bytes.fromhex("41 42 00 43 00") + b"\xff" * 11


def _cfg() -> BlockConfig:
    return BlockConfig(
        PointerTableSource(0, 8, 4, 4, "little", "tms34010-bits"),
        EndToken(),
        "main",
        bound=0x20,
        fill=b"\xee",
    )


def test_it_is_built_in_four_bytes_wide_and_needs_no_bank():
    assert "tms34010-bits" in REG.ids(Stage.MAPPING)
    assert isinstance(TMS, BitAddress) and TMS.base == BASE
    assert TMS.sizes == (4,) and not TMS.needs_bank
    assert TMS.info.name == "TMS34010 bit address (program ROM at $FF800000)"


def test_a_byte_is_eight_bits_past_the_base():
    for offset in (0, 1, 0x1234, 0xFFFFF):
        assert TMS.to_value(offset) == BASE + 8 * offset
        assert TMS.to_offset(BASE + 8 * offset) == offset


def test_a_value_below_the_base_or_off_a_byte_reaches_nothing():
    assert TMS.to_offset(BASE - 8) is None
    assert TMS.to_offset(0x10) is None
    for bit in range(1, 8):
        assert TMS.to_offset(BASE + 0x80 + bit) is None
    # Another base reads its own byte boundaries, not the T-Unit's.
    other = BitAddress(0x1004, "other", "Other")
    assert other.to_offset(0x1004 + 16) == 2 and other.to_offset(0x1008) is None


def test_a_block_reads_and_writes_its_pointers_through_it():
    rom = _rom()
    assert texts(extract(rom, _cfg(), TS, REG)) == ["AB[end]", "C[end]"]
    res, out = relayout(rom, _cfg(), TS, {0: "ABC[end]"}, REG)
    assert res.ok, res.problems
    assert out[4:8] == (BASE + 8 * 0x14).to_bytes(4, "little")
    assert texts(extract(out, _cfg(), TS, REG)) == ["ABC[end]", "C[end]"]


SHIPPED = r'''
"""Pointers of the Midway T-Unit's TMS34010, which addresses bits: the program
ROM's byte ``o`` is ``$FF800000 + 8 * o``. mapchar reads the sample's pointer
blocks through it (celPix does not load ``plugins/mappings``)."""

BASE = 0xFF800000


class Tms34010Bits:
    sizes = (4,)
    needs_bank = False

    def __init__(self, info):
        self.info = info

    def to_offset(self, value, bank=0, ptr_address=0):
        if value < BASE or value & 7:
            return None
        return (value - BASE) >> 3

    def to_value(self, offset, bank=0, ptr_address=0):
        return BASE + 8 * offset


def register(registry):
    from mapchar.plugins.base import PluginInfo, Stage

    registry.register(
        Tms34010Bits(
            PluginInfo(
                "tms34010-bits",
                "TMS34010 bit address (program ROM at $FF800000)",
                Stage.MAPPING,
                "Midway",
            )
        )
    )
'''
"""The arcade samples' ``plugins/mappings/tms34010_bits.py`` as it shipped."""


def _project(tmp_path, text: str, newline: str = "\n") -> list[tuple[str, str]]:
    folder = tmp_path / "project" / "plugins" / "mappings"
    folder.mkdir(parents=True)
    (folder / "tms34010_bits.py").write_bytes(
        text.lstrip("\n").replace("\n", newline).encode("utf-8")
    )
    return plugin_roots(None, str(tmp_path / "project"))


def _never_asked(path: str, digest: str) -> bool:
    raise AssertionError(f"asked to trust {path}")


def test_the_shipped_file_is_the_one_superseded():
    digest = hashlib.sha256(SHIPPED.lstrip("\n").encode("utf-8")).hexdigest()
    assert SUPERSEDED[digest] == "tms34010-bits"


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_the_shipped_plugin_is_passed_over_without_a_prompt(tmp_path, newline):
    registry = default_registry()
    roots = _project(tmp_path, SHIPPED, newline)
    result = discover(registry, roots, TrustStore(None), _never_asked)
    assert result.issues == [] and result.loaded == []
    assert isinstance(registry.plugin(Stage.MAPPING, "tms34010-bits"), BitAddress)


def test_any_other_plugin_with_the_id_is_a_duplicate(tmp_path):
    """Another base under the same id is not the built-in's to replace."""
    other = SHIPPED.replace("0xFF800000", "0xFFC00000")
    roots = _project(tmp_path, other)
    registry = default_registry()
    result = discover(registry, roots, TrustStore(None), lambda p, d: True)
    assert [i.message for i in result.issues] == [
        "duplicate plugin id 'tms34010-bits' for MAPPING"
    ]
    assert registry.plugin(Stage.MAPPING, "tms34010-bits").base == BASE


def test_a_duplicate_of_another_built_in_is_still_an_issue(tmp_path):
    folder = tmp_path / "project" / "plugins" / "mappings"
    folder.mkdir(parents=True)
    (folder / "mine.py").write_text(
        "from mapchar.plugins.base import PluginInfo, Stage\n"
        "class Mine:\n"
        "    info = PluginInfo('linear', 'Mine', Stage.MAPPING)\n"
        "    def to_offset(self, value, bank=0, ptr_address=0): return value\n"
        "    def to_value(self, offset, bank=0, ptr_address=0): return offset\n"
        "def register(registry): registry.register(Mine())\n"
    )
    result = discover(
        default_registry(),
        plugin_roots(None, str(tmp_path / "project")),
        TrustStore(None),
        lambda p, d: True,
    )
    assert [i.message for i in result.issues] == [
        "duplicate plugin id 'linear' for MAPPING"
    ]
