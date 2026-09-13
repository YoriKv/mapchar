"""Compare mapchar's extraction with abcde's Cartographer dump of the fixtures."""

from __future__ import annotations

import os
import re

import pytest

from mapchar.core.table import TableSet
from mapchar.pipeline.exchange.cartographer import parse_command_file
from mapchar.pipeline.extract import extract
from mapchar.plugins.registry import default_registry
from mapchar.project.formats.table_legacy import load_table_text

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
    blocks: dict[str, str] = {}
    name = None
    body: list[str] = []
    for line in read("expected.txt").split("\n"):
        m = re.match(r"^//BLOCK #\d+ NAME:\t\t(.*)$", line)
        if m:
            if name is not None:
                blocks[name] = "\n".join(body).rstrip("\n")
            name, body = m.group(1), []
            continue
        if line.startswith("//") or name is None:
            continue
        body.append(line)
    if name is not None:
        blocks[name] = "\n".join(body).rstrip("\n")
    return blocks


def normalise(text: str) -> str:
    """abcde's dump in mapchar's notation: raw bytes and labels without spaces."""
    text = re.sub(r"<\$([0-9A-Fa-f]{2})>", r"[$\1]", text)
    text = re.sub(
        r"\[([^\]$%][^\]]*)\]",
        lambda m: "[" + re.sub(r"\s+", "_", m.group(1)) + "]",
        text,
    )
    return text.rstrip("\n")


@pytest.fixture(scope="module")
def setup():
    if not os.path.exists(os.path.join(FIXTURES, "expected.txt")):
        pytest.skip("expected.txt missing; run tools/regen_fixtures.py")
    rom = read("rom.bin", binary=True)
    cf = parse_command_file(read("cmd.txt"))
    tables = {}
    registry = default_registry()
    for name in ("main.tbl", "items.tbl"):
        for t in load_table_text(read(name), name, "abcde").tables:
            tables[t.id] = t
    return rom, cf, tables, registry


def _dump(setup, block_name: str) -> list[str]:
    rom, cf, tables, _ = setup
    block = next(b for b in cf.blocks if b.name == block_name)
    start_id = block.table_id or "main"
    ts = TableSet.build(tables[start_id], tables)
    ex = extract(rom, block.config, ts)
    return [s.original_text() for s in ex.strings]


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
