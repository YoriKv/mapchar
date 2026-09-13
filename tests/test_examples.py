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

import pytest

from mapchar.core.table import TableSet
from mapchar.pipeline.exchange.cartographer import parse_command_file
from mapchar.pipeline.extract import extract
from mapchar.pipeline.insert import apply_splices, layout_block
from mapchar.plugins.charsets import apply_charset
from mapchar.plugins.registry import default_registry
from mapchar.project.formats.table_legacy import load_table_text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ABCDE = os.path.join(os.path.dirname(ROOT), "abcde")
EXAMPLES = os.path.join(ABCDE, "eg", "NES")
GAMES = {
    "Dragon Quest IV": "Dragon Quest IV - Michibikareshi Monotachi (J) (PRG1) [!].nes",
    "Dragon Warrior II": "Dragon Warrior II (U) [!].nes",
}


def _rom(game: str) -> str | None:
    name = GAMES[game]
    for folder in (
        os.path.join(EXAMPLES, game),
        os.path.join(ROOT, "sample-projects", game),
    ):
        path = os.path.join(folder, name)
        if os.path.exists(path):
            return path
    return None


def _expected_blocks(text: str) -> list[tuple[str, set[str]]]:
    """``(block name, its strings)`` in command-file order."""
    blocks: list[tuple[str, set[str]]] = []
    name = None
    body: list[str] = []

    def flush() -> None:
        if name is not None:
            joined = "\n".join(body)
            strings = {
                s.rstrip("\n")
                for s in re.split(r"(?<=\n)(?=//POINTER|//Block Range)", joined)
                if s.strip()
            }
            blocks.append((name, strings))

    for line in text.split("\n"):
        m = re.match(r"^//BLOCK #\d+ NAME:\t\t(.*)$", line)
        if m:
            flush()
            name, body = m.group(1), []
            continue
        if name is None:
            continue
        if (
            line.startswith("//")
            and not line.startswith("//POINTER")
            and not line.startswith("//Block Range")
        ):
            continue
        if line.startswith("#"):
            continue
        body.append(line)
    flush()
    return blocks


def _normalise(text: str) -> str:
    text = re.sub(r"//POINTER[^\n]*\n|//Block Range[^\n]*\n", "", text)
    text = re.sub(r"<\$([0-9A-Fa-f]{2})>", r"[$\1]", text)
    text = re.sub(
        r"\[([^\]$%][^\]]*)\]",
        lambda m: "[" + re.sub(r"\s+", "_", m.group(1)) + "]",
        text,
    )
    return text.replace("\n", "")


@pytest.mark.parametrize("game", list(GAMES))
def test_example_project(game, tmp_path):
    rom = _rom(game)
    if rom is None:
        pytest.skip(f"{GAMES[game]} not present")
    if shutil.which("perl") is None or not os.path.exists(
        os.path.join(ABCDE, "abcde.pl")
    ):
        pytest.skip("abcde not available")
    folder = os.path.join(EXAMPLES, game)
    for name in os.listdir(folder):
        if name.endswith((".tbl", ".txt")):
            shutil.copy(os.path.join(folder, name), tmp_path / name)
    shutil.copy(rom, tmp_path / "rom.nes")
    result = subprocess.run(
        [
            "perl",
            os.path.join(ABCDE, "abcde.pl"),
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
    registry = default_registry()
    from mapchar.core.context import PipelineContext
    from mapchar.plugins.base import ReadSource, Stage

    ctx = PipelineContext()
    payload = registry.plugin(Stage.CONTAINER, "ines").read(ReadSource(data), ctx)
    header = len(data) - len(payload)
    cf = parse_command_file((tmp_path / "Cartographer.txt").read_text(encoding="utf-8"))
    tables = {}
    for name in sorted(os.listdir(tmp_path)):
        if name.endswith(".tbl"):
            tf = load_table_text(
                (tmp_path / name).read_text(encoding="utf-8"), name, "abcde"
            )
            for t in tf.tables:
                apply_charset(t, registry)
                tables[t.id] = t
    from dataclasses import replace

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
        ex = extract(payload, cfg, ts, registry)
        got = {_normalise(s.original_text()) for s in ex.strings}
        want = {_normalise(s) for s in want_strings}
        assert got == want, block.name
        res = layout_block(payload, cfg, ts, ex.strings, registry)
        assert res.ok, (block.name, res.problems)
        assert apply_splices(payload, res.splices) == payload, block.name


SMW = "Super Mario World"
SMW_ROM = "Super Mario World (USA).sfc"
SAMPLES = os.path.join(ROOT, "tools", "samples")


def test_super_mario_world():
    """The in-repo SMW sample: the message boxes and the level-name parts."""
    rom = os.path.join(ROOT, "sample-projects", SMW, SMW_ROM)
    if not os.path.exists(rom):
        pytest.skip(f"{SMW_ROM} not present")
    folder = os.path.join(SAMPLES, SMW)
    with open(rom, "rb") as f:
        data = f.read()
    registry = default_registry()
    assert registry.detect_container(data, rom).info.id == "snes"  # no header
    with open(os.path.join(folder, "Cartographer.txt"), encoding="utf-8") as f:
        cf = parse_command_file(f.read())
    tables = {}
    for name in sorted(os.listdir(folder)):
        if name.endswith(".tbl"):
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                tf = load_table_text(f.read(), name, "abcde")
            assert not tf.notices, (name, tf.notices)
            for t in tf.tables:
                apply_charset(t, registry)
                tables[t.id] = t
    blocks = {}
    for block in cf.blocks:
        ts = TableSet.build(tables[block.table_file.removesuffix(".tbl")], tables)
        ex = extract(data, block.config, ts, registry)
        assert not ex.notices, (block.name, ex.notices)
        res = layout_block(data, block.config, ts, ex.strings, registry)
        assert res.ok, (block.name, res.problems)
        assert apply_splices(data, res.splices) == data, block.name
        blocks[block.name] = (block.config, ts, ex)

    config, ts, ex = blocks["Message boxes"]
    texts = [s.original_text() for s in ex.strings]
    assert len(texts) == 22
    assert texts[0].startswith("Welcome!   This is[line]\nDinosaur Land.  In[line]\n")
    assert all(t.count("[line]") == 8 for t in texts)
    assert len(ex.strings[1].pointers) == 4  # the four switch palaces
    assert "[$" not in "".join(texts)
    # An edit shorter than the original re-inserts and reads back.
    ex.strings[0].translation = texts[0].replace("Welcome!   ", "Hi!   ", 1)
    res = layout_block(data, config, ts, ex.strings, registry)
    assert res.ok, res.problems
    again = extract(apply_splices(data, res.splices), config, ts, registry)
    assert again.strings[0].original_text().startswith("Hi!   This is[line]\n")
    assert [s.original_text() for s in again.strings[1:]] == texts[1:]

    config, ts, ex = blocks["Level names"]
    texts = [s.original_text() for s in ex.strings]
    assert len(texts) == 57
    assert texts[:2] == ["YOSHI'S [end]", "STAR [end]"]
    assert texts[-1] == " [end]"  # the empty part, named by three tables
    assert len(ex.strings[-1].pointers) == 3
    assert all(t.endswith("[end]") and t.count("[end]") == 1 for t in texts)
    assert "[$" not in "".join(texts)
    ex.strings[1].translation = "SUN [end]"
    res = layout_block(data, config, ts, ex.strings, registry)
    assert res.ok, res.problems
    again = extract(apply_splices(data, res.splices), config, ts, registry)
    assert [s.original_text() for s in again.strings[1:3]] == ["SUN [end]", texts[2]]
