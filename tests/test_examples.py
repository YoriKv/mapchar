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
