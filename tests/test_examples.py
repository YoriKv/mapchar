"""The in-repo sample projects, when their ROMs are present.

The ROMs never enter the repository: put each under
``sample-projects/<game>/``. Without them these tests skip.
"""

from __future__ import annotations

import pytest

from conftest import ROOT
from helpers import (
    load_abcde_tables,
    relayout,
    texts,
)
from mapchar.core.table import TableSet
from mapchar.pipeline.extract import extract
from mapchar.project.exchange.cartographer import parse_command_file

SMW = "Super Mario World"
SMW_ROM = "Super Mario World (USA).sfc"
SAMPLES = ROOT / "tools" / "samples"


def test_super_mario_world(registry):
    """The in-repo SMW sample: the message boxes and the level-name parts."""
    rom = ROOT / "sample-projects" / SMW / SMW_ROM
    if not rom.exists():
        pytest.skip(f"{SMW_ROM} not present")
    folder = SAMPLES / SMW
    data = rom.read_bytes()
    assert registry.detect_container(data, str(rom)).info.id == "snes"  # no header
    cf = parse_command_file((folder / "Cartographer.txt").read_text(encoding="utf-8"))
    tables = load_abcde_tables(folder, registry, assert_clean=True)
    blocks = {}
    for block in cf.blocks:
        ts = TableSet.build(tables[block.table_file.removesuffix(".tbl")], tables)
        ex = extract(data, block.config, ts, registry)
        assert not ex.notices, (block.name, ex.notices)
        res, out = relayout(data, block.config, ts, {}, registry)
        assert res.ok, (block.name, res.problems)
        assert out == data, block.name
        blocks[block.name] = (block.config, ts, ex)

    config, ts, ex = blocks["Message boxes"]
    originals = texts(ex)
    assert len(originals) == 22
    assert originals[0].startswith(
        "Welcome!   This is[line]\nDinosaur Land.  In[line]\n"
    )
    assert all(t.count("[line]") == 8 for t in originals)
    assert len(ex.strings[1].pointers) == 4  # the four switch palaces
    assert "[$" not in "".join(originals)
    # An edit shorter than the original re-inserts and reads back.
    shorter = originals[0].replace("Welcome!   ", "Hi!   ", 1)
    res, out = relayout(data, config, ts, {0: shorter}, registry)
    assert res.ok, res.problems
    again = extract(out, config, ts, registry)
    assert again.strings[0].original_text().startswith("Hi!   This is[line]\n")
    assert texts(again)[1:] == originals[1:]

    config, ts, ex = blocks["Level names"]
    originals = texts(ex)
    assert len(originals) == 57
    assert originals[:2] == ["YOSHI'S [end]", "STAR [end]"]
    assert originals[-1] == " [end]"  # the empty part, named by three tables
    assert len(ex.strings[-1].pointers) == 3
    assert all(t.endswith("[end]") and t.count("[end]") == 1 for t in originals)
    assert "[$" not in "".join(originals)
    res, out = relayout(data, config, ts, {1: "SUN [end]"}, registry)
    assert res.ok, res.problems
    again = extract(out, config, ts, registry)
    assert texts(again)[1:3] == ["SUN [end]", originals[2]]
