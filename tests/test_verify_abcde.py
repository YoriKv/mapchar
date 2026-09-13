"""Compare mapchar's extraction with abcde's Cartographer dump of the fixtures."""

from __future__ import annotations

import os

import pytest

from helpers import cartographer_blocks, load_abcde_tables, normalise_dump, texts
from mapchar.core.table import TableSet
from mapchar.pipeline.exchange.cartographer import parse_command_file
from mapchar.pipeline.extract import extract

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures", "abcde")


def read(name: str, binary: bool = False):
    path = os.path.join(FIXTURES, name)
    if binary:
        with open(path, "rb") as f:
            return f.read()
    with open(path, encoding="utf-8") as f:
        return f.read()


def expected_blocks() -> dict[str, str]:
    """Block name to the dumped text, from a ``COMMENTS: No`` dump."""
    return {
        name: body.rstrip("\n")
        for name, body in cartographer_blocks(read("expected.txt"))
    }


def normalise(text: str) -> str:
    """abcde's dump in mapchar's notation: raw bytes and labels without spaces."""
    return normalise_dump(text).rstrip("\n")


@pytest.fixture
def setup(registry):
    if not os.path.exists(os.path.join(FIXTURES, "expected.txt")):
        pytest.skip("expected.txt missing; run tools/regen_fixtures.py")
    rom = read("rom.bin", binary=True)
    cf = parse_command_file(read("cmd.txt"))
    return rom, cf, load_abcde_tables(FIXTURES, registry)


def _dump(setup, block_name: str) -> list[str]:
    rom, cf, tables = setup
    block = next(b for b in cf.blocks if b.name == block_name)
    start_id = block.table_id or "main"
    ts = TableSet.build(tables[start_id], tables)
    return texts(extract(rom, block.config, ts))


def test_normal_raw_block_matches_when_joined(setup):
    assert "".join(_dump(setup, "Normal")).rstrip("\n") == normalise(
        expected_blocks()["Normal"]
    )


def test_switch_block(setup):
    assert "".join(_dump(setup, "Switch")).rstrip("\n") == normalise(
        expected_blocks()["Switch"]
    )


def test_unrealigned_raw_block(setup):
    assert "".join(_dump(setup, "Realigned")).rstrip("\n") == normalise(
        expected_blocks()["Realigned"]
    )


def test_fixed_string_first_string_matches(setup):
    assert _dump(setup, "Fixed")[0].rstrip("\n") == normalise(
        expected_blocks()["Fixed"]
    )


def test_fixed_lines_match(setup):
    assert _dump(setup, "Lines")[0].rstrip("\n") == normalise(
        expected_blocks()["Lines"]
    )
