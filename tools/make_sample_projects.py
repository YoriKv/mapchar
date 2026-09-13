"""Build the sample projects from abcde's example command files.

For each game with its ROM under ``sample-projects/<game>/``, copies the
example's tables and command file beside it, imports the command file into a
fresh project and saves ``<game>.mapchar`` there. Headless; needs the
offscreen Qt platform, which it sets itself.
"""

from __future__ import annotations

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
EXAMPLES = os.path.join(os.path.dirname(ROOT), "abcde", "eg", "NES")
GAMES = {
    "Dragon Quest IV": "Dragon Quest IV - Michibikareshi Monotachi (J) (PRG1) [!].nes",
    "Dragon Warrior II": "Dragon Warrior II (U) [!].nes",
}


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from mapchar.ui import dialogs
    from mapchar.ui.main_window import MainWindow

    app = QApplication(sys.argv)  # noqa: F841 - Qt needs one alive
    # Headless: notices go to the terminal instead of a modal dialog.
    dialogs.TextDialog.exec = lambda self: print(  # type: ignore[method-assign]
        self.findChild(
            __import__("PySide6.QtWidgets").QtWidgets.QPlainTextEdit
        ).toPlainText()
    )
    made = 0
    for game, rom_name in GAMES.items():
        folder = os.path.join(ROOT, "sample-projects", game)
        rom = os.path.join(folder, rom_name)
        if not os.path.exists(rom):
            print(f"{game}: ROM not present, skipped")
            continue
        source = os.path.join(EXAMPLES, game)
        for name in os.listdir(source):
            if name.endswith(".tbl") or name == "Cartographer.txt":
                shutil.copy(os.path.join(source, name), os.path.join(folder, name))
        window = MainWindow()
        window.open_rom(rom)
        blocks = window.import_cartographer(os.path.join(folder, "Cartographer.txt"))
        total = 0
        for block in blocks:
            doc = window._load_document(block)
            window._activate_entry(block)
            total += len(doc.strings) if doc else 0
        project = os.path.join(folder, f"{game}.mapchar")
        window._write_project(project)
        print(f"{game}: {len(blocks)} block(s), {total} string(s) -> {project}")
        made += 1
    return 0 if made else 1


if __name__ == "__main__":
    raise SystemExit(main())
