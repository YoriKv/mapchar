"""The in-repo sample projects, when their ROMs are present.

The ROMs never enter the repository: put each under
``sample-projects/<game>/``. Without them these tests skip.
"""

from __future__ import annotations

import importlib.util
import sys

import pytest

from conftest import ROOT
from helpers import (
    load_abcde_tables,
    relayout,
    texts,
)
from mapchar.core.block import WriteMode
from mapchar.core.table import TableSet
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import layout_block
from mapchar.project.exchange.cartographer import parse_command_file
from mapchar.project.formats.script import parse_config
from mapchar.project.formats.table_native import parse_native

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


def _mother3_sample():
    """``tools/mother3_sample.py``, which lives beside the tools, not in a package."""
    path = ROOT / "tools" / "mother3_sample.py"
    spec = importlib.util.spec_from_file_location("mother3_sample", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # its dataclass resolves annotations there
    spec.loader.exec_module(module)
    return module


def test_mother_3(registry):
    """The Mother 3 sample, derived from the ROM: every block reads cleanly and
    lays out again, and edits through all three tables read back."""
    m3 = _mother3_sample()
    rom = ROOT / "sample-projects" / "Mother 3" / m3.ROM_NAME
    if not rom.exists():
        pytest.skip(f"{m3.ROM_NAME} not present")
    data = rom.read_bytes()
    assert registry.detect_container(data, str(rom)).info.id == "gba"
    tables = {}
    for name, text in m3.table_files(data).items():
        table = parse_native(text, name).table
        tables[table.id] = table
    blocks = {}
    for block in m3.blocks(data):
        config = parse_config(block.spec)
        ts = TableSet.build(tables[config.table_id], tables)
        ex = extract(data, config, ts, registry)
        assert not ex.notices, (block.name, ex.notices)
        assert not [n for s in ex.strings for n in s.notices], block.name
        res = layout_block(data, config, ts, ex.strings, registry)
        assert res.ok, (block.name, res.problems)
        blocks[block.name] = (config, ts, ex)
    assert len(blocks) == 582
    assert sum(len(ex.strings) for _, _, ex in blocks.values()) == 12997
    unmatched = [
        (name, s.index)
        for name, (_, _, ex) in blocks.items()
        for s in ex.strings
        if "[$" in s.original_text()
    ]
    # Three unused enemy slots hold values past the end of the font.
    assert unmatched == [
        ("Enemy names", 265),
        ("Enemy names", 288),
        ("Enemy names", 292),
    ]

    _, _, ex = blocks["Menu text"]
    assert [t for t in texts(ex)[:4]] == [
        " [end]",
        "？？？？？？？？？？[end]",
        "－－－－－－[end]",
        "はい[end]",
    ]
    _, _, ex = blocks["Item names"]
    assert texts(ex)[1:3] == [
        "ライタのカクザイ[end]",
        "あたらしいカクザイ",
    ]  # 9 wide, no end

    # Mr. Saturn's font, its dakuten a mark of their own.
    config, ts, ex = blocks["Script 0900"]
    assert texts(ex)[4].startswith("[saturn]◆どせいさん おんせん[FF03][FF00]\n")
    edits = {4: "[saturn]◆ぱぴぷ[line]です[end]", 0: "◇が[FF04 $0010]ぎ[end]"}
    res, out = relayout(data, config, ts, edits, registry)
    assert res.ok, res.problems
    again = extract(out, config, ts, registry)
    assert [texts(again)[i] for i in (0, 4)] == [
        "◇が[FF04 $0010]ぎ[end]",
        "[saturn]◆ぱぴぷ[line]\nです[end]",
    ]
    s = again.strings[4]
    assert out[s.start : s.start + 10].hex() == "0bff3e001a003c001b00"

    # Battle messages: FF20 and FF21 take no operand there.
    config, ts, ex = blocks["Battle text"]
    assert texts(ex)[300] == "[FF11]は[line]\n[FF10]をこころみた！[end]"
    res, out = relayout(data, config, ts, {300: "[FF21]は[FF20]！[end]"}, registry)
    assert res.ok, res.problems
    assert texts(extract(out, config, ts, registry))[300] == "[FF21]は[FF20]！[end]"

    # One string is the last page of another: slotted, and both still edit.
    config, ts, ex = blocks["Script 0101"]
    assert config.write_mode is WriteMode.SLOTTED
    res, out = relayout(data, config, ts, {1: "◇ゴミ。[end]"}, registry)
    assert res.ok, res.problems
    assert texts(extract(out, config, ts, registry))[1] == "◇ゴミ。[end]"
