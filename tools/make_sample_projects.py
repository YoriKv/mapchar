"""Build the sample projects.

For each game with its ROM under ``sample-projects/<game>/``, copies the
game's tables and command file beside it -- from abcde's examples, or from
``tools/samples/<game>/`` for the games abcde has none for -- imports the
command file into a fresh project and saves ``<game>.mapchar`` there. Mother 3,
Mortal Kombat II and Mortal Kombat have no command file: ``mother3_sample.py``,
``mk2_sample.py`` and ``mk1_sample.py`` derive their tables and blocks from the
ROM, and they are written and added the same way. Names given on the command
line build only those games. Headless; needs the offscreen Qt platform,
which it sets itself. :func:`build` makes one game's project in any folder
holding its ROM, which is how the tests build theirs.
"""

from __future__ import annotations

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import mk1_sample  # noqa: E402 - beside this script, not a package
import mk2_sample  # noqa: E402
import mother3_sample  # noqa: E402

EXAMPLES = os.path.join(os.path.dirname(ROOT), "abcde", "eg", "NES")
SAMPLES = os.path.join(ROOT, "tools", "samples")
GAMES: dict[str, tuple[str, str]] = {
    # game: (ROM file name, folder holding its tables and Cartographer.txt)
    "Dragon Quest IV": (
        "Dragon Quest IV - Michibikareshi Monotachi (J) (PRG1) [!].nes",
        os.path.join(EXAMPLES, "Dragon Quest IV"),
    ),
    "Dragon Warrior II": (
        "Dragon Warrior II (U) [!].nes",
        os.path.join(EXAMPLES, "Dragon Warrior II"),
    ),
    "Super Mario World": (
        "Super Mario World (USA).sfc",
        os.path.join(SAMPLES, "Super Mario World"),
    ),
}
DERIVED = {"Mother 3": mother3_sample, "MK2": mk2_sample, "mk1": mk1_sample}
"""Games whose tables and blocks a module derives from the ROM, by folder."""


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from mapchar.ui import dialogs
    from mapchar.ui.main_window import MainWindow

    wanted = set(sys.argv[1:])
    app = QApplication(sys.argv[:1])  # noqa: F841 - Qt needs one alive
    # Headless: notices go to the terminal instead of a modal dialog.
    dialogs.TextDialog.exec = lambda self: print(  # type: ignore[method-assign]
        self.findChild(
            __import__("PySide6.QtWidgets").QtWidgets.QPlainTextEdit
        ).toPlainText()
    )
    made = 0
    for game in [*GAMES, *DERIVED]:
        if wanted and game not in wanted:
            continue
        folder = os.path.join(ROOT, "sample-projects", game)
        if os.path.exists(os.path.join(folder, rom_name(game))):
            build(MainWindow(), game, folder)
            made += 1
        else:
            print(f"{game}: ROM not present, skipped")
    return 0 if made else 1


def rom_name(game: str) -> str:
    return GAMES[game][0] if game in GAMES else DERIVED[game].ROM_NAME


def build(window, game: str, folder: str) -> str:
    """Writes ``game``'s tables beside its ROM in ``folder``, opens them and
    its blocks in ``window``, and saves the project there; its path."""
    rom = os.path.join(folder, rom_name(game))
    if game in DERIVED:
        blocks, notes = _derived(window, rom, DERIVED[game])
        return _save(window, game, folder, blocks, notes)
    source = GAMES[game][1]
    for name in os.listdir(source):
        if name.endswith(".tbl") or name == "Cartographer.txt":
            shutil.copy(os.path.join(source, name), os.path.join(folder, name))
    window.open_rom(rom)
    blocks = window.import_cartographer(os.path.join(folder, "Cartographer.txt"))
    return _save(window, game, folder, blocks)


def _derived(window, rom: str, module) -> tuple[list, list]:
    """Opens ``rom`` in ``window`` with the tables ``module`` derives, written
    beside the ROM, and its blocks, in their folders; the blocks, and the notes
    ``module`` gives each block's strings (``{}`` for none)."""
    from mapchar.core.capabilities import EntryKind
    from mapchar.project.entry import Entry
    from mapchar.project.formats.blockspec import parse_config

    with open(rom, "rb") as f:
        data = f.read()
    file_entry = window.open_rom(rom)
    for name, text in module.table_files(data).items():
        path = os.path.join(os.path.dirname(rom), name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        window.open_table(path, "native")
    blocks, notes = [], []
    folders: dict[str, Entry] = {}
    for block in module.blocks(data):
        folder = folders.get(block.folder) if block.folder else None
        if block.folder and folder is None:
            folder = Entry(
                EntryKind.FOLDER, block.folder, file_entry.path, parent=file_entry
            )
            window._push_add(folder)
            folders[block.folder] = folder
        entry = Entry(
            EntryKind.BLOCK,
            block.name,
            file_entry.path,
            parent=file_entry,
            folder=folder,
            config=parse_config(block.spec),
        )
        if block.compression:
            entry.compression_id, entry.slot_offset, entry.slot_length = (
                block.compression
            )
        window._push_add(entry)
        blocks.append(entry)
        notes.append(getattr(block, "notes", None) or {})
    return blocks, notes


def _save(window, game: str, folder: str, blocks: list, notes=None) -> str:
    """Read every block, put ``notes`` on its strings -- ``{index: text}`` a
    block, ``None`` for what every string starts with -- then save the window's
    session as ``<game>.mapchar``; its path."""
    total = 0
    for block, block_notes in zip(blocks, notes or [{}] * len(blocks), strict=True):
        window._activate_entry(block)  # extracts the block's strings
        doc = window._load_document(block)
        total += len(doc.strings) if doc else 0
        for i, record in enumerate(doc.strings if doc else ()):
            parts = (block_notes.get(None), block_notes.get(i))
            record.notes = " ".join(p for p in parts if p)
    project = os.path.join(folder, f"{game}.mapchar")
    window._write_project(project)
    print(f"{game}: {len(blocks)} block(s), {total} string(s) -> {project}")
    return project


if __name__ == "__main__":
    raise SystemExit(main())
