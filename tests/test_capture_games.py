"""Capture on real games against their known answers: the text of
``docs/text-capture.md``'s experiments, traced by Mesen 2 with no setting
specific to any game.

Opt-in, since it runs for about half an hour: set ``MAPCHAR_CAPTURE_GAMES=1``.
It needs Mesen 2 on ``PATH`` (or ``MAPCHAR_MESEN``), the ROMs — under
``sample-projects/<game>/`` or a folder ``MAPCHAR_ROMS`` lists — and the
recorded moments, game data kept outside the repository: ``MAPCHAR_CAPTURES``
names their folder
(``<game>/capNNNNN.txt``, ``_sNN.mss``, ``_input.txt``, ``.png``), by default
``tmp/capture-spike/cap``. A game with any of them missing skips.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

import pytest

from conftest import ROOT
from mapchar.capture.consoles import detect
from mapchar.capture.emulator import Mesen
from mapchar.capture.session import DONE, FAILED, Session
from mapchar.plugins.base import Stage
from mapchar.plugins.registry import default_registry, resolve_mapping

if not os.environ.get("MAPCHAR_CAPTURE_GAMES"):
    pytest.skip("set MAPCHAR_CAPTURE_GAMES=1 to run", allow_module_level=True)

CONFIG_HOME = os.environ.get("XDG_CONFIG_HOME")
"""Mesen keeps its own files in the config home, which the suite points at a
temporary folder: there it would start as a first run, with a window."""


@pytest.fixture(autouse=True)
def emulator_home(monkeypatch):
    if CONFIG_HOME is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", CONFIG_HOME)


CAPTURES = Path(
    os.environ.get("MAPCHAR_CAPTURES", ROOT / "tmp" / "capture-spike" / "cap")
)
ROMS = {
    "smw": "Super Mario World (USA).sfc",
    "alttp": "Legend of Zelda, The - A Link to the Past (USA).sfc",
    "yi": "Super Mario World 2 - Yoshi's Island (USA).sfc",
    "eb": "EarthBound (USA).sfc",
    "dw2": "Dragon Warrior II (U) [!].nes",
    "m3": "Mother 3 (Japan).gba",
}


def rom_for(game: str) -> Path | None:
    """The ROM, under ``sample-projects/<game>/`` or in one of the folders
    ``MAPCHAR_ROMS`` lists."""
    name = ROMS[game]
    places = list((ROOT / "sample-projects").glob("*"))
    places += [
        Path(p) for p in os.environ.get("MAPCHAR_ROMS", "").split(os.pathsep) if p
    ]
    return next((p / name for p in places if (p / name).is_file()), None)


def session_for(game: str, frames: list[int], texts: list[str], tmp_path) -> Session:
    rom = rom_for(game)
    if rom is None:
        pytest.skip(f"{ROMS[game]} not present")
    emu = Mesen(os.environ.get("MAPCHAR_MESEN"))
    if not emu.available():
        pytest.skip("Mesen 2 not found")
    data = rom.read_bytes()
    s = Session(str(tmp_path / "captures"), str(rom), detect(str(rom), data), emu)
    for frame, text in zip(frames, texts, strict=True):
        src = CAPTURES / game / f"cap{frame:05d}"
        if not Path(f"{src}.txt").exists():
            pytest.skip(f"no recorded moment {src}")
        prefix = os.path.join(s.root, "incoming", f"{game}{frame}")
        for suffix in (".txt", "_input.txt", ".png"):
            if Path(f"{src}{suffix}").exists():
                shutil.copy(f"{src}{suffix}", prefix + suffix)
        for state in sorted((CAPTURES / game).glob(f"cap{frame:05d}_s*.mss")):
            shutil.copy(state, prefix + state.name[len(f"cap{frame:05d}") :])
        cap = s.add_moment(f"{game}{frame}", prefix)
        assert s.submit(cap, text) is None
    end = time.monotonic() + 3600
    while s.advance(0.5):
        assert time.monotonic() < end
    for cap in s.captures:
        assert cap.state == DONE, (cap.id, cap.reason)
    return s


def combined(s: Session):
    reg = default_registry()
    return s.combined({m: resolve_mapping(reg, m) for m in reg.ids(Stage.MAPPING)})


def text_of(c, code: int, width: int = 1) -> str | None:
    m = c.meanings.get(f"{code:0{2 * width}X}")
    return m.text if m is not None else None


def test_super_mario_world(tmp_path):
    s = session_for(
        "smw",
        [620],
        [
            "Welcome! This is Dinosaur Land. In this strange land we find that "
            "Princess Toadstool is missing again! Looks like Bowser is at it again!"
        ],
        tmp_path,
    )
    r = s.captures[0].result
    assert len(r.sources) == 140 and r.string[0] == 0x2A5D9
    moves = {p.address: p.moves for p in r.pointers}
    assert moves[0x2A5AF] == 2 and moves[0x2A5B0] == 512
    c = combined(s)
    assert text_of(c, 0x00) == "A" and text_of(c, 0x1F) == " "
    assert text_of(c, 0x80) == "A\\n"  # bit 7 ends a line
    (e,) = c.engines
    assert e.table is not None and 0x2A5AF in e.table.slots


def test_a_link_to_the_past(tmp_path):
    s = session_for(
        "alttp",
        [2100],
        [
            "Long ago, in the beautiful kingdom of Hyrule surrounded by mountains and "
            "forests..."
        ],
        tmp_path,
    )
    r = s.captures[0].result
    assert len(r.sources) == 80 and not r.packed
    c = combined(s)
    assert text_of(c, 0x00) == "A" and text_of(c, 0x1A) == "a"
    assert text_of(c, 0xD8) == "the" and text_of(c, 0x8F) == "ain"  # the dictionary


def test_earthbound(tmp_path):
    s = session_for("eb", [1050], ["Please name him."], tmp_path)
    r = s.captures[0].result
    assert r.string[0] == 0x4C194
    assert any(p.address == 0x1F94A for p in r.pointers)  # LDA #$C194 in code
    c = combined(s)
    assert (
        text_of(c, 0x71) == "A" and text_of(c, 0x91) == "a" and text_of(c, 0x50) == " "
    )


def test_dragon_warrior_ii(tmp_path):
    s = session_for(
        "dw2",
        [3000, 4050],
        [
            "There one day the King and his daughter were talking in a courtyard of "
            "the castle when the long years of peace ended suddenly.",
            "besieged by the forces of Hargon, the wizard. Hargon is here? asked "
            "the King.",
        ],
        tmp_path,
    )
    assert all(cap.result.packed for cap in s.captures)
    assert s.captures[0].result.stream == (0x14BF6, 0x14C3B)
    c = combined(s)
    (e,) = c.engines
    assert e.layout is not None and e.layout[:3] == ("msb", 5, 4)
    assert c.meanings["1110001010"].text == "King"
    assert c.meanings["00101"].text == "y"


def test_yoshis_island(tmp_path):
    s = session_for(
        "yi", [1200], ["This is a story about baby Mario and Yoshi."], tmp_path
    )
    r = s.captures[0].result
    assert r.output == "vram" and r.string[0] == 0x7CFA0
    kinds = {c.value: c.kind for c in r.codes}
    assert kinds[0xFF] == "end" and {kinds[v] for v in (0xFC, 0xFD, 0xFE)} == {
        "command"
    }
    c = combined(s)
    assert text_of(c, 0xBD) == "T" and c.engines[0].end_code == 0xFF


def test_mother_3(tmp_path):
    s = session_for("m3", [2700], ["つよくて やさしい たよれる おとうさんだ"], tmp_path)
    r = s.captures[0].result
    assert r.output == "vram" and r.unit == 2 and r.string[0] == 0x1BC330A
    assert {p.address for p in r.pointers} >= {0x1BC25B8}
    c = combined(s)
    assert text_of(c, 0x013D, 2) == "つ"
    assert FAILED not in {cap.state for cap in s.captures}
