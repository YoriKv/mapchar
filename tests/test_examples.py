"""abcde's example projects, when their ROMs are present.

The ROMs never enter the repository: put each next to its example under
``../abcde/eg/NES/<game>/`` or in ``sample-projects/<game>/``. Without them
these tests skip.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from dataclasses import replace

import pytest

from conftest import ABCDE, ROOT, needs_abcde
from helpers import (
    cartographer_blocks,
    load_abcde_tables,
    normalise_dump,
    relayout,
    texts,
)
from mapchar.core.context import PipelineContext
from mapchar.core.table import TableSet
from mapchar.pipeline.exchange.cartographer import parse_command_file
from mapchar.pipeline.extract import extract
from mapchar.plugins.base import ReadSource, Stage
from mapchar.project.formats.table_legacy import load_table_text

EXAMPLES = ABCDE / "eg" / "NES"
GAMES = {
    "Dragon Quest IV": "Dragon Quest IV - Michibikareshi Monotachi (J) (PRG1) [!].nes",
    "Dragon Warrior II": "Dragon Warrior II (U) [!].nes",
}


def _rom(game: str) -> str | None:
    name = GAMES[game]
    for folder in (EXAMPLES / game, ROOT / "sample-projects" / game):
        path = folder / name
        if path.exists():
            return str(path)
    return None


def _expected_blocks(text: str) -> list[tuple[str, set[str]]]:
    """``(block name, its strings)`` in command-file order."""
    return [
        (
            name,
            {
                s.rstrip("\n")
                for s in re.split(r"(?<=\n)(?=//POINTER|//Block Range)", body)
                if s.strip()
            },
        )
        for name, body in cartographer_blocks(text)
    ]


def _normalise(text: str) -> str:
    """One dumped string as one line, in mapchar's notation."""
    return normalise_dump(text).replace("\n", "")


@needs_abcde
@pytest.mark.parametrize("game", list(GAMES))
def test_example_project(game, tmp_path, registry):
    rom = _rom(game)
    if rom is None:
        pytest.skip(f"{GAMES[game]} not present")
    folder = EXAMPLES / game
    for name in os.listdir(folder):
        if name.endswith((".tbl", ".txt")):
            shutil.copy(folder / name, tmp_path / name)
    shutil.copy(rom, tmp_path / "rom.nes")
    result = subprocess.run(
        [
            "perl",
            str(ABCDE / "abcde.pl"),
            "-cm",
            "abcde::Cartographer",
            "rom.nes",
            "Cartographer.txt",
            "expected",
            "-s",
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    expected = _expected_blocks((tmp_path / "expected.txt").read_text(encoding="utf-8"))
    with open(rom, "rb") as f:
        data = f.read()
    ctx = PipelineContext()
    payload = registry.plugin(Stage.CONTAINER, "ines").read(ReadSource(data), ctx)
    header = len(data) - len(payload)
    cf = parse_command_file((tmp_path / "Cartographer.txt").read_text(encoding="utf-8"))
    tables = load_abcde_tables(tmp_path, registry)

    assert len(expected) == len(cf.blocks)
    for (_, want_strings), block in zip(expected, cf.blocks, strict=True):
        start_id = (
            block.table_id
            or next(
                iter(
                    load_table_text(
                        (tmp_path / block.table_file).read_text(encoding="utf-8"),
                        block.table_file,
                        "abcde",
                    ).tables
                )
            ).id
        )
        ts = TableSet.build(tables[start_id], tables)
        cfg = block.config
        # Cartographer addresses are file offsets; the payload drops the header.
        src = cfg.source
        if hasattr(src, "start"):
            src = replace(src, start=src.start - header, stop=src.stop - header)
        if hasattr(src, "offset") and src.mapping_id == "linear":
            src = replace(src, offset=src.offset - header)
        cfg = replace(
            cfg,
            source=src,
            bound=(cfg.bound - header) if cfg.bound else None,
            skips=tuple((a - header, b - header) for a, b in cfg.skips),
        )
        got = {_normalise(t) for t in texts(extract(payload, cfg, ts, registry))}
        want = {_normalise(s) for s in want_strings}
        assert got == want, block.name
        res, out = relayout(payload, cfg, ts, {}, registry)
        assert res.ok, (block.name, res.problems)
        assert out == payload, block.name


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
